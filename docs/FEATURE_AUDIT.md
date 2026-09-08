# NWIS — Feature Audit

Every row below was checked three ways: **does the code exist**, **does it work when run**,
and **can a judge reach it in the demo**. A feature is COMPLETE only when all three hold
*and* it is connected to real data. Backend code alone is never enough.

Method: the repository was rebuilt from a clean checkout on this machine — dependencies
installed, the FORCE 2020, Volve WITSML and Sodir datasets downloaded, every pipeline stage
run, all three models trained, the database loaded — and then the running application was
driven through the judge journey in a browser while the API was swept directly with `curl`.
Evidence below is measured output, not a reading of the source.

Environment audited: Python 3.12, Node 25, macOS. The audit ran first against the local
SQLite fallback and then against the full Docker Postgres stack, which reports
**`fallback_active: false`** with PostGIS, TimescaleDB and pgvector all active.

Rebuilt state, measured from the database and `/api/status`:

| | Count |
|---|---|
| Wells | 101 (98 FORCE + 3 Volve), 101 with a real surveyed position |
| Telemetry samples | 86,800 across 3 Volve wellbores, in a TimescaleDB hypertable (3 chunks) |
| Segment embeddings | 3,620 |
| Anomaly scores | 8,130, each with contributing features |
| Lithology predictions | 18,842 |
| Formation intervals | 1,501 |
| Drilling events | 243 — 184 Volve operational remarks + 59 categorised, document-extracted |
| Categorised events with a recovered depth | 10 (these are the ones that can serve as risk evidence) |
| Mitigations | 15, each citing a document and page |
| Documents | 8 reports, 320 pages OCR'd, 410 passages, **410 of 410 embedded** |
| Models | 3 (lithology, anomaly, analogue embedding) |

---

## Summary

| Feature | Exists | Works | Demo-visible | Status | Evidence | Missing work |
|---|---|---|---|---|---|---|
| Institutional memory | yes | yes | yes | **PARTIAL** | 8 reports, 320 pages OCR'd, 410 passages, 410 of 410 embedded; reachable from **Reports** and from inside the investigation drawer | 8 of 388 available reports — coverage, not capability |
| Smart offset intelligence | yes | yes | yes | **COMPLETE** | `/api/wells/{id}/nearby` returns ascending haversine distances computed server-side; map on the Active Well page; ranking is the analogue engine, not the distance list | — |
| Contextual analogue engine | yes | yes | yes | **COMPLETE** | Live: querying `16/2-16` at 1,100 m ranks `31/2-9` **231 km away** 4th, geography component **0.0**, on geology 0.801 and formation 1.0 | on the Volve replay wells only depth, drilling behaviour and geography can be computed — now stated in the UI |
| Live–historical correlation | yes | yes | yes | **COMPLETE** | Replay advances the bit; risk, evidence and radar re-query at each depth step; labelled REPLAY MODE / "historical Volve telemetry, not a live rig feed" | — |
| Proactive risk alerts | yes | yes | yes | **COMPLETE** | Isolation Forest + historical evidence + configured rules; alerts now raised and stored from the live session; `probability` stays `null` | no named failure modes, by design and by data |
| Historical risk radar | yes | yes | yes | **COMPLETE** | Live: "Historical risk interval 14 m ahead — operational_remark recorded in NO 15/9-F-4 at 39 m", window drawn from `risk.lookahead_m` | — |
| Explainable alerts | yes | yes | yes | **COMPLETE** | Component scores with explanations, contributing features, analogue wells, evidence, mitigation, provenance — in the drawer and on `/alerts/{id}` | — |
| Mitigation strategies | yes | yes | yes | **PARTIAL** | 15 mitigations, each citing a document and page; verified live in the drawer — *16/7-5 overpull at 459 m → "cemented" → 134_02_16_7_5_Final_Well_Report p.30* | only 10 categorised events carry a depth, so only those can be matched to a bit position |
| Mitigation memory | yes | yes | yes | **COMPLETE** | Problem → action → outcome → source, now including provenance, reachable from the risk investigation flow | — |
| System-agnostic layer | yes | n/a | yes | **COMPLETE** | No integration claimed anywhere; `docs/OIL_FACT_CHECK.md` separates verified from unverified | — |

---

## 1 · Institutional memory — **PARTIAL**

| Check | Result |
|---|---|
| OCR | **works** — Tesseract when present, RapidOCR otherwise; the engine that ran is recorded per page |
| Document ingestion | **works** — Sodir PDFs downloaded, page text cached so re-extraction is free |
| Text extraction | works |
| Passage chunking | works — 1,200 characters, 150 overlap, page number retained |
| Embeddings | works — `all-MiniLM-L6-v2`, 384 dimensions, stored L2-normalised |
| Vector search | works — pgvector on Postgres, exact NumPy cosine on the fallback |
| Report search UI | works — `/reports`, with example queries |
| Historical event retrieval | works |
| Source citation | works — document title and well |
| Page citation | works — page number on every passage |
| Usable from the demo | **works, and now also from the investigation drawer** |

**What was broken and is fixed.** On a clean checkout this feature could not be built at
all: ingestion reads `data/raw/npd/wellbore_document.csv` to discover which reports exist,
and no script downloaded that file. It is now declared in configuration and fetched by
`scripts/download_datasets.py`, verified against the live Sodir endpoint.

**What remains partial.** Coverage, and only coverage. 8 reports are ingested — 320 pages
OCR'd into 410 passages, **all 410 embedded** — out of 388 reports available for the wells
in the knowledge base. The interface says so on every search: *"Searched 410 of 410 stored
passages"*. That is a statement about the corpus, not a claim about the world; do not
present report search as covering the back catalogue.

The reports ingested were chosen from data rather than at random: the analogue engine was
queried for the three replayable Volve wellbores across their depth ranges, and the wells it
actually returns — `15/9-17`, `15/9-13`, `16/7-5`, `15/9-15`, `16/7-4` — are the ones whose
reports were read. The extra OCR therefore lands where the demo looks.

Retrieval quality, measured live: investigating a `16/7-5` overpull returns a passage from a
**different** well, `16/7-4` page 8, describing overpull on a wiper trip. Shared meaning,
not shared keywords, with the page to go and read.

**One latency defect fixed.** The first search on a cold process took **14.5 s** while the
sentence-transformer loaded; every later one took about half a second. The encoder is now
warmed in a background thread at startup, so the demo never sees it. Measured after: 1.2 s
cold, 0.4–0.7 s warm.

An empty index returns **503 with the command that builds it**, not an empty result set
that would read as "no matches". The page now surfaces that immediately rather than
retrying behind a spinner.

---

## 2 · Smart offset intelligence — **COMPLETE**

| Check | Result |
|---|---|
| Nearby wells | works — `/api/wells/{id}/nearby`, distances ascending, computed in SQL |
| Geographic map | works — Leaflet, real coordinates, no well placed at a guess |
| Well selection | works |
| Analogue ranking | works — see §3 |
| Geological similarity | works — 32-dimension petrophysical segment vectors |
| Drilling similarity | works — median operating envelope over operationally-active rows only |
| Operational similarity | works where telemetry exists on both sides; reported unavailable otherwise |
| Explanation | works — every component carries its own sentence |
| Demo access | works |

Not a nearest-well search: proximity is one weighted component of five, and it is not the
largest.

---

## 3 · Contextual analogue engine — **COMPLETE**

Verified live rather than argued. Querying `16/2-16` at 1,100 m:

| Rank | Well | Score | Distance | Geology | Formation | Geography |
|---|---|---|---|---|---|---|
| 1 | 16/2-6 | 0.9516 | 3.1 km | 0.985 | 1.0 | 0.883 |
| 2 | 25/11-19 S | 0.7782 | 41.7 km | 0.868 | 1.0 | 0.188 |
| 4 | **31/2-9** | 0.7340 | **231.1 km** | 0.801 | 1.0 | **0.0** |

A well 231 km away, scoring zero on geography, ranked fourth on geology and shared
stratigraphy. That is the difference between "nearby" and "relevant", demonstrated from the
running system.

**Does changing depth change the answer?** On wells with logs, yes — the segment chosen and
the formation overlap both move. On the **Volve replay wells, no**, and the reason is data,
not a bug: those wellbores have no wireline logs and no stratigraphy, so geology and
formation cannot be computed at all and the ranking falls back on depth, drilling behaviour
and geography, which are near-constant across depth for a candidate whose logged interval
spans the whole hole. The simulator now prints exactly this — *"scored on depth,
drilling_behaviour, geography · not available: geology, formation"* — so nobody mistakes a
two-dimension match for a five-dimension one.

What *does* change with depth on the replay wells is the historical evidence and the radar,
which is the correlation the demo is actually about.

---

## 4 · Live–historical correlation — **COMPLETE**

| Check | Result |
|---|---|
| Telemetry | works — 86,800 stored samples |
| Replay | works — server-owned position; 12,123 samples on F-9 |
| WebSocket | works — `socket: open`; now reconnects with backoff, which it previously only claimed |
| Current depth | works — the stored `bit_depth_m`, never generated |
| Historical events | works |
| Analogue context | works |
| Correlation | **works, and was materially wrong before this audit** — see below |
| UI explanation | works |
| Demo flow | works |

**The defect found here was the most important one in the audit.** `GET
/wells/{id}/risk` resolved telemetry and the anomaly score as *the most recent stored row*,
ignoring `depth_m` entirely. The historical half of every assessment followed the bit while
the measured half was frozen at the end of the recording. Both are now resolved at the
queried depth within `risk.telemetry_match_tolerance_m`, and beyond that tolerance the
components are reported unavailable with the reason stated rather than borrowed from
another part of the hole. Measured after the fix, on `NO 15/9-F-4`:

| Depth | Components | Contributing features |
|---|---|---|
| 300 m | anomaly 0.119, historical 0.793, rules 0.0 | weight_on_bit_kn_long_slope, … |
| 1,000 m | historical 0.419 only — *"No telemetry within 25 m of 1000 m: the nearest stored sample is 251 m away"* | none |
| 2,000 m | none — `evaluated: false` | none |
| 2,500 m | anomaly 0.380, rules 0.200 | hookload_kn_medium_slope, … |

Labelling is correct: the top bar reads **REPLAY MODE**, the source line reads *"Volve
WITSML replay · historical Volve telemetry, not a live rig feed"*. No live OIL telemetry is
claimed anywhere.

---

## 5 · Proactive risk alerts — **COMPLETE**

| Check | Result |
|---|---|
| Anomalous telemetry | works — Isolation Forest, 121 features, 8,130 active rows, 2% contamination |
| Torque-related anomalies | works where torque varies; `surface_torque_knm_long_std` appears in real attributions |
| Other supported anomalies | works — hookload, standpipe, pit gain/loss, WOB, trip speed |
| Historical risk correlation | works |
| Current-depth relevance | **works — this is the P0 fix in §4** |
| Risk indicator | works — bounded score, configured thresholds |
| Explanation | works |
| UI alert | **works — alerts are now actually raised**, see below |
| Demo visibility | works |

**No probability is fabricated.** `probability` is `null` by design and the interface says
why. The mode is chosen by the data: `resolve_mode` counts categorised historical events
and reports `hybrid_indicator` until they exceed the configured threshold. No stuck-pipe,
kick or mud-loss probability exists anywhere in the system.

**Two defects found and fixed.**

1. **No alert was ever raised.** The engine only stores an assessment and raises an alert
   when called with `persist=true`, and nothing in the frontend ever did. The Alerts inbox
   was permanently empty, `/alerts/{id}` unreachable, and recording an engineer decision
   always failed. The live session now persists what it evaluates. Verified: alert #1,
   MEDIUM at 0 m on `NO 15/9-F-9`, `occurrence_count: 5` — the cooldown and depth
   deduplication doing their job rather than five separate alerts.

2. **Routine operations were being counted as risk evidence.** 184 of the 185 stored
   drilling events are uncategorised Volve WITSML remarks — *"Toolbox Talk Prior to Rig Up
   Tubing Equipment"*, *"Prep move to F14"*, *"Stop monitoring for F-7"*. The engine
   counted all of them, producing a MEDIUM indicator *"supported by 65 historical records"*
   made entirely of workover housekeeping: a fabricated risk signal assembled from real
   rows. Historical evidence is now restricted to events that carry a category, which is
   the line the extractor draws between "something went wrong here" and "something happened
   here". Uncategorised remarks remain visible on the borehole track as recorded events;
   they no longer raise an indicator. Pinned by a regression test.

---

## 6 · Historical risk radar — **COMPLETE**

| Check | Result |
|---|---|
| Current depth | works |
| Formation | works where recorded; the Volve wellbores have none, and the UI says so rather than inventing one |
| Historical event depth | works — recovered by time-join, and the record says so |
| Historical risk interval | works |
| Distance ahead/behind | **works — added by this audit** |
| Depth timeline | works — `DepthRadar`, axis = the engine's own look-ahead window |
| Warning before the interval | works |
| Analogue relationship | works — every marker names the analogue well it came from |
| Demo visibility | works |

Before: the evidence list said *"120 m deeper"*, a fact rather than a warning, and the
borehole track plotted the active well's own events, not the intervals ahead in analogue
wells.

Now, measured live at 25 m on `NO 15/9-F-9`:

> **Historical risk interval 14 m ahead** — operational_remark recorded in NO 15/9-F-4 at 39 m.

Nothing is hardcoded. The axis extent is `risk.lookahead_m` served through `/api/status`;
each marker sits at its own signed `distance_from_bit_m`; the headline is the nearest event
the bit has **not yet reached**. Where nothing lies ahead the radar says so and calls it an
absence of matching records, not an all-clear. A single depth in the Volve recordings can
carry sixty-odd records, so the axis draws the nearest seven and counts the rest rather
than becoming a wall of identical labels.

---

## 7 · Explainable alerts — **COMPLETE**

Clicking **Investigate** answers, from stored data only:

| Question | Where it comes from |
|---|---|
| WHAT | event type and description |
| WHY | component scores with per-component explanations |
| CURRENT TELEMETRY | the sample at this depth, with a note saying which sample and how far away |
| CURRENT DEPTH | replayed bit depth |
| ANOMALY | Isolation Forest score **plus contributing features** |
| ANALOGUE | analogue well and its similarity |
| HISTORICAL EVENT | the retrieved record |
| HISTORICAL OUTCOME | the recorded outcome, or "No data available" |
| MITIGATION | the recorded action, or the explicit absence |
| SOURCE | dataset, reference and — now — the mitigation's own provenance |

**Two fixes.** Mitigation provenance was being dropped before it reached the UI: the risk
engine built each mitigation from three fields, and the drawer rendered a fourth that was
therefore always undefined, so the source line silently never appeared. And the anomaly had
no *why* at all — `anomaly.attribution_top_n` was configured but unused and
`contributing_features` was written as an empty list for every one of the 8,130 scores.
Attribution is now computed at scoring time and shown as *"What made this sample unusual"*.

It is labelled for what it is: a deviation score against the active-row median, **not** an
attribution of the forest's decision, because the forest exposes none. The model card
carries that limitation.

---

## 8 · Mitigation strategies — **PARTIAL**

| Check | Result |
|---|---|
| Historical problem | works |
| Action | works |
| Outcome | works, or "No data available" |
| Source | **works — fixed by this audit** |
| Relation to current risk | works — retrieved through the analogue + depth window |
| Displayed during investigation | works |
| Useful in the demo | limited by corpus coverage |

Mitigations exist **only** where document ingestion extracted them: the WITSML pipeline
produces events but never mitigations. So this feature's reach equals the report corpus's
reach, which is why the ingestion targets the wells the analogue engine actually returns
for the replayable wellbores. Where nothing was recorded the interface states *"No
validated historical mitigation found for this event. NWIS retrieves actions that were
recorded; it does not generate one."* Verified live.

Verified end to end on the Postgres stack, replaying `NO 15/9-F-7` at 500 m:

> **16/7-5 · overpull · 41 m behind** — *"When the 18-3/4 inch wellhead was latched and pull
> tested with 30 kips overpull, the 20 inch shoe was located at 458.66 meters."*
> What was done: **cemented**. Outcome: *No data available*.
> Source: **134_02_16_7_5_Final_Well_Report p.30**. Depth established via `document_text`.

The honest limit: **15 mitigations across 10 depth-placed categorised events.** An event
without a recovered depth cannot be matched to a bit position, and NWIS will not invent one
to make the radar look busier.

---

## 9 · Mitigation memory — **COMPLETE**

Problem → action → outcome → source is rendered as one block, and it is no longer buried on
another page: it sits inside the risk investigation drawer, alongside the report passages
that the same drawer can now retrieve for the same event.

---

## 10 · System-agnostic intelligence layer — **COMPLETE**

Architecture is a read-only intelligence layer over its own database. It controls nothing
and integrates with nothing proprietary. Framing, enforced throughout:

```
OIL / eRTMAC  →  the target operational ecosystem, not an integration we have
NWIS          →  an additional intelligence layer
Volve         →  public telemetry, replayed for development and demo
FORCE 2020    →  public geological data
WITSML        →  a real standard, and the format of our Volve source data —
                 NOT a claim about what eRTMAC uses
```

`docs/OIL_FACT_CHECK.md` separates what is verifiable on Oil India's own pages from what is
not, claim by claim. No eRTMAC integration is asserted anywhere in the code, the UI or the
documents.

---

## Cross-feature integration

The full chain was walked in the browser. Each transition uses the current well and depth:

| Transition | Verified |
|---|---|
| Active well → nearby wells | yes |
| → analogue engine | yes, at the current depth |
| → current depth | yes, quantised to the served `depth_context_step_m` |
| → telemetry | yes, over the WebSocket |
| → anomaly | **yes, now at the queried depth** |
| → historical risk | yes |
| → alert | **yes, now actually raised** |
| → historical event | yes |
| → mitigation | yes, or an explicit absence |
| → source | yes, including page citations |
| → engineer action | yes — *"Recorded as 'accepted' against alert #1 (MEDIUM risk indicator at 0 m MD)"* |
| → institutional memory | yes, reachable from the drawer |

Changing the **active well** resets depth context, evidence and analogues. Changing **depth**
(by replay or by seek) re-queries risk, analogues, evidence and the radar — verified by
seeking to sample 9,985 and watching the panel move from 0 m to 25 m and the radar warning
change from "1 m ahead" to "14 m ahead". Changing **replay state** and **speed** affects
pacing only; no sample value is touched.

---

## Simulator hardening

| Control | Result |
|---|---|
| Start / Pause / Resume / Stop | pass — `stopped → running → paused → running → stopped` |
| Reset | pass — returns to index 0 and clears context |
| Seek | **pass — newly exposed**; the endpoint existed, no UI reached it |
| Speed 0.5× | clamped to the configured minimum 1.0× — correct, not a failure |
| Speed 1× / 2× / 5× / 10× / 60× / 3600× | pass, accepted exactly |
| Speed 100000× | clamped to 3600× |
| Depth / bit / events / distance / analogue / anomaly / risk / telemetry synchronised | pass |

The seek control matters for the demo specifically: `NO 15/9-F-4` sits at 0 m until sample
14,952 of 59,806, which is minutes of waiting at any sane speed.

No `setTimeout` pacing hacks and no fabricated values were introduced. Replay position is
server-owned; the client renders what arrives.

---

## Error and empty states

| Request | Response |
|---|---|
| `/api/wells/99999` | 404 "Well 99999 not found" |
| `/api/wells/1/telemetry` (no telemetry) | 409 "15/9-13 has no telemetry in the knowledge base." |
| `/api/alerts/99999/explanation` | 404 |
| `/api/models/nonexistent` | 404 "has not been trained. No metrics exist for it." |
| `/api/documents/search?q=%20%20` | 422 "A search query is required." |
| document search with no index | 503 **with the command that builds it** |
| `/api/replay/1` (no telemetry) | 404 "Well 1 has no telemetry to replay" |
| `/api/wells/1/formation-at?depth_m=99999` | 404, names the depth |
| risk where nothing is computable | `evaluated: false`, and the UI refuses to render the score |

No `undefined`, `NaN`, `Infinity` or stand-in zero was observed anywhere. Console: no
JavaScript errors; the only console entries were the expected 503s from searching an
unbuilt index.

---

## Hardcoded business data

Audited across `frontend/src` and the backend. No well name, coordinate, depth, formation,
telemetry value, event, risk figure, mitigation, metric, document or timestamp is hardcoded
in either. Labels, units, colours and layout constants are, which is what they should be.

Four numbers that were **not** business data but were still duplicated in the frontend —
`DEPTH_CONTEXT_STEP_M`, `LITHOLOGY_MATCH_TOLERANCE_M`, `PAGE_SIZE` and the replay speed
list — are now served from `/api/status.config` and removed from the code. A test asserts
every offered speed lies within the replay engine's configured bounds, so the UI cannot
offer a speed the server will silently clamp.

One genuine leak was found and fixed: Volve event citations stored **absolute local
filesystem paths**, so the investigation drawer displayed
`/Users/…/Downloads/nwis-main/data/raw/volve/witsml/…`. That is a fact about the machine
that ran the pipeline, not reproducible evidence. Citations are now recorded relative to
the repository root.

---

## ML and data integrity

| Source | Used for | Verified |
|---|---|---|
| FORCE 2020 | lithology + segment embeddings | yes — 1,170,511 rows, 98 wells, well-level split |
| Volve WITSML | telemetry, trajectories, events | yes — 86,800 rows, 3 wellbores |
| Historical reports | institutional memory | yes — via OCR |
| Embeddings | analogue retrieval | yes — 3,620 segments, 32 named dimensions |
| Isolation Forest | anomaly detection | yes — 8,130 active rows, 121 features |
| Hybrid indicator | risk | yes — `probability: null` |

The two embedding spaces are never compared: 32-dimension petrophysical segment vectors for
the analogue engine, 384-dimension sentence-transformer vectors for report passages. No
supervised risk probability exists. No claim of Indian or OIL validation appears anywhere.

**One fabricated figure was found on the Models page and fixed.** The registry recorded
`device: cuda` for the lithology model on a machine with no GPU. XGBoost does not raise
when CUDA is absent — it warns and trains on the CPU anyway — so the "does a GPU work?"
probe read a successful fit as proof of one. The probe now asks the fitted booster which
device it actually used. The model was retrained so the registry reflects the run that
happened rather than being edited by hand.

---

## Docker

| Check | Result |
|---|---|
| Compose file valid | pass |
| Backend healthcheck present | pass — `depends_on: service_healthy` is satisfiable |
| Loader dependency closed | pass — `pyarrow` was the missing declaration and is now installed and exercised by the full local pipeline run |
| Postgres image builds PostGIS + TimescaleDB + pgvector | **pass** — postgis 3.6.4, timescaledb 2.29.2, vector 0.8.6 |
| Database container healthy | pass |
| Full knowledge base loads into Postgres | pass — 101 wells, 86,800 samples, 410 embedded passages |
| TimescaleDB hypertable created | **pass, after a fix** — see below |
| PostGIS GIST index | pass — `ix_wells_location_gist` |
| pgvector ivfflat indexes | pass — `ix_well_embeddings_vector`, `ix_document_chunks_vector` |
| `fallback_active: false` | **pass** |
| Simulator runs against the Postgres stack | pass — full judge journey re-walked, engineer action persisted |

**A defect that made the hypertable impossible.** `create_hypertable` failed every time
with *"cannot create a unique index without the column \"recorded_at\" (used in
partitioning)"*. TimescaleDB requires the partitioning column in every unique index, and
`telemetry_samples` had a primary key of `id` alone. Because the compose loader treats the
migration as best-effort, this failed silently and the stack simply ran as an ordinary
indexed table — `/api/status` reported that honestly, so it was visible, but the feature
had never worked. The migration now widens the key to `(id, recorded_at)` on PostgreSQL
only, which is the standard TimescaleDB pattern; the fallback backend keeps its
single-column key. Measured after the fix: 1 hypertable, 3 chunks, all 86,800 rows
migrated.

Every capability the Postgres stack accelerates also has a working fallback path — SQL
haversine for proximity, NumPy cosine for vectors, an indexed table for time series — and
the API reports which one is actually serving each concern, so either mode is honest.

---

## Not fixed, and why

- **Corpus coverage.** 8 of 388 available reports are ingested — 320 pages, 410 passages,
  all embedded. Capability is complete; coverage is a function of OCR time. Never describe
  report search as covering the back catalogue.
- **Most extracted events have no recoverable depth.** 59 of 243 events are categorised,
  and only 10 of those carry a depth. Only those 10 can serve as depth-matched risk
  evidence. The rest are searchable as passages but never surface on the radar, because
  placing them would require inventing a depth.
- **Volve wells have no geology.** They carry no wireline logs, so geology and formation
  similarity genuinely cannot be computed for them. Stated in the UI rather than papered
  over.
- **Named failure modes.** Neither public dataset has labelled incidents. The system will
  not name stuck pipe, a kick or losses from telemetry, and no supervised probability is
  produced. This is a deliberate limit, not an outstanding task.
