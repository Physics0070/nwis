# NWIS — Handoff

Repository root: `D:\SOHAM ALL\hackathons\SIH`
Last session: 2026-09-07 · 13 commits on `master`

---

## 1. Goal

Build a genuinely functional end-to-end prototype of **NWIS (Nearby Wells Intelligence
System)** for SIH 2026 PS 26121: real data flowing through ingestion → databases → trained
models → analogue engine → risk engine → React dashboard, with **no fabricated values
anywhere** and every claim traceable to a measurement.

---

## 2. Current state

**Running and verified.** 62 tests pass (48 backend, 14 frontend). Zero 5xx across a
26-endpoint sweep. UI verified in Chromium with zero console errors, in both the dev server
and the production bundle.

| Item | Measured |
|---|---|
| Wells | 101 (98 FORCE + 3 Volve), **0 without a real surveyed position** |
| Telemetry | 86,800 samples @ 10 s, 20 normalised channels |
| Events | 185, depths recovered by time-join |
| Embeddings | 3,620 segments × 32 interpretable dimensions |
| Lithology predictions | 18,842 stored |
| Anomaly scores | 8,130 stored (163 flagged, 2.0%) |
| Documents | 2 reports, 60 pages OCR'd @ 0.968 confidence, 33 formation intervals |

**Lithology model** (10 unseen FORCE leaderboard wells): RandomForest *selected* —
accuracy 0.7022, macro F1 0.3337. XGBoost scores better on holdout (0.7498 / 0.3852) but
loses on validation, which is the pre-committed selection metric. The disagreement is
recorded in `artifacts/models/lithology/selected.json`, not resolved by picking the holdout
winner.

**Risk** runs as `hybrid_indicator` with `probability: null` — there are no labelled
incidents in either dataset, verified by searching the corpus.

**Open:** `docker compose up --build` has **never been executed**. Docker Desktop needs
WSL2, and the install hit a UAC prompt that cannot be approved from a non-interactive
session. All compose/Dockerfile/nginx artifacts are written and unit-tested for structure,
but the PostGIS / TimescaleDB / pgvector runtime paths are unverified. Everything currently
runs on the documented local fallback (SQLite + SQL haversine + NumPy cosine), which the UI
states plainly in its header badge.

---

## 3. Active files

| Path | Role |
|---|---|
| `config/default.yaml` | every threshold, weight, window, K value — no magic numbers in code |
| `config/production.yaml` | production overlay: no fallback DB, empty CORS |
| `backend/app/core/startup.py` | fail-fast deployment checks |
| `backend/app/services/analogue.py` | contextual similarity + weight renormalisation |
| `backend/app/services/risk.py` | mode resolution, scoring, alert dedupe |
| `backend/app/services/replay.py` | server-owned telemetry replay |
| `ml/lithology/{train,predict}.py` | training, selection, inference |
| `data_pipeline/documents/{ocr,extract,ingest}.py` | OCR → NLP → knowledge |
| `frontend/src/pages/ActiveWell.tsx` | the main dashboard |
| `frontend/src/lib/api.ts` | **every** value the UI shows passes through here |
| `scripts/bootstrap.py` | one-command rebuild of the whole knowledge base |
| `docker/nginx.conf` | same-origin proxy for `/api` and `/ws` |
| `CHECKLIST.md` | 62 done, 1 blocked (Docker) |

---

## 4. Changes made this session

- **Added the missing lithology inference stage.** `/api/wells/{id}/lithology` always
  returned 404 because nothing ever ran the model. Now 18,842 predictions stored, using the
  model named by `selected.json` rather than the newest artifact.
- **Added a context-depth selector.** Wells without telemetry never resolved a depth, so
  geology, lithology and analogues were empty on all 98 FORCE wells.
- **Persisted anomaly scores** so the risk engine resolves them without a caller supplying
  one.
- **Persisted OCR-extracted formation intervals** (33) — they were being computed and
  discarded.
- **Added OCR page-text caching** — re-running extraction cost a 25-min OCR pass per doc.
- **Deployment hardening**: `config/production.yaml`; fail-fast startup checks; nginx
  same-origin proxy so production uses no CORS; WebSocket URL derived from
  `window.location` (selects `wss://` on HTTPS); fixed the compose frontend build context;
  security headers; non-root image; `scripts/bootstrap.py`; `docs/DEPLOYMENT.md`.
- **UI fixes**: stale websocket error shown beside a live connection; risk/analogue panels
  stuck loading because they re-queried every 10 s sample (depth now quantised to 25 m);
  Carto basemap rendering "API KEY REQUIRED" (switched to key-free OSM + CSS dark filter);
  map now fits bounds to the wells shown.
- Suppressed the Windows `ConnectionResetError` traceback on every websocket disconnect.
- Untracked SQLite WAL files and screenshots from git.

---

## 5. Failed attempts — do not repeat

- **`winget install UB-Mannheim.TesseractOCR`** fails: machine-scope install raises a UAC
  prompt that a non-interactive session cannot approve (`0x800704c7`). `--scope user` gives
  "no applicable installer". **RapidOCR (pip, ONNX) is used instead** and works well
  (0.968 confidence). The OCR engine is pluggable via `documents.ocr.engine`; Tesseract is
  preferred automatically the moment its binary exists on PATH.
- **`pkill -f "uvicorn backend.app.main"` does not kill the server on Windows.** The old
  process keeps serving stale config. Use
  `Get-NetTCPConnection -LocalPort 8000 -State Listen` → `Stop-Process -Force`.
- **Playwright `wait_until="networkidle"` always times out** on any page with an open
  WebSocket. Use `wait_until="domcontentloaded"` plus an explicit `wait_for_timeout`.
- **Bash heredocs mangle apostrophes and regex backslashes** in this environment. Use the
  Write/Edit tools for source files, and `git commit -F <file>` for messages containing
  apostrophes.
- **RandomForest at `n_estimators=300, min_samples_leaf=5`** took **10.1 hours** and made a
  901 MB artifact. Retuned to 150/25: 291 s, 179 MB, holdout macro F1 unchanged
  (0.3336 → 0.3337). Do not raise those back.
- **XGBoost early stopping on `mlogloss`** halts at iteration 54, before rare lithologies
  are learned. Stop on macro F1 instead (runs to 384).
- **Do not select XGBoost because it wins on the holdout.** That is selecting on the test
  set. The rule is validation-only; the disagreement is documented instead.
- Volve `message.md` and the `Depth` channel are **constants** (wellbore total depth). Any
  on-bottom test derived from them is an artefact. See `ASSUMPTIONS.md` A10.
- Searching the Volve corpus for incident vocabulary returns 4 weak matches and **none are
  incidents** ("Kick drill" is a practice drill). Do not build a supervised risk model on
  manufactured labels.

---

## 6. Next steps, in order

1. **Verify the Docker stack.** Install Docker Desktop (needs WSL2 + admin, so a human must
   approve the UAC prompt), then `docker compose up --build`. This is the only checklist
   item still open and the only way to exercise PostGIS / TimescaleDB / pgvector.
2. **Ingest deeper report pages.** The first 30 pages of the completion reports are
   geological sample descriptions; the drilling-operations narrative — where real
   problem/action/outcome chains live — is further in. Try
   `--max-pages 120`, or add a `--start-page` argument. This is what would eventually flip
   `risk.mode: auto` to supervised.
3. **Embed document chunks.** 75 chunks are stored with `embedding = NULL`. Populating them
   with `sentence-transformers/all-MiniLM-L6-v2` enables semantic search over reports.
4. **Grouped cross-validation for model selection.** A 15-well validation set cannot
   separate RandomForest from XGBoost; k-fold over the 68 training wells would give a stable
   answer and settle the recorded disagreement.
5. Optional: local LLM (Phi-3.5-mini fits the 6 GB RTX 4050) for natural-language search
   over report passages. It must never author risk assessments or mitigations.
6. Before any non-trusted network exposure: authentication, TLS, rate limiting. The
   prototype has none — see `docs/DEPLOYMENT.md`.

---

## Run it

```bash
python scripts/bootstrap.py            # build everything (skips what exists)
uvicorn backend.app.main:app --reload  # terminal 1
cd frontend && npm run dev             # terminal 2  → http://localhost:5173
```

`GET /api/status` is the fastest way to see what is loaded and which storage backends are
actually active.
