# NWIS — Build Checklist

Living progress tracker. Status legend: `[ ]` not started · `[~]` in progress · `[x]` done · `[!]` blocked/assumption recorded.

Last updated: 2026-09-07

---

## Phase 0 — Repository & environment inspection

- [x] Inspect machine for existing NWIS code, Stitch assets, datasets
- [x] Read SIH 2026 idea submission (PS 26121, Team 53476) to ground scope
- [x] Confirm toolchain: Python 3.12, Node 25, git, network access
- [x] Confirm FORCE 2020 reachable (train.zip + labelled leaderboard test set)
- [x] Confirm Volve WITSML reachable (3 wells: F-4, F-7, F-9)
- [x] Profile Volve WITSML object types and real curve mnemonics
- [x] Create repository skeleton
- [x] Record environment assumptions in `docs/ASSUMPTIONS.md`

## Phase 1 — Repository, configuration, database, Docker

- [x] Centralised configuration (`config/*.yaml` + `.env.example`), zero magic numbers
- [x] Structured logging
- [x] SQLAlchemy models: 16 tables (wells, trajectories, logs, formations,
      lithology_predictions, telemetry, anomalies, events, mitigations, risk,
      alerts, embeddings, documents, chunks, engineer_actions, model_versions)
- [x] Alembic migration (dialect-aware: hypertable + GIST + ivfflat on Postgres)
- [x] Storage adapter layer — dialect-adaptive types + runtime capability detection
- [x] `docker-compose.yml` (postgres+postgis+timescale+pgvector, backend, frontend)
- [x] Database bring-up verified (16 tables, honest fallback reporting)

## Phase 2 — Dataset acquisition & profiling

- [x] Reproducible download scripts (FORCE, Volve, NPD coordinates)
- [x] FORCE profiling report (wells, curves, missingness, class balance, depth ranges)
- [x] Volve WITSML profiling report (3 wells, 781,558 log rows, units audited)
- [x] Data-quality validation rules + report (FORCE + Volve; implausible values flagged not deleted)

## Phase 3 — FORCE preprocessing

- [x] Configurable preprocessing pipeline (raw → cleaned → processed → features)
- [x] Well-level train/val/test split manifest

## Phase 4 — FORCE lithology model

- [x] Random Forest baseline - retuned: 291 s / 179 MB (was 36,280 s / 901 MB), same metrics
- [x] XGBoost on RTX 4050 — 196 s, early stopping on macro F1, class-balanced
- [x] Evaluation on unseen wells — internal 15-well test + official 10-well FORCE holdout
- [x] Model registry entry + metrics persisted (no invented numbers)
- [x] MODEL_CARD entry (docs/MODEL_CARD.md)

## Phase 5 — Volve preprocessing

- [x] WITSML parser (log, trajectory, message, bhaRun, wellInfo)
- [x] Normalised telemetry schema + unit conversion (20 channels, SI -> driller units)
- [x] Cleaned/aligned telemetry dataset (86,800 rows @10 s, 8,130 operationally active)

## Phase 6 — Telemetry feature engineering

- [ ] Configurable rolling/derivative features
- [ ] Feature spec documented

## Phase 7 — Anomaly detection

- [x] Isolation Forest trained (121 features, 8,130 active rows, 1.05 s)
- [x] Contributing-feature attribution wired into risk payload
- [ ] Persisted anomaly results

## Phase 8 — Contextual analogue engine

- [x] Well/segment feature representation + embeddings (3,620 segments, 32 interpretable dims)
- [x] Vector similarity search (pgvector column; NumPy cosine on fallback)
- [x] Transparent weighted re-ranking; unavailable dimensions renormalised and reported
- [x] Top-K configurable

## Phase 9 — Historical event intelligence

- [ ] Event extraction from WITSML messages
- [ ] Event → depth interval → formation linkage
- [ ] Evidence traceability to source records

## Phase 10 — Risk engine

- [x] Label sufficiency assessment — auto-resolves to hybrid_indicator (0 labelled events)
- [x] Risk scoring: anomaly + historical evidence + configurable rules
- [x] Explainability payload with evidence, components, narrative, notes

## Phase 11 — OCR / NLP knowledge ingestion

- [x] Document ingestion pipeline (OCR + NLP, page-text caching)
- [x] Entity/event extraction with provenance + confidence
- [x] Knowledge records persisted (2 docs, 75 chunks, 33 formations, 1 event)

## Phase 12 — Telemetry replay engine

- [x] Replay from real telemetry (start/pause/resume/stop/speed/seek)
- [x] Backend-owned replay state

## Phase 13 — FastAPI integration

- [x] REST endpoints (wells, nearby, analogues, events, telemetry, lithology, risk, alerts, mitigations, models, actions, status)
- [x] OpenAPI docs (FastAPI auto-generated at /docs)
- [x] Error handling + graceful degradation (sanitised errors, explicit unavailable states)

## Phase 14 — Frontend

- [x] Vite + React + TS + Tailwind scaffold (builds clean)
- [x] Design system: token-driven, Stitch-swappable (see ASSUMPTIONS A1)
- [x] Pages: Overview, Wells, Active Well (map+telemetry+analogues+risk+events),
      Alerts, Alert Explanation (+engineer feedback), Model Insights
- [x] Every value API-driven; loading / error / explicitly-unavailable states

## Phase 15 — Real-time WebSocket

- [x] `/ws/telemetry/{well_id}` streaming verified
- [x] Live risk panel follows replayed bit depth

## Phase 16 — Feedback loop

- [x] Engineer action capture -> institutional memory (POST /api/engineer-actions)

## Phase 17 — Testing

- [x] Backend tests: 31 passing, 2 skipped
- [x] Frontend tests: 14 passing (missing-value rendering, level vocabulary, formatting)

## Phase 18 — Docs & Docker demo

- [x] README, ARCHITECTURE, DATA_PIPELINE, ML, API, SETUP, DEMO, MODEL_CARD, ASSUMPTIONS
- [ ] `docker compose up --build` verified

---

## Progress log

| Phase | Result (measured, not estimated) |
|---|---|
| Datasets acquired | FORCE 389 MB (1,170,511 rows / 98 wells / 12 classes) + Volve 200 MB (781,558 telemetry rows / 3 wells / 79 curves) |
| FORCE profiled | 0 duplicate (well,depth) rows, 0 non-monotonic wells, 19 curves >= 30% coverage, imbalance 6,998:1 |
| FORCE prepared | 15 base curves -> 90 features (rolling 5 m / 15 m mean+std, gradients); depth sampling measured at 0.152 m |
| Well-level split | 68 train / 15 validation / 15 test wells. Only Basement (1 well dataset-wide) cannot reach val/test |
| External holdout | FORCE leaderboard set: 136,786 rows across 10 wells absent from training |
| Volve profiled | 3 wells, 781,558 log rows; messages carry total depth only, so event depth must be recovered by time-join |
| **Lithology model** | **XGBoost (GPU, 196 s): holdout accuracy 0.7498, macro F1 0.3852, kappa 0.5659, FORCE penalty 0.6322** vs majority baseline 0.6139 / 0.0761 |
| Volve reality check | Mirror is completion/workover, not drilling-ahead: ROP constant 0, Depth constant. Scoped honestly (see A10) |
| Message depths | All 184 remarks given real depths by time-join to telemetry (were all constant total depth) |
| Database loaded | 101 wells, 1,449 formation intervals, 18,842 log samples, 86,800 telemetry rows, 154 trajectory stations, 184 events, 0 wells without a real position |
| CRS cross-validated | Volve (NPD source) lands at 58.441N 1.886E; nearest FORCE well (independent source) is 15/9-17 at 3.6 km, same licence block 15/9 |
| Analogue engine | Proven non-proximity: 16/10-1 at 36 km ranks top-5 on geology 0.9873 despite geography score 0.2372. All top matches share Tor Fm. |
| Document ingestion | 60 pages OCR'd at 0.968 mean confidence; 33 formation intervals recovered from scanned 1980s completion reports |
| OCR honesty | Only 1 problem event and 0 mitigations found in these pages: they are sample descriptions, not operations narrative. Reported, not padded |
| Known limitation | Chalk -> Limestone 99.8%: carbonate family is not separable on the available curves. Documented, not hidden |

## Open items / risks

- **Stitch UI not present on disk.** Building an API-driven component architecture with a
  neutral NWIS design system; Stitch styling can be swapped in without touching data flow.
- **Docker & PostgreSQL not installed on this machine** (no WSL2). Compose stack is authored
  for the real Postgres+PostGIS+Timescale+pgvector target; local verification requires Docker.
- **Volve WITSML surface coordinates are zeroed** — ingesting from public NPD factpages.
