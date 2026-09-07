# Setup

## Requirements

- Python 3.12+
- Node 20+
- Optional: Docker, for the real PostgreSQL + PostGIS + TimescaleDB + pgvector stack
- Optional: an NVIDIA GPU (XGBoost uses it automatically; CPU otherwise)
- Optional: Tesseract, for document ingestion

## 1. Install

```bash
pip install -r backend/requirements.txt
cd frontend && npm install && cd ..
cp .env.example .env
```

## 2. Acquire data (~325 MB)

```bash
python scripts/download_datasets.py
```

Downloads FORCE 2020, the Volve WITSML mirror and the NPD wellbore tables into `data/raw/`.
Re-running skips files already present; `--force` re-downloads. Restrict with
`--only force|volve|npd`.

## 3. Build the pipelines

```bash
python -m data_pipeline.force.profile      # discovers the schema; assumes nothing
python -m data_pipeline.force.prepare      # 1.17M rows -> 90 features + well-level split
python -m data_pipeline.volve.profile
python -m data_pipeline.volve.prepare      # WITSML -> 86,800 normalised telemetry rows
```

Profiling reports land in `artifacts/profiling/`. Read them before trusting any pipeline
output: they record missingness, class balance, duplicate depths and constant channels.

## 4. Train models

```bash
python -m ml.lithology.train               # ~5 min; XGBoost uses the GPU when present
python -m ml.anomaly.train                 # ~1 s
python -m ml.similarity.build_embeddings   # ~30 s
```

`--smoke` runs the lithology trainer end to end on a few wells for a fast check. Smoke runs
are deliberately **not** registered, so they can never be served as real metrics.
`--select-only` recomputes model selection from the registry without retraining.

## 5. Load the database

```bash
python -m data_pipeline.load_database --reset
python -m data_pipeline.load_embeddings
```

## 6. Run

```bash
uvicorn backend.app.main:app --reload      # http://localhost:8000  (docs at /docs)
cd frontend && npm run dev                 # http://localhost:5173
```

## Docker

```bash
docker compose up --build
```

Requires `POSTGRES_PASSWORD` in `.env`. The compose backend sets
`NWIS__DATABASE__ALLOW_FALLBACK=false`, so if Postgres is unreachable it fails loudly
rather than quietly degrading.

## Storage backends

NWIS targets PostgreSQL with PostGIS, TimescaleDB and pgvector. When they are unavailable it
uses equivalent implementations and reports which are active at `/api/status`:

| Concern | Postgres | Fallback |
|---|---|---|
| Nearby wells | PostGIS `ST_DWithin` | SQL haversine — a real query, same kilometres |
| Vector search | pgvector cosine | NumPy cosine |
| Telemetry | TimescaleDB hypertable | indexed table |

Latitude and longitude are plain float columns in both, so no record is reachable in one
backend and missing in the other.

## Configuration

`config/default.yaml` is the base, `config/<environment>.yaml` overlays it, and environment
variables override both using `NWIS__SECTION__KEY`:

```bash
NWIS__ANALOGUE__TOP_K=8
NWIS__ALERTS__COOLDOWN_SECONDS=600
NWIS__LITHOLOGY_MODEL__DEVICE=cpu
```

## Retraining

Every trainer writes a versioned registry entry recording dataset, features,
hyperparameters, measured metrics, training duration and known limitations. Re-run a trainer
and the new version is registered; `--select-only` re-decides which model is served.

Engineer feedback captured in the UI is stored in `engineer_actions` and marked
`available_for_training`. **Nothing retrains automatically** — feedback accumulates for a
deliberate future retraining run.
