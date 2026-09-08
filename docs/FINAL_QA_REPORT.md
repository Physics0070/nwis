# NWIS — Final QA Report

Date: 2026-09-08 · macOS · Python 3.12 · Node 25 · Docker 29.6.2

The repository was rebuilt from a clean checkout: dependencies installed, FORCE 2020, Volve
WITSML and Sodir data downloaded, every pipeline stage run, all three models trained, the
database loaded, reports OCR'd and embedded. The running application was then driven
through the full judge journey in a browser — first on the SQLite fallback, then again on
the Docker Postgres stack.

**Nothing below was read off the source code.** Every figure is measured output.

---

## Verdict

**READY WITH MINOR ISSUES.**

Every core NWIS feature works, is connected to real data, and is reachable in the demo. Two
features are marked PARTIAL, and in both cases the limit is *corpus coverage*, not
capability — a function of OCR time, and stated in the interface on every search.

Five defects found in this audit were serious enough that the demo would have misled a
judge. All five are fixed and four carry regression tests.

---

## Test results

| Suite | Result |
|---|---|
| Backend (`pytest backend/tests -q`) | **86 passed**, 0 failed |
| Frontend (`npm run test`) | **30 passed**, 0 failed |
| TypeScript (`tsc -b --noEmit`) | clean |
| Lint (`oxlint src`) | 0 errors (warnings only, all pre-existing patterns) |
| Production build (`npm run build`) | ✓ built, 846 kB / 249 kB gzipped |
| API sweep (`curl`) | every endpoint and every error path exercised |
| Browser (Chromium) | full journey, **0 JavaScript console errors** |
| Docker | database healthy, hypertable created, `fallback_active: false` |
| E2E simulator | replay, seek, all speeds, investigate/pause/resume, action persisted |

Backend tests grew from 66 to 86 during this audit; frontend from 28 to 30.

---

## Defects found and fixed

Ranked by how badly each would have misled a judge.

### 1 · The risk engine ignored the depth it was asked about — **P0**

`GET /api/wells/{id}/risk?depth_m=…` resolved telemetry as *the most recent stored sample*
and the anomaly score as *the most recent stored score*. The historical half of every
assessment followed the bit; the measured half was frozen at the end of the recording.

Fixed: both are resolved at the queried depth, within a new configured tolerance
`risk.telemetry_match_tolerance_m`. Beyond it, the components are reported unavailable and
the reason is stated, rather than borrowed from another part of the hole. Measured after
the fix on `NO 15/9-F-4`:

| Depth | Components | Note |
|---|---|---|
| 300 m | anomaly 0.119, historical 0.793, rules 0.0 | telemetry 0 m from the query |
| 1,000 m | historical 0.419 only | *"nearest stored sample is 251 m away"* |
| 2,000 m | none — `evaluated: false` | absence of signal, not low risk |
| 2,500 m | anomaly 0.380, rules 0.200 | telemetry 0 m from the query |

Two regression tests.

### 2 · Routine operations were counted as historical risk evidence — **P0**

184 of 243 stored drilling events are uncategorised Volve WITSML remarks: *"Toolbox Talk
Prior to Rig Up Tubing Equipment"*, *"Prep move to F14"*, *"Stop monitoring for F-7"*. The
engine counted every one, producing a **MEDIUM indicator "supported by 65 historical
records"** made entirely of workover housekeeping — a fabricated risk signal assembled from
genuine rows, which is the most dangerous kind of false claim this project could make.

Fixed: only categorised events count as risk evidence. That is the line the extractor draws
between "something went wrong here" and "something happened here". Uncategorised remarks
stay visible on the borehole track; they no longer raise an indicator. After the fix the
same well at 0 m reports INFO 0.20 from anomaly and rules only. Regression test.

### 3 · No alert was ever raised, so half the chain was unreachable — **P0**

The engine only stores an assessment and raises an alert when called with `persist=true`,
and nothing in the frontend ever did. The Alerts inbox was permanently empty, `/alerts/{id}`
unreachable, and "record your decision" always failed with *"No alert has been raised for
this well yet"*.

Fixed: the live session persists what it evaluates, so the alert an engineer acts on is the
assessment they were shown. Cooldown and depth deduplication keep it from becoming spam —
observed `occurrence_count: 5` on one alert rather than five alerts. Verified end to end on
Postgres: *"Recorded as 'accepted' against alert #1 (MEDIUM risk indicator at 400 m MD)"*,
stored as engineer action row 1, alert moved to `reviewed`.

### 4 · Evidence at exactly the bit's depth scored as the weakest — **P0**

The proximity term read `abs(e.distance_from_bit_m or lookahead)`. An offset of `0.0` is
falsy in Python, so a historical event recorded at *precisely* the depth the bit had
reached — the strongest possible evidence — was replaced with the full 150 m window and
scored as the weakest.

Found on real data: `NO 15/9-F-4` reaches 2,368.4 m and `16/7-5` records a fishing
operation at 2,368 m. That exact match returned `historical_evidence: 0.000`. After the
fix: **0.739**. The term is now an extracted, unit-tested function.

### 5 · Institutional memory could not be rebuilt from a clean checkout — **P0**

Document ingestion reads `data/raw/npd/wellbore_document.csv` to discover which reports
exist. No script downloaded that file and no configuration named it, so on a fresh clone
ingestion failed immediately — taking OCR, passages, embeddings, report search **and every
mitigation** with it. Mitigations are produced only by document ingestion; the WITSML
pipeline creates events but never mitigations.

Fixed: declared in configuration, fetched by `scripts/download_datasets.py`, verified
against the live Sodir endpoint.

### 6 · The TimescaleDB hypertable could never be created — **P1**

`create_hypertable` failed on every Docker run with *"cannot create a unique index without
the column 'recorded_at' (used in partitioning)"*. TimescaleDB requires the partitioning
column in every unique index; `telemetry_samples` had a primary key of `id` alone. The
compose loader treats the migration as best-effort, so it failed quietly and the stack ran
as an ordinary indexed table.

Fixed: the migration widens the key to `(id, recorded_at)` on PostgreSQL only — the
standard TimescaleDB pattern. Measured after: 1 hypertable, 3 chunks, all 86,800 rows
migrated, `timeseries_storage: timescaledb_hypertable`.

### 7 · Mitigation provenance was dropped before it reached the UI — **P1**

The investigation drawer renders `source_reference` on each mitigation, but the risk engine
built mitigation dictionaries from three fields only. The source line was therefore
silently absent on every mitigation ever shown — "evidence you cannot trace", which this
project explicitly forbids. Fixed; the citation now reads *"source:
134_02_16_7_5_Final_Well_Report p.30"*.

### 8 · The anomaly had no "why" — **P1**

`anomaly.attribution_top_n` was configured but unused, and `contributing_features` was
written as an empty list for all 8,130 scores. The CHECKLIST claim that attribution was
"wired into the risk payload" was not true.

Fixed: attribution is computed per row at scoring time and shown as *"What made this sample
unusual"*. It is labelled for what it is — a deviation score against the active-row median,
**not** an Isolation Forest attribution, because the forest exposes none — and the model
card carries that limitation.

An intermediate version used the interquartile range as the scale. Measured, it was
degenerate: rolling-slope features are zero for most active rows, so their IQR is ~0 and
every non-zero value divided out to hundreds of sigma, making the same handful of slope
features "the explanation" for every sample. Switched to the standard deviation, which the
spikes themselves inflate. Measured after: **105 distinct top-ranked features** across
8,130 scores, physically varied (pit gain/loss, standpipe, hookload, flow, torque, WOB).

### 9 · The registry recorded a GPU that does not exist — **P1**

The Models page showed `device: cuda` for the lithology model on a machine with no GPU.
XGBoost does not raise when CUDA is absent — it warns and trains on the CPU anyway — so the
"does a GPU work?" probe read a successful fit as proof of one.

Fixed: the probe asks the fitted booster which device it actually resolved to. The model was
**retrained** so the registry reflects the run that happened rather than being edited by
hand. Now records `device: cpu`.

### 10 · Citations leaked absolute filesystem paths — **P1**

The investigation drawer displayed
`/Users/…/Downloads/nwis-main/data/raw/volve/witsml/…/26.xml` as provenance. That is a fact
about the machine that ran the pipeline, not reproducible evidence. Fixed with a
`citation_path` helper; citations are now recorded relative to the repository root.

### 11 · The stream claimed to reconnect and did not — **P1**

The simulator told the engineer *"the stream will reconnect automatically"*. The hook only
opened a socket when the well changed. Fixed with exponential backoff, buffered samples kept
across a reconnect, retries cancelled on deliberate teardown. Two regression tests, plus a
live test: the API was killed mid-replay, the UI showed `socket: closed` with the banner,
and recovered to `socket: open` on its own when the API returned.

### 12 · The frontend carried its own copies of backend thresholds — **P1**

`DEPTH_CONTEXT_STEP_M`, `LITHOLOGY_MATCH_TOLERANCE_M`, `PAGE_SIZE` and the replay speed list
were hardcoded across three pages, free to drift from the values the backend uses. Fixed: a
`ui:` section in configuration, served through `/api/status.config`. No threshold constant
remains in `frontend/src`. A test asserts every offered speed lies inside the replay
engine's configured bounds.

### 13 · An actionable 503 looked like a hang — **P2**

Searching before the index was built returned 503 with the command that fixes it, but
react-query retried three times behind a backoff, so the page said *"Searching report
passages…"* for seven seconds and then showed the same message. Fixed: surfaced at once.

### 14 · The first report search took 14.5 seconds — **P2**

The sentence-transformer loaded lazily on the first request. Fourteen seconds of spinner on
the institutional-memory demo reads as a broken feature. Fixed: warmed in a background
thread at startup. Measured after: 1.2 s cold, 0.4–0.7 s warm.

---

## Features added to close a gap

Nothing was invented; each of these exposes data or an endpoint that already existed.

- **Historical risk radar** (`DepthRadar.tsx`) — the axis is the risk engine's own
  `risk.lookahead_m`, served from the API; each marker sits at its real signed depth
  offset; the headline names the nearest event the bit has **not yet reached**. Verified
  live: *"Historical risk interval 14 m ahead — operational_remark recorded in NO 15/9-F-4
  at 39 m"*. Where nothing lies ahead it says so, and calls it an absence of records rather
  than an all-clear. No distance is hardcoded.
- **Investigate pauses the replay**, and closing resumes it — but only if the drawer is what
  paused it, so a deliberate pause is not undone.
- **Report search inside the investigation drawer** — the same semantic search, seeded with
  the event's own recorded words, with page citations. Institutional memory is now part of
  the risk flow instead of a separate page.
- **A scrub control on the replay bar**, wired to the `/seek` endpoint that had existed all
  along with no UI reaching it. `NO 15/9-F-4` sits at 0 m until sample 14,952 of 59,806;
  without this a demo waits the recording out.
- **Analogue dimension disclosure in the simulator** — *"scored on depth, geography · not
  available: geology, formation, drilling_behaviour"*.

---

## The complete chain, verified on the Postgres stack

Replaying `NO 15/9-F-7`, seeking to sample 8,167, bit at 500 m:

```
ACTIVE WELL        NO 15/9-F-7 (Volve WITSML replay)
  ↓
NEARBY WELLS       server-side PostGIS query
  ↓
ANALOGUE ENGINE    15/9-17 score 0.898 — scored on depth, geography;
                   geology, formation, drilling_behaviour unavailable and said so
  ↓
CURRENT DEPTH      500 m, quantised to the served 25 m step
  ↓
TELEMETRY          the sample at that depth, over the WebSocket
  ↓
ANOMALY            0.208, top deviations: weight_on_bit_kn 1.53σ, flow_out_lpm …
  ↓
HISTORICAL RISK    16/7-5 overpull, 41 m behind the bit
  ↓
ALERT              LOW 0.345 — no probability, by design
  ↓
HISTORICAL EVENT   "the 18-3/4 inch wellhead was latched and pull tested with
                    30 kips overpull, the 20 inch shoe was located at 458.66 m"
  ↓
MITIGATION         cemented · outcome: No data available
  ↓
SOURCE             134_02_16_7_5_Final_Well_Report p.30 · depth via document_text
  ↓
ENGINEER ACTION    "Recorded as 'accepted' against alert #1" — persisted, alert reviewed
  ↓
INSTITUTIONAL      report passages retrieved for the same event, including
MEMORY             16/7-4 page 8 on overpull during a wiper trip
```

Every transition used the current well and depth. Changing the active well resets context;
changing depth by replay or seek re-queries risk, analogues, evidence and the radar;
changing speed or replay state affects pacing only.

---

## Storage backends

Both were exercised.

| | SQLite fallback | Docker Postgres |
|---|---|---|
| `fallback_active` | true, stated in the header badge | **false** |
| Proximity | SQL haversine | PostGIS + GIST |
| Vectors | NumPy cosine | pgvector + ivfflat |
| Time series | indexed table | TimescaleDB hypertable, 3 chunks |
| Full journey | pass | pass |

Extensions measured in the container: postgis 3.6.4, timescaledb 2.29.2, vector 0.8.6.

---

## Simulator hardening

| Control | Result |
|---|---|
| Start / Pause / Resume / Stop | `stopped → running → paused → running → stopped` |
| Reset | returns to index 0, clears depth context and investigation |
| Seek | works, newly exposed |
| 0.5× | clamped to the configured minimum 1.0× — correct |
| 1× / 2× / 5× / 10× / 60× / 3600× | accepted exactly |
| 100000× | clamped to 3600× |
| Synchronisation across speed and seek changes | depth, bit, formations, events, radar, analogue, anomaly, risk and telemetry all follow |

No `setTimeout` pacing hack and no fabricated value was introduced. Replay position is
server-owned. One consequence worth knowing before a demo: **restarting the API resets the
replay to sample 0**, because replay state lives in the server process. The socket
reconnects on its own; the position does not survive. That is correct behaviour, not a bug.

---

## Error and empty states

Every one returns a specific sentence. No `undefined`, `NaN`, `Infinity` or stand-in zero
was observed anywhere.

| Request | Response |
|---|---|
| unknown well | 404, names the well |
| well without telemetry | 409, *"15/9-13 has no telemetry in the knowledge base."* |
| unknown alert / model | 404, *"has not been trained. No metrics exist for it."* |
| blank search query | 422 — a client error, not an outage |
| unbuilt search index | 503 **with the command that builds it** |
| depth with no stratigraphy | 404, names the depth |
| risk with nothing computable | `evaluated: false`; the UI refuses to render the score |

---

## Claims discipline

Re-verified against Oil India's own pages during this audit; all three URLs still return
200 and still contain the quoted text. See `docs/OIL_FACT_CHECK.md`.

- No eRTMAC integration is claimed anywhere in the code, the UI or the documents.
- No claim that eRTMAC uses WITSML.
- No accuracy figure presented as applying to Indian wells.
- The replay is labelled **REPLAY MODE · historical Volve telemetry, not a live rig feed**.
- `probability` is `null` in every risk response, with the reason on screen.
- No failure mode is ever named from telemetry.

---

## Still partial

**Institutional memory** and **mitigation strategies**, both for the same reason: corpus
coverage.

- 8 of 388 available reports ingested — 320 pages, 410 passages, **410 of 410 embedded**.
- 59 of 243 events are categorised; **only 10 of those carry a recovered depth**, and only
  those 10 can be matched to a bit position. An event without a depth cannot be placed on
  the radar, and NWIS will not invent one.
- 15 mitigations, each citing a document and page.

Both limits are visible in the interface rather than papered over: every search prints how
much of the corpus was consulted, and an event with no recorded mitigation says so.

Extraction precision is the other half of this. Some categorised "events" are OCR'd
table-of-contents lines — *"FISHING —0.00 — 0.00"* is a rig-time summary table, not an
incident. They never reach the radar because they have no depth, but they do inflate the
event count. Tightening the extractor is the next improvement to institutional memory, and
it is a data-quality task, not a plumbing one.

---

## Top three remaining actions

1. **Ingest more reports, and the deeper pages of the ones already read.** Coverage is the
   only thing separating both PARTIAL features from COMPLETE. `--start-page` exists for
   exactly this, page text is cached, and Tesseract is now installed, so a second pass is
   cheap.
2. **Tighten event extraction so table-of-contents lines are not stored as events.** They
   are harmless today but they inflate the count and would embarrass a close reading.
3. **Run the full `docker compose up` end to end**, including the containerised loader and
   the nginx frontend. The database service, the migration and the complete data load were
   verified against the container; the backend and frontend images were not rebuilt in this
   session.
