# NWIS — Handoff

Repository root: `D:\SOHAM ALL\hackathons\SIH`
Last session: 2026-09-07 · 22 commits on `master`, 9 of them this session

---

## 1. Goal

Build a genuinely functional end-to-end prototype of **NWIS (Nearby Wells Intelligence
System)** for SIH 2026 PS 26121: real data flowing through ingestion → databases → trained
models → analogue engine → risk engine → React dashboard, with **no fabricated values
anywhere** and every claim traceable to a measurement.

---

## 2. Current state

**Running and verified.** 89 tests pass (75 backend, 14 frontend). UI verified in Chromium
with zero console errors.

| Item | Measured |
|---|---|
| Wells | 101 (98 FORCE + 3 Volve), **0 without a real surveyed position** |
| Telemetry | 86,800 samples @ 10 s, 20 normalised channels |
| Drilling events | 331 — 184 from Volve WITSML, **147 from report text** |
| **Mitigations** | **48** (was 0 — see §4) |
| Documents | 2 reports, **429 pages OCR'd** @ 0.957 confidence |
| Passages | 562 chunks, **562 embedded** (384-d MiniLM-L6-v2) |
| Embeddings | 3,620 segments × 32 interpretable dimensions |
| Lithology predictions | 18,842 stored |
| Anomaly scores | 8,130 stored (163 flagged, 2.0%) |
| Formation intervals | 1,482 (1,449 FORCE + 33 from OCR'd reports) |

**Lithology model selection is now settled.** Grouped (by well) 5-fold CV over the 83
selection wells: XGBoost 0.3840 ± 0.0543, RandomForest 0.3667 ± 0.0462, mean difference
0.0173, paired p = 0.224.

> **The two models are not separable on this data.** The spread between folds is 3.1× the
> difference between the models. The old validation-vs-holdout disagreement was never a
> contest between the models — it was the variation between draws of wells.

RandomForest (the pre-committed validation-only choice) is still served. `selected.json`
carries the verdict beside it with `models_separable_on_this_evidence: false`.

**Risk** runs as `hybrid_indicator` with `probability: null` — there are still no
*labelled* incidents. The 48 mitigations are extracted actions with page citations, not
supervised labels; see §6 item 1.

**Open:** `docker compose up --build` has **still never been executed.** Docker Desktop
needs WSL2, `wsl --status` reports it is not installed, and installing it raises a UAC
prompt that a non-interactive session cannot approve. Everything runs on the documented
local fallback (SQLite + SQL haversine + NumPy cosine), which the UI states in its header.

---

## 3. Active files

| Path | Role |
|---|---|
| `config/default.yaml` | every threshold, weight, window, K value — no magic numbers in code |
| `config/production.yaml` | production overlay: no fallback DB, empty CORS |
| `backend/app/core/startup.py` | fail-fast deployment checks |
| `backend/app/services/analogue.py` | contextual similarity + weight renormalisation |
| `backend/app/services/risk.py` | mode resolution, scoring, alert dedupe |
| `backend/app/services/document_search.py` | semantic search over passages |
| `data_pipeline/documents/{ocr,extract,ingest,embed}.py` | OCR → NLP → knowledge → index |
| `ml/lithology/{train,predict,cross_validate}.py` | training, selection, inference, CV |
| `frontend/src/pages/ActiveWell.tsx` | the main dashboard |
| `frontend/src/pages/Reports.tsx` | report search |
| `frontend/src/lib/api.ts` | **every** value the UI shows passes through here |
| `scripts/bootstrap.py` | one-command rebuild of the whole knowledge base |
| `CHECKLIST.md` | 1 blocked (Docker) |

---

## 4. Changes made this session

Worked items 2, 3 and 4 of the previous handoff's next-steps list.

- **Read both completion reports in full** (429 pages). `--start-page` existed in the OCR
  layer but was never exposed, and ingest skipped any document it had seen, so nothing
  past page 30 was reachable. Passing a `--start-page` beyond the stored range now
  **extends** the document: chunk numbering continues, overlapping pages are filtered
  against the deepest page stored, events are deduplicated.
- **The prediction in the last handoff was right.** Pages 31+ are the drilling-operations
  narrative: 157,278 and 191,406 characters against 21,869 and 26,529 for the first 30
  pages. **Mitigations went from 0 to 48** — real recorded actions, each with the page it
  came from.
- **Fixed two extraction defects that only surfaced at this volume**, both of which put
  claims in front of an engineer that the source does not support:
  - *Negation.* "No tight spot" was stored as a tight_hole event. 12 denied mentions.
  - *Routine operations.* A leak-off test is a planned integrity test, but the bare term
    "leak" matched it — 11 of 72 events in one document. 25 routine mentions.
  Exclusions are counted and reported **by reason**, never dropped silently.
- **Added `--reextract`.** `ocr.py` has always claimed re-running extraction costs
  nothing; ingest made it impossible. Now the cached page text is read back and derived
  events/mitigations rebuilt with no download and no OCR.
- **Document chunk embeddings.** Needed a schema correction first: `document_chunks.
  embedding` was `Vector(32)`, sharing the constant with the 32-d petrophysical segment
  vectors, but a sentence-transformer emits 384. Separate constants now; the column type
  rejects a wrong-width vector so the two spaces can never be compared.
- **`GET /api/documents/search` + a Reports page.** Returns passages with page citations,
  never a generated answer. Reports how much of the corpus is indexed; a query matching
  nothing renders an explicit absence rather than the least-bad passage.
- **Grouped cross-validation** (§2). Plus `--verdict-only` to re-derive the conclusion
  from an existing report without a 50-minute refit.
- Fixed Enter not submitting the report search (implicit form submission was not firing).

---

## 5. Failed attempts — do not repeat

- **`winget install UB-Mannheim.TesseractOCR`** fails: machine-scope install raises a UAC
  prompt a non-interactive session cannot approve (`0x800704c7`). `--scope user` gives "no
  applicable installer". **RapidOCR (pip, ONNX) is used instead** and works well. The
  engine is pluggable via `documents.ocr.engine`; Tesseract is preferred automatically the
  moment its binary exists on PATH.
- **`pkill -f "uvicorn backend.app.main"` does not kill the server on Windows.** Use
  `Get-NetTCPConnection -LocalPort 8000 -State Listen` → `Stop-Process -Force`. Confirmed
  again this session: the stale server served a 404 for the new route and looked like a
  code bug.
- **Bash heredocs mangle regex backslashes.** Hit again this session: `\b` in a heredoc
  became a literal backspace byte (0x08) inside a compiled regex, and the pattern silently
  matched nothing. **Write Python source with the Write/Edit tools**, or with a fixer
  script written by Write — never through a heredoc. Same for `git commit -F <file>`.
- **OCR on these scans costs ~14 s/page, not the ~8 s/page a shallow sample suggests.**
  Deep pages are dense. 369 pages took ~68 minutes. Budget accordingly; the page-text
  cache means it is paid only once.
- **RandomForest at `n_estimators=300, min_samples_leaf=5`** took **10.1 hours** for a
  901 MB artifact. Retuned to 150/25: 291 s, 179 MB, holdout macro F1 unchanged. Do not
  raise those back.
- **XGBoost early stopping on `mlogloss`** halts at iteration 54, before rare lithologies
  are learned. Stop on macro F1 instead.
- **Do not select XGBoost because it wins on the holdout.** That is selecting on the test
  set. Cross-validation has now shown the models are not separable anyway, so there is no
  performance argument for re-opening the pre-committed rule.
- **In cross-validation, derive the class index from the rows actually fitted.** Deriving
  it from the whole fold leaves a gap in the label encoding when a rare class lands only
  in the early-stopping wells, and XGBoost rejects it outright.
- Volve `message.md` and the `Depth` channel are **constants** (wellbore total depth). See
  `ASSUMPTIONS.md` A10.
- Searching the Volve corpus for incident vocabulary returns 4 weak matches and **none are
  incidents** ("Kick drill" is a practice drill — the same false-positive class as the
  leak-off tests fixed this session). Do not build a supervised risk model on manufactured
  labels.

---

## 6. Next steps, in order

1. **Label the 48 extracted mitigations, or decide not to.** This is the first time the
   corpus contains real problem→action chains with citations. They are *extracted text*,
   not labelled incidents, and they are not yet enough to flip `risk.mode` to supervised
   — 48 actions across 2 wells is not a training set. The honest options are: ingest more
   reports (there are **388 in the Sodir index covering 75 of our wells**, and only 2 are
   read) to get to a usable count, or keep `hybrid_indicator` and say why. Do not split
   the difference by training on 48 rows.
2. **Verify the Docker stack.** Install WSL2 + Docker Desktop (needs a human to approve
   the UAC prompt), then `docker compose up --build`. Still the only checklist item open
   and the only way to exercise PostGIS / TimescaleDB / pgvector. The 562 stored 384-d
   vectors now make the pgvector path worth actually testing.
3. **Ingest more reports.** `--limit N` walks the Sodir index largest-first, and the
   deep-page path is now proven. This is the input to step 1. Budget ~14 s/page.
4. **Review the extraction lexicons against the new volume.** `PROBLEM_TERMS`,
   `NEGATION_TERMS` and `ROUTINE_OPERATION_TERMS` are data, and 147 events is the first
   sample large enough to audit. `--reextract` makes each iteration free. Note that
   0 of 48 mitigations carry an `outcome` — `OUTCOME_TERMS` is not matching this corpus's
   vocabulary and is the obvious next thing to fix.
5. Optional: local LLM (Phi-3.5-mini fits the 6 GB RTX 4050) for natural-language
   questions over the passages the search already retrieves. It must never author risk
   assessments or mitigations.
6. Before any non-trusted network exposure: authentication, TLS, rate limiting. The
   prototype has none — see `docs/DEPLOYMENT.md`.

---

## Run it

```bash
python scripts/bootstrap.py            # build everything (skips what exists)
uvicorn backend.app.main:app --reload  # terminal 1
cd frontend && npm run dev             # terminal 2  → http://localhost:5173
```

Document pipeline, in order:

```bash
python -m data_pipeline.documents.ingest --list
python -m data_pipeline.documents.ingest --well 15/9-13 --max-pages 30
python -m data_pipeline.documents.ingest --well 15/9-13 --start-page 30 --max-pages 167
python -m data_pipeline.documents.ingest --reextract   # free: re-run NLP over cached text
python -m data_pipeline.documents.embed                # index for search
```

`GET /api/status` is the fastest way to see what is loaded and which storage backends are
actually active.
