# NWIS — Team Status

Written after inspecting the repository, the running application and the live database.
Numbers here were read from the system, not copied from a slide.

Repo: `D:\SOHAM ALL\hackathons\SIH` · 31 commits on `main` ·
github.com/Physics0070/nwis

---

## Project purpose

Historical drilling knowledge is scattered across completion reports, WITSML feeds, well
logs, trajectories and operational remarks. NWIS turns that into searchable intelligence
for a well being drilled now: nearby wells, *analogue* wells, comparable historical
events, what was actually done about them, and the source document behind each claim.

It is decision **support**. The engineer stays in control. Nothing here steers a rig.

The organising principle, and the reason several things below look unfinished rather than
polished: **no number is displayed unless a measurement produced it.** Where a value is
unknown the interface says so instead of showing a zero.

---

## Current architecture

```
FORCE 2020 ─┐
Volve WITSML ┼─► data_pipeline ─► 16-table DB ─► ML artifacts ─► services ─► FastAPI ─► React
Sodir docs ─┘    force/volve/       Postgres      lithology       analogue    30 eps    8 pages
                 documents/         +PostGIS      anomaly         risk        + ws
                                    +Timescale    similarity      replay
                                    +pgvector     text embed      doc search
```

- **Backend** FastAPI, **30 endpoints** + `/ws/telemetry/{well_id}`.
- **Storage** PostgreSQL + PostGIS + TimescaleDB + pgvector, with a documented local
  fallback (SQLite + SQL haversine + NumPy cosine). Which one is live is reported by
  `/api/status` and shown in the UI header badge. This is deliberate, not a fudge.
- **Frontend** React + TS + Tailwind. Every value routes through `src/lib/api.ts`; there
  are no fixture objects anywhere in the UI.
- **Config** `config/default.yaml` holds every threshold, weight, window and K value.

---

## Current features

| Area | State |
|---|---|
| Well knowledge base + map | working, 101 wells, all with real surveyed positions |
| Nearby wells | server-side PostGIS or SQL haversine; never computed in the browser |
| Analogue engine | 5 weighted dimensions, unavailable ones renormalised and reported |
| Telemetry replay | server-owned state, start/pause/resume/stop/speed/seek |
| Live streaming | WebSocket, reconnect-safe |
| Anomaly | Isolation Forest, 8,130 scores persisted |
| Risk | hybrid indicator with full explanation payload |
| Historical events + mitigations | with provenance and citations |
| Document search | semantic, returns cited passages, never generated prose |
| Engineer feedback | persisted for future retraining; nothing retrains automatically |
| **Simulator** | **built and verified end to end** (see below) |

---

## Current data (read from the DB)

| | |
|---|---|
| Wells | 101 (98 FORCE + 3 Volve), **0 without a real surveyed position** |
| Telemetry | 86,800 samples, 20 normalised channels |
| Drilling events | 243 — 184 WITSML remarks, 59 categorised from report text |
| Categorised events with a recovered depth | 10 — only these can serve as depth-matched risk evidence |
| Mitigations | 15, each citing a document and page |
| Documents | 8 reports, **320 pages OCR'd** |
| Passages | 410, all embedded (384-d MiniLM) |

These counts are from the 2026-09-08 rebuild recorded in `docs/FINAL_QA_REPORT.md`. Read
them from `/api/status` and the database before quoting them; they change every time more
reports are ingested.
| Formation intervals | 1,482 |
| Lithology predictions | 18,842 |
| Anomaly scores | 8,130 |
| Segment embeddings | 3,620 × 32 interpretable dimensions |

**388 reports exist in the Sodir index; 2 are ingested.** The other 386 are *available to
download*, not available to the application. Do not describe them as ingested.

Note the two embedding spaces are different and must never be compared: segment
embeddings are 32-d petrophysical statistics; passage embeddings are 384-d MiniLM. The
column types enforce this.

---

## Current models

**Lithology** — RandomForest served, XGBoost registered. Selection was pre-committed to
validation wells only. Grouped 5-fold CV over 83 wells then measured:

| | mean macro F1 | std |
|---|---|---|
| XGBoost | 0.3840 | 0.0543 |
| RandomForest | 0.3667 | 0.0462 |

p = 0.224, and the **fold spread is 3.1× the gap between the models**. The honest reading
is that the two are *not separable* on this data — the earlier validation-vs-holdout
disagreement was variation between draws of wells, not a real difference. No winner is
claimed. FORCE holdout for XGBoost: accuracy 0.7498, macro F1 0.3852, against a
majority-class baseline of 0.6139 / 0.0761.

**Anomaly** — Isolation Forest, 121 features.

**Risk** — `hybrid_indicator`, `probability: null`. There are no labelled drilling
incidents in either dataset; this was checked, not assumed. A supervised probability here
would be fabricated. The mode is chosen by the data and flips to supervised on its own if
labels ever exist.

---

## Current test status

```
backend    68 passed, 12 skipped     (skips = Postgres container up but empty)
frontend   20 passed
typecheck  clean
build      clean
```

87 API requests swept across three sweeps (main endpoints, malformed input, replay state
machine): **0 server errors**. Chromium: 0 console errors.

---

## Current limitations — state these, do not hide them

1. Risk is a hybrid indicator, not a probability.
2. No labelled incidents exist, so supervised risk is not possible today.
3. Chalk is predicted as Limestone ~99.8% of the time — carbonates are not separable on
   the available curves. Recorded in the model card.
4. Norwegian North Sea data only. **Not validated on Indian or OIL wells.** The pipeline
   is basin-agnostic; the lithology model is not.
5. No authentication, TLS or rate limiting. Prototype.
6. 2 of 388 reports ingested.
7. Most mitigations carry no parsed `outcome` — the outcome vocabulary does not match
   this corpus. The PROBLEM → ACTION → SOURCE chain is real; OUTCOME is usually missing.

---

## Current blockers

**1 — Docker loader fails on an undeclared dependency.** Live and specific:

```
ImportError: Unable to find a usable engine; tried using: 'pyarrow', 'fastparquet'.
  data_pipeline/load_database.py:137  ingest_force → pandas.read_parquet
```

`pandas` needs a parquet engine and none is declared. This is the third instance of the
same class of bug — `pyproj` and `rapidocr_onnxruntime` were the first two — all found
only because the container has a clean environment while the dev machine has extras
installed. **The fix is one line in `backend/requirements.txt`.**

Everything upstream of it now works: the database container comes up healthy reporting
`postgis: true, timescaledb: true, pgvector: true, fallback_active: false`, and the schema
creates cleanly.

**Resolved earlier today, for context:** GeoAlchemy2's `before_create`/`after_create` pair
desynchronised against our `TypeDecorator` and aborted `create_all` on Postgres with
`KeyError: '_saved_columns'`. GeoAlchemy2 was removed entirely — nothing else used it.

---

## Simulator plan

**Already built and verified**, at `/simulator`. It replays real recorded telemetry and
presents it as a live session. No new backend endpoint was needed; it drives the existing
replay, risk, analogue, events, formations and engineer-action APIs.

Verified end to end in Chromium against the live API: well selection → start → 728 samples
replayed → telemetry updating → risk LOW 0.425 `hybrid_indicator` → historical evidence →
Investigate → mitigation + WITSML citation → engineer action **persisted to the database**
→ pause/resume/stop.

Layout is one screen: top bar, borehole, telemetry strip, one intelligence panel,
controls. Secondary detail opens in a drawer. `REPLAY MODE` is labelled permanently.

Remaining polish, none of it structural:

- Telemetry chart in the simulator (the strip is numeric; `TelemetryChart` already exists
  and can be reused).
- Auto-pause when the indicator crosses a configured threshold — the "simulation pauses on
  flag" beat of the golden demo is currently manual.
- Visual pass toward the restrained industrial look; current styling reuses existing
  tokens and is functional rather than finished.

One thing to know before demoing: **bit depth reads 0.0 m early in the Volve recording.**
That is real — the bit is at surface and this is a completion/workover run, not
drilling-ahead. It is a measurement, not a missing value.

---

## Important files

| Path | Role |
|---|---|
| `config/default.yaml` | every threshold, weight, window, K value |
| `backend/app/models/entities.py` | 16 tables; two distinct embedding widths |
| `backend/app/models/types.py` | dialect-adaptive Vector / GeographyPoint |
| `backend/app/services/analogue.py` | similarity + weight renormalisation |
| `backend/app/services/risk.py` | mode resolution, scoring, alert dedupe |
| `backend/app/services/replay.py` | server-owned replay state |
| `backend/app/services/document_search.py` | passage retrieval with citations |
| `backend/app/repositories/wells.py` | PostGIS / haversine proximity |
| `data_pipeline/documents/{ocr,extract,ingest,embed}.py` | OCR → NLP → knowledge → index |
| `ml/lithology/{train,predict,cross_validate}.py` | training, inference, selection |
| `frontend/src/lib/api.ts` | **every** UI value passes through here |
| `frontend/src/pages/Simulator.tsx` | the simulator |
| `frontend/src/hooks/useTelemetryStream.ts` | WebSocket, reconnect-safe |
| `docs/DOCKER.md` | what is verified vs not |
| `docs/SIMULATOR_CHECKLIST.md` | simulator progress |

---

## Safe areas to modify

- `frontend/src/pages/Simulator.tsx` and `components/DrillTrack.tsx` — new, self-contained.
- Presentational styling and design tokens (`tailwind.config`), which exist to be swapped.
- Adding **new** endpoints or pages.
- `backend/requirements.txt` — needs `pyarrow` anyway.
- Extraction lexicons in `data_pipeline/documents/extract.py`; they are data, and
  `--reextract` makes iterating free.

## Areas not to break

- **`frontend/src/lib/api.ts`** — the single data boundary. Fetching around it breaks the
  "no fabricated values" property the whole project rests on.
- **`backend/app/models/types.py`** — the dialect-adaptive types are load-bearing for the
  SQLite fallback *and* Postgres, and this is exactly where GeoAlchemy2 broke.
- **`services/risk.py` mode resolution** — do not hardcode a probability or a mode.
- **`ml/lithology` selection rule** — validation wells only. Selecting on the holdout
  invalidates the only external number the project reports.
- **`services/replay.py`** — replay state is server-owned so two viewers see the same
  position. Do not move it into the client.
- **The honesty primitives** (`Unavailable`, `Metric`'s null handling) — a zero where a
  value is unknown is a safety problem, not a cosmetic one.
- Alembic migration ordering: `load_database --reset` calls `drop_all` and must run
  *before* the migration, or the hypertable and vector indexes are destroyed.
