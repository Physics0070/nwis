# NWIS — Master QA Checklist

Run before any demo. Every line is something that has actually been checked at least once,
with the result recorded in `docs/FINAL_QA_REPORT.md`. A line is only ticked when it was
observed, not when the code that would produce it was read.

Legend: `[x]` verified · `[~]` verified with a stated limitation · `[ ]` not verified

---

## 0 · Bring-up from a clean checkout

- [x] `pip install -r backend/requirements.txt` completes on Python 3.12
- [x] `npm install` completes
- [x] `python scripts/download_datasets.py` fetches FORCE, Volve, NPD **and the wellbore
      document index** (the last was missing before this audit and broke all of
      institutional memory on a fresh clone)
- [x] `python scripts/bootstrap.py` runs every stage in order and reports timings
- [x] `/api/status` reports non-zero counts for wells, telemetry, events and embeddings
- [x] The header badge states which storage backend is actually active

## 1 · API contract

- [x] `/health` returns ok and names the dialect
- [x] `/api/status` reports `spatial_query`, `vector_search`, `timeseries_storage` truthfully
- [x] `/api/status.config` serves every threshold the UI renders with
- [x] Every offered replay speed lies inside `replay.min_speed`/`max_speed`
- [x] Unknown well → 404 naming the well
- [x] Well without telemetry → 409 with a sentence, not an empty list
- [x] Untrained model → 404 saying no metrics exist, never zeros
- [x] Blank search query → 422 (client error), not 503 (outage)
- [x] Unbuilt search index → 503 **with the command that builds it**
- [x] `probability` is `null` in every risk response
- [x] Risk with nothing computable → `evaluated: false`
- [x] No `undefined`, `NaN`, `Infinity` or stand-in zero in any response

## 2 · Risk engine

- [x] Telemetry is resolved **at the queried depth**, not "most recent"
- [x] Anomaly score is resolved at the queried depth
- [x] Beyond `risk.telemetry_match_tolerance_m` the components are unavailable and say why
- [x] Two different depths produce different components, evidence and attributions
- [x] Only **categorised** events count as historical risk evidence
- [x] Uncategorised operational remarks never raise an indicator
- [x] Rules fire from configured bands and name the channel and the limit
- [x] Mode is chosen by the data (`hybrid_indicator` until labelled events exist)

## 3 · Alerts and the feedback loop

- [x] The live session raises alerts (nothing did before this audit)
- [x] Cooldown and depth deduplication increment `occurrence_count` instead of spamming
- [x] `/alerts` lists them; `/alerts/{id}` explains them from the **stored** assessment
- [x] An engineer decision can be recorded and names the alert it was filed against
- [x] The decision persists and is readable back
- [x] Nothing retrains automatically, and the interface says so

## 4 · Simulator

- [x] Only wells with stored telemetry are selectable
- [x] WebSocket connects; status shown
- [x] WebSocket reconnects after a drop (it previously only claimed to)
- [x] Start / Pause / Resume / Stop / Reset all behave
- [x] Seek moves the replay (endpoint existed; no UI reached it before)
- [x] 0.5× clamps to the configured minimum — correct, not a failure
- [x] 1× / 2× / 5× / 10× / 60× / 3600× accepted exactly; 100000× clamps to 3600×
- [x] Speed changes pacing only; no sample value is touched
- [x] Depth, bit marker, formations, events, radar, analogue, anomaly, risk and telemetry
      stay synchronised through speed and seek changes
- [x] Investigate pauses the replay; closing resumes it — and only if the drawer paused it
- [x] Reset clears depth context, investigation and seek draft
- [x] REPLAY MODE and "historical Volve telemetry, not a live rig feed" both visible

## 5 · Historical risk radar

- [x] Axis extent is the engine's own `risk.lookahead_m`, served from the API
- [x] Markers sit at their real signed depth offsets
- [x] The headline names the nearest event **ahead** of the bit
- [x] Nothing ahead → an explicit absence, never an all-clear
- [x] A dense depth draws the nearest few and counts the rest
- [x] No distance is hardcoded anywhere

## 6 · Analogue engine

- [x] Ranking is not proximity: a 231 km well ranks fourth on geology and formation
- [x] Components and configured weights are shown
- [x] Unavailable dimensions are named and the remaining weights renormalised
- [x] The simulator states which dimensions actually contributed
- [~] Changing depth changes the ranking **on wells with logs**; on the Volve wellbores it
      cannot, because they have no logs and no stratigraphy — and the UI says so

## 7 · Institutional memory

- [x] Report search returns passages with document, well and page number
- [x] Provenance reports indexed vs total passages on every search
- [x] A query the corpus cannot answer returns nothing, and says the threshold was not met
- [x] Nothing is summarised or generated; only stored passages are shown
- [x] Report search is reachable from inside the risk investigation drawer
- [~] Corpus covers a small share of the 388 available reports — never describe it as complete

## 8 · Mitigations

- [x] Problem → action → outcome → source rendered together
- [x] Mitigation provenance reaches the UI (it was silently dropped before this audit)
- [x] Absence is stated as "No validated historical mitigation found"
- [x] No mitigation is ever generated

## 9 · Data integrity

- [x] No well name, coordinate, depth, formation, telemetry value, event, risk figure,
      mitigation, metric, document or timestamp is hardcoded in the frontend or backend
- [x] Citations are repository-relative, not absolute paths from the build machine
- [x] The registry records the compute device that was actually used
- [x] The two embedding spaces are never compared with each other
- [x] No supervised risk probability anywhere
- [x] No claim of Indian or OIL validation anywhere

## 10 · Tests and build

- [x] `python -m pytest backend/tests -q`
- [x] `npm run test`
- [x] `npx tsc -b --noEmit`
- [x] `npm run build`
- [x] Browser sweep with no JavaScript console errors

## 11 · Docker

- [x] `docker compose config` validates
- [x] The backend image defines the healthcheck its dependants wait on
- [ ] `fallback_active: false` observed under compose *(not re-verified in this audit; the
      application ran on the SQLite fallback throughout, and says so)*

## 12 · Claims discipline

- [x] Never say NWIS integrates with eRTMAC
- [x] Never say eRTMAC uses WITSML
- [x] Never present an accuracy figure as applying to Indian wells
- [x] Never call the replay a live rig feed
- [x] Never name a failure mode the anomaly model did not detect
- [x] `docs/OIL_FACT_CHECK.md` is the authority on what may be said about OIL
