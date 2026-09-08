# NWIS — Handoff

Repo: `github.com/Physics0070/nwis` · `main`
Last session: 2026-09-08 — **final feature audit, completion and demo verification**

---

## 1. Goal

An evidence-backed drilling decision-support prototype for SIH 2026 (PS 26121): real data
through ingestion → database → trained models → analogue engine → risk engine → React
dashboard, plus a **drilling simulator** that replays real recorded telemetry.

The rule the whole thing rests on: **no number is shown unless a measurement produced it.**
Unknown values say so instead of rendering a zero.

---

## 2. Current state

**Working, on both storage backends.** 86 backend + 30 frontend tests pass. TypeScript
clean, production build clean, no JavaScript console errors. The full judge journey was
walked in a browser on the SQLite fallback *and* on the Docker Postgres stack.

| | Measured 2026-09-08 |
|---|---|
| Wells | 101 (98 FORCE + 3 Volve), 101 with a real surveyed position |
| Telemetry | 86,800 samples across 3 Volve wells |
| Drilling events | 243 — 184 WITSML remarks, 59 categorised from reports |
| … with a recovered depth | **10** — only these can serve as depth-matched risk evidence |
| Mitigations | 15, each citing a document and page |
| Documents | 8 reports, 320 pages OCR'd, 410 passages, **410 of 410 embedded** |
| Segment embeddings | 3,620 · Anomaly scores 8,130 · Lithology predictions 18,842 |

**Docker: verified.** `fallback_active: false`, with postgis 3.6.4, timescaledb 2.29.2 and
vector 0.8.6 all active, the telemetry hypertable created (3 chunks, 86,800 rows), the GIST
index and both ivfflat indexes present. The whole knowledge base loads and the simulator
runs against it.

Full detail: **`docs/FINAL_QA_REPORT.md`**, **`docs/FEATURE_AUDIT.md`**,
**`docs/FEATURE_COMPLETION_PLAN.md`**, **`docs/MASTER_QA_CHECKLIST.md`**.

**Open:** report-corpus coverage (8 of 388) and extraction precision — see §6.

---

## 3. Active files

| Path | Role |
|---|---|
| `frontend/src/pages/Simulator.tsx` | the simulator — radar, seek, investigate/pause, report search |
| `frontend/src/components/DepthRadar.tsx` | the historical risk radar |
| `frontend/src/hooks/useTelemetryStream.ts` | WebSocket + reconnect |
| `frontend/src/lib/api.ts` | **every** UI value passes through here |
| `backend/app/api/routes/intelligence.py` | depth-resolved risk, `/api/status.config` |
| `backend/app/services/risk.py` | risk engine, evidence gathering, alerting |
| `config/default.yaml` | every threshold, including the new `ui:` section |
| `backend/alembic/versions/20260907_0000_initial_schema.py` | hypertable + indexes |
| `ml/anomaly/train.py` | anomaly model + per-row attribution |

---

## 4. What changed this session

Fourteen defects found by running the system, not by reading it. The five that would have
misled a judge:

1. **Risk ignored the depth it was asked about.** Telemetry and the anomaly score were
   resolved as "most recent row", so the measured half of every assessment was frozen at
   the end of the recording while the historical half followed the bit. Both now resolve at
   the queried depth within `risk.telemetry_match_tolerance_m`; past that they are reported
   unavailable rather than borrowed.
2. **Routine operations counted as risk evidence.** 184 uncategorised WITSML remarks —
   toolbox talks, rig moves — produced a MEDIUM indicator "supported by 65 historical
   records". Only categorised events count now.
3. **No alert was ever raised.** Nothing called risk with `persist=true`, so the Alerts
   inbox was empty and engineer actions were impossible. The live session now persists what
   it evaluates.
4. **Evidence at exactly the bit's depth scored as the weakest.** `abs(offset or lookahead)`
   — `0.0` is falsy. An exact depth match returned `historical_evidence: 0.000`; it is
   0.739 now.
5. **Institutional memory could not be built from a clean checkout.** `wellbore_document.csv`
   was required and never downloaded. Now in config and in `download_datasets.py`.

Also fixed: the TimescaleDB hypertable could never be created (primary key omitted the
partitioning column); mitigation provenance was dropped before reaching the UI; the anomaly
had no "why" at all; the registry recorded `device: cuda` on a machine with no GPU; event
citations leaked absolute filesystem paths; the WebSocket claimed to reconnect and did not;
four backend thresholds were duplicated in the frontend; an actionable 503 looked like a
hang; the first report search took 14.5 s.

Added, all from data that already existed: the **historical risk radar**, **investigate
pauses the replay**, **report search inside the investigation drawer**, a **seek control**
on the replay bar, and **analogue dimension disclosure** in the simulator.

---

## 5. Failed attempts — do not repeat

- **Do not use the interquartile range to scale anomaly feature deviations.** Rolling-slope
  features are zero for most active rows, so their IQR is ~0 and every non-zero value
  divides out to hundreds of "sigma" — the same handful of slope features then ranked top
  for every sample. Standard deviation is inflated by exactly those spikes and ranks what is
  genuinely unusual: 105 distinct top features across 8,130 scores instead of a handful.
- **Do not trust a successful XGBoost fit as proof of a GPU.** XGBoost warns and falls back
  to CPU rather than raising. Read the resolved device from the fitted booster's saved
  config.
- **Do not write `abs(x or default)` where `x` can legitimately be 0.0.** It silently turns
  the best case into the worst.
- **`create_hypertable` needs the partitioning column in the primary key.** `(id)` alone
  fails; `(id, recorded_at)` works, PostgreSQL-side only.
- **Do not select the lithology model on the holdout.** Cross-validation showed the two
  candidates are not separable (fold spread 3.1× the gap, p = 0.224). The disagreement
  reproduced on this rebuild.
- **Bit depth 0.0 m early in Volve is genuine** — the bit is at surface on a
  completion/workover run. Do not "fix" it.
- **A stale Vite bundle looks exactly like broken features.** Restart Vite and clear
  `node_modules/.vite` before debugging the application.
- **A stale uvicorn process looks exactly like a fix that did not work.** `--reload` is off
  in the audit setup; restart it after touching backend code.
- **`api.wells` does not exist — it is `api.listWells`.**

---

## 6. Next steps, in order

1. **Ingest more reports, and the deeper pages of those already read.** Coverage is the only
   thing keeping institutional memory and mitigation strategies at PARTIAL. `--start-page`
   exists for this, page text is cached under `data/interim/document_text`, and Tesseract is
   installed, so a second pass is cheap. Target the wells the analogue engine actually
   returns: `15/9-17`, `15/9-13`, `16/7-5`, `15/9-15`, `16/7-4`.
2. **Tighten event extraction.** Some categorised "events" are OCR'd table-of-contents lines
   — *"FISHING —0.00 — 0.00"* is a rig-time summary table, not an incident. They never reach
   the radar because they carry no depth, but they inflate the event count.
3. **Run the full `docker compose up --build` end to end**, including the containerised
   loader and the nginx frontend. The database, migration and complete data load were
   verified against the container; the backend and frontend images were not rebuilt.

**Best demo depths** (verified, all with real evidence and citations):

| Well | Seek to sample | Bit depth | What appears |
|---|---|---|---|
| `NO 15/9-F-7` | 8,167 → resume briefly | ~500 m | `16/7-5` overpull 41 m behind, mitigation *"cemented"*, source *Final Well Report p.30* |
| `NO 15/9-F-4` | ~43,400 | ~2,375 m | `16/7-5` fishing operation, 7 m from the bit |

`NO 15/9-F-9` starts moving immediately and is the best well for showing the replay itself;
`NO 15/9-F-4` stays at 0 m until sample 14,952 of 59,806, which is what the seek control is
for.

**Before any demo:** never claim eRTMAC integration, WITSML use by eRTMAC, or validation on
Indian wells. See `docs/OIL_FACT_CHECK.md` — all three OIL source URLs were re-verified on
2026-09-08 and still contain the quoted text.

---

## Run it

```bash
uvicorn backend.app.main:app --reload   # terminal 1
cd frontend && npm run dev              # terminal 2 → http://localhost:5173/simulator
```

For the Postgres stack, `docker compose up -d database`, then set `DATABASE_URL` and run
the loaders (`data_pipeline.load_database --reset`, `alembic upgrade head`,
`load_embeddings`, `load_anomaly_scores`, `ml.lithology.predict`, the document ingest).

`GET /api/status` shows what is loaded, which storage backend is active, and every
threshold the UI renders with.
