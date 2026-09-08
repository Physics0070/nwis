# NWIS — Feature Completion Plan

Derived from `docs/FEATURE_AUDIT.md`. Every item below is a gap found by reading the code
and running the application, not a wish list. Priorities:

- **P0** — a core promised feature is broken or unreachable in the demo
- **P1** — an important feature is incomplete or misleading
- **P2** — polish

Nothing here adds a feature the proposal did not promise, and nothing fabricates data to
make a row go green.

---

## P0

### P0-1 · The risk engine ignored the depth it was asked about

`GET /api/wells/{id}/risk` resolved telemetry as *the most recent stored sample* and the
anomaly score as *the most recent stored score*, regardless of `depth_m`. The historical
half of every assessment followed the bit; the measured half was frozen at the end of the
recording. Two depths 2 km apart returned identical rule inputs and an identical anomaly
score.

**Done.** Both are now resolved at the queried depth, within a new configured tolerance
`risk.telemetry_match_tolerance_m`. Past that tolerance the components are reported as
unavailable and the reason is stated in `notes`, rather than borrowing a reading from a
different part of the hole. Regression-tested in
`backend/tests/test_api.py::test_risk_uses_telemetry_from_the_queried_depth` and
`::test_risk_refuses_telemetry_from_a_different_part_of_the_hole`.

### P0-2 · No alert was ever raised, so half the demo chain was unreachable

The risk engine only stores an assessment and raises an alert when called with
`persist=true`. Nothing in the frontend ever did. Consequences: the Alerts inbox was
permanently empty, `/alerts/{id}` was unreachable, and the simulator's "record your
decision" always failed with *"No alert has been raised for this well yet"* — breaking
**ALERT → HISTORICAL EVENT → MITIGATION → SOURCE → ENGINEER ACTION**.

**Done.** The simulator — the live-session surface — now persists what it evaluates, so the
alert an engineer acts on is the assessment they were shown. The engine's own cooldown and
depth deduplication are what keep this from becoming alert spam. Read-only browsing of
historical wells on the Active Well page still does not persist.

### P0-3 · Institutional memory could not be rebuilt from a clean checkout

`data_pipeline/documents/ingest.py` reads `data/raw/npd/wellbore_document.csv` to find
which reports exist. No script downloaded that file and no configuration named it, so on a
fresh clone document ingestion failed immediately — taking OCR, passages, embeddings,
report search **and every mitigation** with it (mitigations are produced only by document
ingestion; the WITSML pipeline creates events but no mitigations).

**Done.** `datasets.npd.wellbore_document_csv` added to configuration and to
`scripts/download_datasets.py`, verified against the live Sodir endpoint.

### P0-4 · Mitigation provenance was dropped before it reached the UI

The investigation drawer renders `source_reference` on each mitigation, but the risk
engine built its mitigation dictionaries from three fields only — `action_taken`,
`outcome`, `outcome_status`. The source line was therefore silently absent on every
mitigation ever displayed, which is precisely the "evidence you cannot trace" the project
forbids.

**Done.** `source_dataset` and `source_reference` now travel with the action.

---

## P1

### P1-1 · Historical Risk Radar had no "ahead" relationship

The evidence list stated *"120 m deeper"*, which is a fact but not a warning, and the
borehole track plots the **active well's own** events, not the historical intervals the bit
is approaching in analogue wells. There was nothing that answered "is the bit about to
enter an interval where something went wrong before?".

**Done.** `frontend/src/components/DepthRadar.tsx` draws the risk engine's own look-ahead
window centred on the bit, places each piece of historical evidence at its real signed
depth offset, and leads with the nearest event the bit has *not yet reached*. The window
extent is `risk.lookahead_m` served through `/api/status`, so the picture cannot disagree
with the query that produced the evidence. No distance is hardcoded. Sign handling is
regression-tested in `DepthRadar.test.ts`.

### P1-2 · Investigate did not pause the replay

The golden demo script says it pauses; it did not, so the bit kept moving behind a drawer
explaining a depth the well had already passed.

**Done.** Opening the drawer pauses a *running* replay and closing it resumes — and only
if the drawer is what paused it, so a deliberate pause is not undone.

### P1-3 · Institutional memory was unreachable from the investigation flow

Report search existed only as its own page. An engineer investigating an alert had to
leave, retype the context, and search by hand.

**Done.** The investigation drawer runs the same semantic search, seeded with the event's
own recorded words, and shows the passages with their document and page citations plus how
much of the corpus is indexed.

### P1-4 · The anomaly component had no "why"

`anomaly.attribution_top_n` was configured but unused; `contributing_features` was written
as an empty list for every score, and the CHECKLIST claim that attribution was "wired into
the risk payload" was not true. An alert could report *how* unusual a sample was but not
*which reading* was unusual.

**Done.** `ml/anomaly/train.py` now stores, per row, the features furthest from their
normal operating value as a robust z-score (median / IQR over the active rows the model was
fitted on). It is explicitly **not** presented as an Isolation Forest attribution — the
forest exposes none — and the model card limitation says so. The loader carries it into the
database and the simulator shows it under "What made this sample unusual".

### P1-5 · The frontend carried its own copies of backend thresholds

`DEPTH_CONTEXT_STEP_M`, `LITHOLOGY_MATCH_TOLERANCE_M`, `PAGE_SIZE` and the speed list were
hardcoded in three pages, free to drift away from the values the backend actually uses.

**Done.** A `ui:` section in `config/default.yaml`, served through `/api/status.config`,
with `risk_lookahead_m` and the replay speed bounds alongside. No threshold constant
remains in `frontend/src`. A test asserts every offered speed is one the replay engine will
accept.

### P1-6 · The stream claimed to reconnect and did not

The simulator told the engineer *"the stream will reconnect automatically"*. The hook only
opened a socket when the well changed; a dropped connection stayed dropped until a reload.

**Done.** Exponential backoff reconnect, buffered samples kept across a reconnect (they were
real measurements), retries cancelled on deliberate teardown. Both behaviours are
regression-tested.

### P1-7 · A recording that starts at the surface could not be skipped

`NO 15/9-F-4` sits at 0 m until sample 14,952 of 59,806. The backend has had a
`/api/replay/{id}/seek` endpoint all along; no UI reached it.

**Done.** A scrub control on the replay bar, wired to that endpoint.

---

## P2

### P2-1 · Corpus coverage

Two of 388 available reports were ingested. Report search worked, but on a corpus small
enough that most analogue wells had no passages and no mitigations.

**Approach:** ingest the reports belonging to the wells the analogue engine actually
returns for the replayable Volve wells, so the extra OCR lands where the demo looks. The
provenance line already reports indexed vs total passages, so a partial corpus stays
visible as partial — the fix is coverage, never a claim of completeness.

### P2-2 · Component explanations were computed and not shown

`RiskComponent.explanation` — "3 historical record(s) from 2 analogue well(s); nearest is
40 m from the bit" — was returned by the API and rendered only on the alert page.

**Done.** Shown inline in the simulator's intelligence panel.
