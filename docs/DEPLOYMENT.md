# Deployment

NWIS ships as three containers: PostgreSQL (with PostGIS, TimescaleDB and pgvector), the
FastAPI backend, and an nginx-served React bundle that proxies the API on the same origin.

---

## 1. Configure

```bash
cp .env.example .env
```

Set at minimum:

```bash
NWIS_ENV=production
POSTGRES_USER=nwis
POSTGRES_PASSWORD=<a real secret>      # startup fails on a placeholder
POSTGRES_DB=nwis
```

`POSTGRES_PASSWORD` has **no default** in `docker-compose.yml`. Compose refuses to start
without it, rather than quietly using a well-known password.

## 2. Build the knowledge base

Models and processed data are build artefacts, not part of the image. Produce them once:

```bash
python scripts/bootstrap.py
```

This runs every stage in dependency order and skips work already done. Rough costs on a
laptop with a GPU:

| Stage | Time |
|---|---|
| Download (~325 MB) | ~30 s |
| FORCE profile + prepare | ~2 min |
| Volve profile + prepare | ~1 min |
| Lithology training | 3–5 min |
| Anomaly training | ~1 s |
| Embeddings | ~30 s |
| Database load + predictions | ~2 min |
| Document OCR (optional, slow) | ~25 min per report |

Use `--skip-documents` for a fast first deployment, `--dry-run` to preview, `--force` to
rebuild everything.

## 3. Start

```bash
docker compose up --build
```

- Frontend: `http://localhost:5173`
- API and OpenAPI docs: proxied at `/api` and `/docs`
- Health: `/health`

The backend waits for a healthy database before starting; the frontend waits for a healthy
backend.

## 4. Load data into the deployed database

The containers mount `./artifacts` and `./data/processed` read-only. With the stack up,
point the loaders at the containerised database:

```bash
export DATABASE_URL='postgresql+psycopg://nwis:<secret>@localhost:5432/nwis'
python -m data_pipeline.load_database --reset
python -m data_pipeline.load_embeddings
python -m data_pipeline.load_anomaly_scores
python -m ml.lithology.predict
```

Or apply the migration first if you prefer explicit schema management:

```bash
cd backend && python -m alembic -c alembic.ini upgrade head
```

The migration is dialect-aware: on PostgreSQL it creates the extensions, converts
`telemetry_samples` to a TimescaleDB hypertable, and adds the PostGIS GIST and pgvector
ivfflat indexes.

---

## What production refuses to do

`backend/app/core/startup.py` runs on boot. In production these are **fatal**, because each
one produces a system that looks healthy but is wrong:

| Condition | Why it is fatal |
|---|---|
| Running on the local fallback database | PostGIS, TimescaleDB and pgvector would be silently unused |
| A placeholder password in `DATABASE_URL` | a well-known credential on a reachable database |
| A SQLite URL | not a production datastore |
| `cors_origins: ["*"]` | unsafe with credentials enabled |

In development the same checks log warnings and never block.

Missing models are always warnings, never fatal: the affected endpoints report that a model
is unavailable, which is the designed behaviour.

---

## Security posture

- **Secrets** come from the environment only. `.env` is git-ignored; `config/*.yaml`
  contains placeholders that startup validation rejects.
- **No CORS in production.** nginx proxies `/api` and `/ws`, so the browser makes no
  cross-origin request. `config/production.yaml` ships an empty origin list.
- **Non-root container.** The backend image runs as uid 10001.
- **Sanitised errors.** Database failures return a 503 with a fixed message; the detail is
  logged, never returned.
- **Redacted logs.** The structured logger drops any field named `password`, `token`,
  `secret`, `api_key`, `authorization` or `database_url`.
- **Parameterised queries** throughout; no string-built SQL.
- **Security headers** set by nginx: `X-Content-Type-Options`, `X-Frame-Options`,
  `Referrer-Policy`, `Permissions-Policy`.
- **No third-party CDN.** Scripts, styles and fonts are bundled and served from the same
  origin. Map tiles are the one external request, and the provider is configurable via
  `VITE_MAP_TILE_URL`.
- **No external AI calls.** Every model runs locally; there is no API key anywhere. This is
  what makes an on-premise deployment viable for operator data.

### Before exposing NWIS beyond a trusted network

The prototype has **no authentication**. It is scoped as a decision-support tool for an
internal network. Before wider exposure, add:

- authentication and authorisation (`engineer_actions` already records a name; it is not
  verified)
- TLS termination, and `wss://` for the telemetry socket (the client already selects `wss`
  automatically when the page is served over HTTPS)
- rate limiting on the analogue and risk endpoints, which are the expensive ones
- an audit trail for engineer actions

---

## Scaling notes

- **The backend is stateless except for replay.** Replay sessions live in process memory,
  so with more than one worker a client could reach a worker that does not hold its
  session. Run a single backend replica while replay is in use, or move replay state to
  Redis first.
- **Telemetry is the table that grows.** On PostgreSQL it is a TimescaleDB hypertable;
  add a retention or compression policy for continuous ingestion.
- **Analogue ranking is the expensive query.** It scores every candidate well. With
  pgvector present, restrict the candidate pool with an approximate nearest-neighbour
  pre-filter (`analogue.candidate_pool` already exists for this).
- **Models are read-only files.** Mount them, or bake them into an image tagged with the
  model version from the registry.

## Operations

| Task | Command |
|---|---|
| Health | `curl localhost:8000/health` |
| What is loaded, and which storage features are live | `curl localhost:8000/api/status` |
| Registered models and their measured metrics | `curl localhost:8000/api/models` |
| Retrain and re-select | `python -m ml.lithology.train` |
| Re-decide the served model without retraining | `python -m ml.lithology.train --select-only` |
| Rebuild everything | `python scripts/bootstrap.py --force` |

`/api/status` is the first place to look when something seems wrong: it reports whether
PostGIS, TimescaleDB and pgvector are actually in use, how many wells lack a position, and
any active warnings.
