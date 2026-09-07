# Running the Docker stack

This brings up the real target architecture: PostgreSQL with **PostGIS**, **TimescaleDB**
and **pgvector**, the FastAPI backend, and the SPA behind nginx on one origin.

Without it NWIS runs on the documented local fallback (SQLite + SQL haversine + NumPy
cosine) and says so in the header badge and in `/api/status`. That fallback is honest and
fully functional — the Docker stack is what proves the production storage paths.

---

## 1 · Prerequisites (needs an administrator)

Docker Desktop on **Windows 11 Home** requires WSL2; the Hyper-V backend is not available
on Home editions. These steps need elevation, which is why they are not automated here.

Open **PowerShell as Administrator** (right-click → Run as administrator) and run:

```powershell
wsl --install
```

Then **reboot**. After the reboot, confirm:

```powershell
wsl --status
```

Install Docker Desktop, still elevated:

```powershell
winget install --id Docker.DockerDesktop --accept-source-agreements --accept-package-agreements
```

Launch Docker Desktop once, let it finish first-run setup, and confirm the engine is up:

```bash
docker version
docker compose version
```

**Disk.** The images are large — Postgres with three extensions, plus a Python image
carrying torch, spaCy, XGBoost and scikit-learn. Budget **10–12 GB free**. Check first:

```powershell
Get-PSDrive C | Select-Object @{n='FreeGB';e={[math]::Round($_.Free/1GB,1)}}
```

If C: is tight, move Docker's data root to another drive in
Docker Desktop → Settings → Resources → Advanced → Disk image location.

---

## 2 · Secrets

`.env` is gitignored and must exist. If it does not:

```bash
python -c "import secrets; print('POSTGRES_PASSWORD=' + secrets.token_urlsafe(24))"
```

Put that in `.env` alongside `POSTGRES_USER`, `POSTGRES_DB` and `DATABASE_URL`.

**`change-me` will not work.** The compose services run with `NWIS_ENV=production`, and
`backend/app/core/startup.py` refuses to start when the database URL contains a known
placeholder password. That is deliberate: a production instance running on a default
credential looks perfectly healthy from the outside.

Leave `NWIS_ENV` **unset** in `.env`. Compose defaults its own services to production,
while the local dev server stays in development where the SQLite fallback only warns.

---

## 3 · Bring it up

```bash
docker compose up --build
```

Four services start in dependency order:

| Service | Role |
|---|---|
| `database` | Postgres 16 + PostGIS + TimescaleDB + pgvector; extensions created on first init |
| `loader` | **one-shot** — creates the schema, loads the knowledge base, then exits |
| `backend` | FastAPI; waits for `loader` to complete successfully |
| `frontend` | nginx serving the built SPA, proxying `/api` and `/ws` on the same origin |

Then open **http://localhost:5173**.

The header badge should now read **postgres stack** rather than *local storage backend*.
That single badge is the proof the stack is live — confirm it before demoing.

### Why the loader exists

Without it, `docker compose up` produced a running application over an **empty database**.
The loader runs, in this order:

```
load_database --reset      # schema + wells, geology, telemetry, events, model registry
alembic upgrade head       # hypertable + GIST + ivfflat  (best-effort)
load_embeddings            # 3,620 segment vectors into pgvector
load_anomaly_scores        # 8,130 scores
ml.lithology.predict       # 18,842 predictions
```

The order is not cosmetic. `load_database --reset` calls `drop_all`, so running it *after*
the migration would drop the hypertable and vector indexes the migration had just created.

The migration step is **best-effort on purpose**. The hypertable and the vector indexes are
performance features, not correctness ones. If TimescaleDB rejects the conversion, the
stack still serves correct results and `/api/status` reports which backends are genuinely
active instead of claiming all three.

---

## 4 · Verify

```bash
curl -s http://localhost:8000/api/status
```

Look at `database`: `postgis`, `timescaledb` and `pgvector` should be `true` and
`fallback_active` should be `false`. `counts.wells` should be **101**.

Check the seed actually ran:

```bash
docker compose logs loader
```

---

## 5 · Documents are not ingested in the container

OCR of the completion reports is a slow, one-off batch job and is **not** part of
`docker compose up`. Passage search will be empty in a fresh stack. To populate it:

```bash
docker compose exec backend python -m data_pipeline.documents.ingest --limit 2 --max-pages 30
docker compose exec backend python -m data_pipeline.documents.embed
```

Budget roughly **14 seconds per page**; the page-text cache means it is paid once.

---

## 6 · Teardown

```bash
docker compose down          # stop, keep the database volume
docker compose down -v       # stop and delete the data, forcing a full reseed next time
```

---

## Known limitation

This stack has been authored and unit-tested for structure, but on this machine it has
**never been executed** — Docker Desktop requires WSL2, whose installation raises a UAC
prompt that a non-interactive session cannot approve. The defects found by inspection are
fixed and covered by tests in `backend/tests/test_deployment.py`, but "it builds and runs"
is a claim that needs one real `docker compose up` behind it before anyone should make it.
