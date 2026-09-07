# Architecture

NWIS is a **multi-model intelligence architecture**, not one model trained on two datasets.
Each source answers a different question, and the risk engine combines them.

| Source | Question it answers | Component |
|---|---|---|
| FORCE 2020 well logs | *What rock is this?* | lithology model, geology embeddings |
| Volve WITSML telemetry | *Is the rig behaving normally?* | anomaly detector, replay engine |
| Sodir well reports | *What happened here before?* | OCR/NLP institutional memory |
| NPD factpages | *Where is this well?* | geospatial layer |
| Combination | *Should the engineer be concerned?* | analogue engine + risk engine |

---

## Layers

```
┌─────────────────────────────────────────────────────────────────┐
│ 01  DATA SOURCES                                                │
│     FORCE 2020 · Volve WITSML · Sodir documents · NPD factpages │
└───────────────────────────┬─────────────────────────────────────┘
                            ↓
┌─────────────────────────────────────────────────────────────────┐
│ 02  INGESTION            data_pipeline/                         │
│     profile → validate → prepare → normalise → load             │
│     OCR · NLP extraction · unit conversion · CRS transform      │
└───────────────────────────┬─────────────────────────────────────┘
                            ↓
┌─────────────────────────────────────────────────────────────────┐
│ 03  KNOWLEDGE & DATA LAYER      16 tables                       │
│     PostgreSQL · PostGIS · TimescaleDB · pgvector               │
│     wells · trajectories · formations · logs · telemetry ·      │
│     events · mitigations · documents · chunks · embeddings ·    │
│     risk · alerts · engineer_actions · model_versions           │
└───────────────────────────┬─────────────────────────────────────┘
                            ↓
┌─────────────────────────────────────────────────────────────────┐
│ 04  INTELLIGENCE         ml/ + backend/app/services/            │
│     lithology model · anomaly detector · analogue engine ·      │
│     cross-well correlation · risk engine · mitigation memory    │
└───────────────────────────┬─────────────────────────────────────┘
                            ↓
┌─────────────────────────────────────────────────────────────────┐
│ 05  DECISION SUPPORT     FastAPI REST + WebSocket               │
│     map · events · explainable alerts · evidence · model cards  │
└───────────────────────────┬─────────────────────────────────────┘
                            ↓
┌─────────────────────────────────────────────────────────────────┐
│ 06  ENGINEER             React dashboard                        │
│     reviews evidence → acts → outcome recorded                  │
└───────────────────────────┬─────────────────────────────────────┘
                            ↓
                  FEEDBACK → engineer_actions → future training
```

---

## Why the analogue engine is the core

Ranking offset wells by distance is trivial and mostly unhelpful: the nearest well may have
been drilled through different rock to a different depth. NWIS ranks by **context**, with
weights in `config/default.yaml`:

| Dimension | How it is computed | Default weight |
|---|---:|---:|
| Geology | cosine similarity of 32-dimension petrophysical segment vectors | 0.30 |
| Formation | overlap of stratigraphy over the depth window | 0.20 |
| Drilling behaviour | similarity of operating envelopes on active rows | 0.20 |
| Depth | closeness of the compared interval | 0.15 |
| Geography | distance decay from the active well | 0.15 |

Geography is deliberately a minority contributor. Measured example from the running system:
querying `15/9-13` at 2500 m ranks `16/10-1` — **36 km away** — in the top five, because its
geology similarity is 0.9873 while its geography score is only 0.2372.

**Honest degradation.** A dimension that cannot be computed for a candidate is dropped and
the remaining weights are renormalised. The API returns `dimensions_used` and
`dimensions_unavailable` on every match, so a score is never quietly built from fewer
signals than the engineer assumes. A Volve well has telemetry but no wireline logs, so its
matches legitimately report `geology: unavailable`.

### Embeddings are interpretable by design

Each of the 32 dimensions is a named statistic (`GR_mean`, `RHOB_std`, `NPHI_median`, …)
rather than a learned latent. A match can therefore be explained in terms of the curves that
drove it. A learned ranking model would likely score better, but nothing about NWIS is
useful if an engineer cannot see why two wells were called similar. The registry records
this trade-off as a limitation, with engineer feedback as the path to a learned ranker.

---

## Why risk is an indicator, not a prediction

`risk.mode: auto` counts categorised historical events in the database at evaluation time.
Below `risk.min_labelled_events_for_supervised`, the engine runs as **`hybrid_indicator`**:

```
score = Σ(componentᵢ.value × componentᵢ.weight) / Σ(componentᵢ.weight)

  anomaly              Isolation Forest on live telemetry features   0.40
  historical_evidence  proximity × similarity of analogue records    0.40
  rules                deterministic bands on current measurements   0.20
```

and `probability` is `null`. Not zero — null. Zero would read as "no risk"; null reads as
"no calibrated model produced this". Only a genuinely supervised, calibrated model may
populate that field.

The rules component exists so that an obviously dangerous reading is never masked by a model
that finds it statistically unremarkable. Bands are in configuration and auditable.

---

## Storage: capability-aware, not capability-dependent

The schema targets PostgreSQL with PostGIS, TimescaleDB and pgvector. Spatial and vector
columns are dialect-adaptive (`backend/app/models/types.py`), and the backend detects at
startup what the connected database can actually do, reporting it at `/api/status`.

| Concern | Postgres | Fallback |
|---|---|---|
| Nearby wells | PostGIS `ST_DWithin` / `ST_Distance` | haversine in SQL |
| Vector search | pgvector cosine | NumPy cosine |
| Telemetry | TimescaleDB hypertable | indexed table |

Latitude and longitude are plain float columns in **both** backends, so no record exists in
one and not the other. The extensions make queries exact and fast; they are never a data
dependency. This is what lets the prototype run on a laptop while targeting the real stack.

---

## Provenance as a first-class concern

Almost every derived table carries `source_dataset` and `source_reference`, and extracted
records additionally carry `extraction_confidence`, `extraction_method`, page number and the
original text.

`depth_source` is the clearest example. Volve WITSML `message` objects publish the wellbore
total depth on every record rather than the depth of the remark, so event depths are
recovered by joining to telemetry on timestamp. That substitution is marked
`telemetry_time_join` and surfaced in the UI, because an engineer weighing evidence needs to
know a depth was reconstructed rather than recorded.

---

## Request paths

**Analogue ranking** — `GET /api/wells/{id}/analogues?depth_m=`
```
route → analogue service → segment embedding for query depth
                         → candidate segments (pgvector / NumPy)
                         → formation overlap, depth proximity,
                           behaviour profile, geographic decay
                         → renormalise over available dimensions
                         → ranked matches + per-component explanation
```

**Live telemetry** — `WS /ws/telemetry/{well_id}`
```
replay service (server-owned state)
   → reads stored rows in timestamp order
   → emits one message per row at cadence / speed
   → frontend renders; it never advances a clock of its own
```

Every streamed sample is a stored database row with its original timestamp. The replay
engine exists because eRTMAC access is unavailable, and it substitutes **real recorded data
played back**, not generated signals.

---

## Component map

| Path | Responsibility |
|---|---|
| `nwis_common/` | configuration, structured logging, path resolution |
| `data_pipeline/force/` | profiling, cleaning, feature building, well-level splits |
| `data_pipeline/volve/` | WITSML parsing, channel mapping, unit conversion, activity states |
| `data_pipeline/documents/` | OCR, NLP extraction, document ingestion |
| `data_pipeline/load_*.py` | database loading |
| `ml/lithology/`, `ml/anomaly/`, `ml/similarity/` | training and embedding pipelines |
| `ml/common/` | model registry and metric computation |
| `backend/app/models/` | SQLAlchemy schema and dialect-adaptive types |
| `backend/app/repositories/` | queries, including server-side geospatial search |
| `backend/app/services/` | analogue engine, risk engine, replay engine |
| `backend/app/api/routes/` | REST and WebSocket endpoints |
| `frontend/src/` | React dashboard; all data via `lib/api.ts` |
