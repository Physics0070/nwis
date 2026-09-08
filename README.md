# NWIS — Nearby Wells Intelligence System

An AI-powered offset-well knowledge and decision-support platform for drilling operations.

Smart India Hackathon 2026 · Problem Statement **26121** (eRTMAC-NWIS) · Team **53476**, *Last 6 Braincells*

---

## What this is

A working end-to-end prototype. Data flows from real public datasets through ingestion,
databases, trained models, a similarity engine and a risk engine, into a React dashboard
over REST and WebSocket. **There is no mock data anywhere in the frontend.**

Every figure the UI shows came from the database, a model that was actually trained in this
repository, or a configuration file. Where something is unknown, the interface says so
rather than showing a zero.

### Verified state

| | Measured |
|---|---|
| Wells in knowledge base | **101** (98 FORCE + 3 Volve), 0 without a real surveyed position |
| Telemetry | **86,800** samples at 10 s cadence, 20 normalised channels |
| Historical events | **184** WITSML remarks with depths recovered from telemetry, plus categorised events extracted from reports |
| Segment embeddings | **3,620** across 98 wells, 32 interpretable dimensions |
| Lithology model | trained on 1,170,511 rows; evaluated on **10 wells it has never seen** |
| Anomaly model | trained on 8,130 operationally-active rows, 121 features |
| Lithology predictions | **18,842** stored, 0.80 agreement with labels |
| Documents ingested | 8 reports, 320 scanned pages OCR'd, 410 passages all embedded |
| Tests | **86 backend + 30 frontend passing** |

Counts move as more reports are ingested. `GET /api/status` is the authority; the figures
above are from the rebuild recorded in [`docs/FINAL_QA_REPORT.md`](docs/FINAL_QA_REPORT.md).

Model metrics are in [`docs/MODEL_CARD.md`](docs/MODEL_CARD.md). They are what the models
scored, including where they scored badly.

---

## The core idea

```
CURRENT WELL
   ↓  geological + drilling context at the bit
FIND HISTORICALLY SIMILAR WELLS        ← not the nearest wells; the most similar ones
   ↓  what happened in those wells at this depth
COMPARE LIVE TELEMETRY TO HISTORY
   ↓  anomaly + historical evidence + rules
EXPLAINABLE RISK INDICATOR
   ↓  which wells, which depths, which measurements, what was done
ENGINEER REVIEWS AND ACTS              ← the engineer decides, always
   ↓
OUTCOME STORED AS INSTITUTIONAL MEMORY
```

The analogue engine genuinely is not a proximity ranking. Worked example from the running
system: querying well `15/9-13` at 2500 m returns `16/10-1` — **36 km away** — in the top 5,
because its geology similarity is the highest of any candidate (0.9873) even though its
geography score is only 0.2372.

---

## Architecture

```
FORCE 2020 + Volve WITSML + NPD factpages
                 ↓
        data_pipeline/  (profile → prepare → load)
                 ↓
  PostgreSQL + PostGIS + TimescaleDB + pgvector
                 ↓
   ml/  lithology · anomaly · similarity
                 ↓
  backend/  analogue engine · risk engine · replay
                 ↓
     FastAPI REST + /ws/telemetry/{well_id}
                 ↓
              frontend/
```

Detail in [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md).

---

## Quick start

### With Docker (full Postgres stack)

```bash
cp .env.example .env          # set POSTGRES_PASSWORD
docker compose up --build
```

Frontend on <http://localhost:5173>, API on <http://localhost:8000>, docs at `/docs`.

### Without Docker (runs today on a plain Python install)

```bash
pip install -r backend/requirements.txt
cd frontend && npm install && cd ..

python scripts/bootstrap.py                  # builds everything, skips what exists

uvicorn backend.app.main:app --reload        # terminal 1
cd frontend && npm run dev                   # terminal 2
```

`bootstrap.py` runs every stage in dependency order: download, profile, prepare, train,
load, predict. Use `--dry-run` to preview, `--skip-documents` to skip the slow OCR stage,
and `--force` to rebuild from scratch.

Without the Postgres stack the backend uses a local storage backend and **says so** — in
the header badge, in `/api/status`, and in the startup log. Nearby-well search still runs
as a real SQL haversine query and vector search as NumPy cosine; results are equivalent.
See [`docs/ASSUMPTIONS.md`](docs/ASSUMPTIONS.md) A2.

Full instructions: [`docs/SETUP.md`](docs/SETUP.md) · Deployment:
[`docs/DEPLOYMENT.md`](docs/DEPLOYMENT.md) · Demo script: [`docs/DEMO.md`](docs/DEMO.md).

---

## Data

| Source | What it provides | Licence |
|---|---|---|
| [FORCE 2020](https://github.com/bolgebrygg/Force-2020-Machine-Learning-competition) | 1,170,511 labelled log rows, 98 wells, 12 lithofacies, real UTM coordinates | open competition data |
| [Volve WITSML](https://github.com/f0nzie/volve-drilling) | 312 XML files, 3 wellbores, 79 curves, trajectories, 184 operational remarks | Equinor open data |
| [NPD / Sodir factpages](https://factpages.sodir.no) | 8,452 official wellbore surface positions | public |

All acquired by `scripts/download_datasets.py`; nothing is committed to the repository.

**A cross-check worth knowing about.** Volve coordinates come from NPD; FORCE coordinates
come from the dataset's own `X_LOC/Y_LOC`. Transformed independently, Volve lands at
58.441 °N 1.886 °E and the nearest FORCE well is 3.6 km away *in the same licence block
15/9*. Two independent sources agreeing is what makes the CRS assumption trustworthy.

---

## What this prototype does not do

Stated plainly, because a decision-support tool that overstates itself is dangerous.

- **It does not control drilling equipment.** The engineer is the decision maker.
- **It does not report a risk probability.** With no labelled incident data, risk runs as a
  `hybrid_indicator` and `probability` is `null` by design — not zero, not a guess.
- **It does not name failure modes from telemetry.** The anomaly model reports *abnormal
  operational behaviour* with contributing features. Naming an event needs evidence.
- **It does not invent mitigations.** If no historical mitigation exists, it says
  "No validated historical mitigation found."
- **It cannot distinguish chalk from limestone.** The lithology model confuses them 99.8%
  of the time, because they are near-identical on the available curves. Documented, not
  hidden.
- **The Volve mirror is completion/workover data, not drilling-ahead.** `ROP` is constant
  zero across all 86,800 rows. Anomaly detection is scoped to rig operations accordingly.
  See [`docs/ASSUMPTIONS.md`](docs/ASSUMPTIONS.md) A10.

---

## Repository layout

```
config/            layered YAML — every threshold, weight, window and K value
nwis_common/       configuration, logging, path resolution
data_pipeline/     profiling, preprocessing, WITSML parsing, database loading
ml/                lithology · anomaly · similarity + registry and metrics
backend/           FastAPI app, SQLAlchemy models, repositories, services
frontend/          React + TypeScript + Tailwind + Leaflet + Recharts
docker/            Postgres (PostGIS + TimescaleDB + pgvector), backend, frontend images
docs/              architecture, data pipeline, ML, API, setup, demo, deployment, model card, assumptions
artifacts/         profiling reports and the model registry
CHECKLIST.md       build progress
```

## Configuration

No magic numbers in code. Thresholds, analogue weights, top-K, rolling windows, replay
speed, alert cooldown and dataset paths all live in `config/default.yaml`, overridable per
environment and by `NWIS__SECTION__KEY` environment variables.

## Tests

```bash
python -m pytest backend/tests -q      # 86 tests
cd frontend && npm run test            # 30 tests
```

Covers the honesty contracts (no invented probability, no invented position, no silently
reduced evidence), scoring maths, unit conversion, well-level split integrity, data-quality
flagging and the API contract.
