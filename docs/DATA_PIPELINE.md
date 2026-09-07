# Data Pipeline

`raw → profiled → validated → prepared → features → database`

Every stage is a script that can be re-run, and every stage writes a report. Nothing about
a dataset is assumed; the profiler discovers what is actually in the file and the
preprocessing pipeline is driven by that.

---

## Principles

1. **Profile before preprocessing.** Column names, curve coverage, class balance and depth
   sampling are *measured*, then written to `artifacts/profiling/`.
2. **Never delete silently.** Implausible values are flagged, counted and reported. Rows
   survive; only the impossible reading is nulled, and it is marked.
3. **Zero is a value, not a null.** In drilling telemetry, zero flow means the pumps are
   off. Zeros are never treated as missing.
4. **Constant channels are reported.** A zero-variance channel contributes nothing and is
   excluded automatically rather than padding a feature count.
5. **Provenance travels with the record.** Source dataset, source reference and — for
   extracted knowledge — page, original text and confidence.

---

## FORCE 2020 (geology)

```bash
python -m data_pipeline.force.profile
python -m data_pipeline.force.prepare
```

### Profiling output

| Measured | Value |
|---|---|
| Rows | 1,170,511 |
| Wells | 98 |
| Classes | 12 lithofacies |
| Depth sampling | 0.152 m |
| Duplicate (well, depth) rows | **0** |
| Wells with non-monotonic depth | **0** |
| Curves at ≥ 30% coverage | 19 |
| Class imbalance | **6,998:1** (Shale 61.58% → Basement 0.01%) |

Curve coverage varies sharply, and this drives selection:

| Curve | Coverage | Wells present |
|---|---:|---:|
| GR | 100.0% | 98/98 |
| RDEP | 99.1% | 98/98 |
| RMED | 96.7% | 97/98 |
| DTC | 93.1% | 98/98 |
| RHOB | 86.2% | 98/98 |
| NPHI | 65.4% | 98/98 |
| PEF | 57.4% | 69/98 |
| ROP | 45.7% | 53/98 |
| SGR | 5.9% | 13/98 |

### Preparation

15 base curves → **90 features**: rolling mean and standard deviation over 5 m and 15 m
depth windows, plus first derivative with respect to depth, all computed per well.

Windows are configured in **metres** and converted to sample counts using the measured
sampling interval, so the configuration stays correct if log density changes.

### Well-level splitting

Rows of a well never cross splits, so test metrics answer "how does this behave on a well it
has never seen".

| Split | Wells | Rows |
|---|---:|---:|
| Train | 68 | 769,983 |
| Validation | 15 | 217,144 |
| Test | 15 | 183,384 |

Rare classes occur in very few wells, so classes present in ≤ 15 wells are actively spread
across splits before common wells are dealt out. Without this, five rare lithologies landed
entirely in train and their validation metrics were meaningless.

Only **Basement** remains unmeasurable: it exists in exactly 1 of 98 wells, so it
structurally cannot appear in both training and evaluation. The split manifest records this
in `classes_absent_from_split`.

### Coordinates

`X_LOC/Y_LOC` are projected metres (ED50 / UTM 31N, `EPSG:23031`), not degrees. They are
transformed to WGS84 with `pyproj` on ingestion. Rows without coordinates are kept for ML
and excluded from mapping.

---

## Volve WITSML (drilling operations)

```bash
python -m data_pipeline.volve.profile
python -m data_pipeline.volve.prepare
```

312 XML files across 3 wellbores → **86,800 rows at 10 s cadence, 20 channels**.

### Parsing

The parser handles the object types actually present: `log`, `trajectory`, `message`,
`bhaRun`, `wbGeometry`, `_wellInfo`, `_wellboreInfo`. Channels are discovered from
`logCurveInfo`; the canonical channel map in configuration resolves against what each log
contains, so a missing mnemonic yields a null column rather than a crash.

Only drilling log objects are ingested (`GenTime`, `GenTime2`, `GwdTime`, `Hydraulics`);
`CementData`, `GasTime` and `Pits` are separate operations.

### Unit normalisation

WITSML publishes strict SI, which is unreadable on a drilling dashboard:

| WITSML | Canonical | Factor |
|---|---|---|
| N | kN | ×10⁻³ |
| N·m | kN·m | ×10⁻³ |
| Pa | bar | ×10⁻⁵ |
| m³/s | L/min | ×60,000 |
| c/s | rpm | ×60 |
| K | °C | −273.15 |
| kg/m³ | s.g. | ×10⁻³ |
| m/s | m/h | ×3600 |

An unrecognised unit passes through untouched and is reported — never converted by
guesswork.

### Rig state

Activity is derived from signals that genuinely vary:

- `circulating` — flow in above threshold
- `rotating` — bit RPM above threshold
- `tripping` — rate of change of bit depth above threshold
- `operations_active` — any of the above (**8,130 of 86,800 rows**)

An on-bottom test is deliberately *not* derived: `hole_depth_m` is a constant in this mirror
(see `ASSUMPTIONS.md` A10), so any such flag would be an artefact.

Resampling happens **before** activity derivation. Overlapping log objects repeat
timestamps, and a derivative across a duplicated timestamp produced trip speeds of
994 m/min — physically impossible. Binning to a regular cadence merges duplicates first.

### Event depth recovery

WITSML `message` objects carry wellbore total depth on every record, not the depth of the
remark. Depths are recovered by matching the message timestamp to the nearest telemetry
sample within 10 minutes. **All 184 messages resolved**, giving F-4 a real range of
0–3510 m instead of a single constant.

Both the reported and recovered values are retained alongside a `depth_source` column, so
the substitution is auditable rather than invisible.

---

## Documents (institutional memory)

```bash
python -m data_pipeline.documents.ingest --list
python -m data_pipeline.documents.ingest --well 15/9-13 --max-pages 30
python -m data_pipeline.documents.ingest --start-page 30 --max-pages 202   # go deeper
python -m data_pipeline.documents.embed                                    # index for search
```

The Sodir wellbore document index lists **388 licensee reports covering 75 of our wells**.
These are genuine completion reports — and they are scans: the 15/9-13 report is 197 pages
with **zero text layer**.

```
PDF page ─┬─ has a text layer?  → use it (exact, free)
          └─ no                 → rasterise at configured DPI → OCR
                                    ↓
                          clean → sentence segmentation
                                    ↓
                    pattern extraction + domain lexicon
                                    ↓
        formation intervals · measurements · problem/action/outcome events
                                    ↓
                database, with page, source text and confidence
```

Extraction is explicit, auditable patterns plus a reviewable domain lexicon, **not** a
general NER model asked to understand petroleum terminology. spaCy supplies sentence
segmentation; anything it contributes is labelled as such.

Confidence is conservative and compounds: OCR page confidence × pattern reliability, raised
only when a remedial action or outcome is actually found in the surrounding sentences.
Records below `documents.nlp.min_confidence` are counted in the ingestion report but not
written to the database.

### Reading deeper into a report

The first ~30 pages of these reports are geological sample descriptions. The
drilling-operations narrative — where problem, action and outcome chains actually live —
is further in; the reports' own tables of contents list a "DRILLING REPORT" section well
past that point.

`--start-page` reads from a page offset. Passing one beyond the range already stored
**extends** the existing document rather than skipping it or creating a duplicate: chunk
numbering continues from the highest stored index, pages at or below the deepest stored
page are filtered out, and extracted events are deduplicated by document, event type and
source text. So a report can be ingested in passes as deeper pages become worth the OCR
cost.

OCR page text is cached under `documents.cache_dir`, keyed by document, start page and
page count. Re-running extraction to improve the NLP rules costs nothing; only a page
range never read before pays for OCR.

### Semantic search over passages

Every chunk is stored with `embedding = NULL` at ingestion and filled in by a separate
stage, because OCR is expensive and rarely repeated while the choice of encoder may be
revisited without re-reading a PDF.

`data_pipeline/documents/embed.py` encodes passages with a pretrained sentence-transformer
(`documents.embedding.model`) and stores L2-normalised vectors, which makes cosine
similarity a dot product and lets the pgvector cosine index and the NumPy fallback agree
exactly.

These vectors are **384-dimensional and live in a different space** from the
32-dimensional petrophysical segment embeddings used by the analogue engine. Different
model, different meaning; the two constants are separate and the column type rejects a
vector of the wrong width, so the two can never be silently compared. If the configured
encoder produces a width that disagrees with the schema, loading fails before anything is
encoded rather than writing vectors that would rank nonsense confidently.

Search is served by `/api/documents/search` and returns passages with page citations,
never a generated answer. See `docs/API.md`.

---

## Loading

```bash
python -m data_pipeline.load_database --reset
python -m data_pipeline.load_embeddings
```

| Loaded | Count |
|---|---:|
| Wells | 101 |
| Formation intervals | 1,449 |
| Log samples (decimated to 5 m) | 18,842 |
| Telemetry samples | 86,800 |
| Trajectory stations | 154 |
| Drilling events | 184 |
| Segment embeddings | 3,620 |
| Wells **without** a real position | **0** |

Full-resolution logs stay in `data/processed/` for training; the database holds a decimated
copy, because 0.152 m spacing is far finer than any display or depth lookup needs.

### The coordinate cross-check

Volve positions come from NPD factpages; FORCE positions come from the dataset's own
`X_LOC/Y_LOC`. Transformed independently, Volve resolves to 58.441 °N 1.886 °E and the
nearest FORCE well is 3.6 km away **in the same licence block, 15/9**. A third confirmation
came from OCR: the 15/9-13 completion report states 58°22′25.96″N = 58.3739 °N, against
58.37327 °N in the database.

Three independent sources agreeing is what makes the CRS assumption trustworthy rather than
merely plausible.

---

## Data quality checks

| Check | Where | Behaviour |
|---|---|---|
| Null sentinels (−999.25 etc.) | both | replaced with null, counted per column |
| Duplicate (well, depth) | FORCE | counted; 0 found |
| Depth monotonicity | FORCE | non-monotonic wells listed; 0 found |
| Curve coverage | FORCE | below threshold → excluded, and reported |
| Implausible ranges | Volve | flagged, nulled, row retained, marked `*_implausible` |
| Constant channels | Volve | detected and excluded from features |
| Coordinate bounds | loading | out-of-range rejected; well stays unmapped |
| Unknown units | Volve | passed through untouched and reported |
