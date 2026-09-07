# NWIS Simulator Audit

Audit only — the simulator was not rebuilt. Everything below was executed against the
running application and the served database, not read off the code.

Date: 2026-09-08 · commit `2e929db` + docs

## Overall status

**PASS WITH ISSUES**

The simulator works end to end and the data is genuinely real. Two issues affect the
scripted demo, one of which is a behaviour gap against the golden flow. Docker is still
blocked one dependency short.

---

## Simulator

| Check | Result |
|---|---|
| Simulator loads | PASS |
| Real wells load from API | PASS — 3 wells from `/api/wells?has_telemetry=true` |
| Only wells with actual telemetry selectable | PASS — filter applied server-side |
| Well selection works | PASS |
| START REPLAY works | PASS |
| WebSocket connects | PASS — `socket: open` |
| Real historical telemetry replayed | PASS — 6,367/59,806 samples observed advancing |
| Telemetry values change | PASS — standpipe, hookload, RPM all moving |
| Depth advances per recorded data | **PASS, with a caveat** — see Critical #1 |
| Bit position follows actual depth | PASS |
| Formations update where available | PASS |
| Historical event markers use real events | PASS — from `/api/wells/{id}/events` |
| Anomaly score updates from backend | PASS — risk component `anomaly 0.267` |
| Analogue intelligence loads | PASS |
| Historical evidence loads | PASS — 3 items |
| Risk indicator updates | PASS |
| `probability` stays null | PASS — rendered as a sentence, never a number |
| **INVESTIGATE pauses the simulator** | **FAIL** — see Critical #2 |
| Investigation drawer shows correct evidence | PASS |
| Historical mitigation real / cited | PASS |
| Document / page / source real | PASS |
| Engineer action recordable | PASS |
| Engineer action persists | PASS — row 16 written and read back |
| Pause / Resume / Stop | PASS — `running → paused → running → stopped` |
| Reset | PASS |
| Replay speed | PASS — 60×–3600×, server clamps to config bounds |
| Replay completion | PASS (summary panel renders on `finished`) |
| WebSocket reconnect | PASS — stale-socket bug was fixed and is covered by a test |
| Missing-data states | PASS — "No data available", never a zero |
| No fake/random business data | PASS — see below |

---

## Data integrity — what is real vs simulated

**Real:** every telemetry value, depth, timestamp, event, mitigation, citation, formation,
analogue score and risk component.

**Simulated:** only the *passage of time*. The replay engine reads stored rows in order
and paces them; it does not synthesise values.

Verified by search: **no random/synthetic generator exists anywhere** in the telemetry
path — no `random`, `np.random`, `Math.random`, faker or mock in `services/replay.py`,
the API layer, `data_pipeline/volve/`, or the frontend.

**Replay speed changes pacing only.** `replay.py` divides the recorded sample cadence by
the speed to compute a sleep interval (`asyncio.sleep(cadence / speed)`). It never touches
a sample value.

**Depth is not generated.** It is the stored `bit_depth_m` column.

### Source data actually ingested

| Well | Samples | Depth range | Time coverage |
|---|---|---|---|
| NO 15/9-F-4 | 59,806 | 0.0 – 2,617.56 m | 2016-09-30 → 2016-10-07 |
| NO 15/9-F-7 | 14,871 | 0.0 – 865.58 m | 2016-07-28 → 2016-07-30 |
| NO 15/9-F-9 | 12,123 | 0.0 – 359.0 m | 2016-09-29 → 2016-09-30 |

Total **86,800** samples. Dataset: **Volve WITSML** (source_dataset column, all three).

16 channels stored: `bit_rpm, block_position_m, flow_in_lpm, flow_out_lpm, hookload_kn,
mse_bar, mud_weight_in_sg, mud_weight_out_sg, pit_gain_loss_m3, rig_activity_code,
rop_m_per_hr, standpipe_pressure_bar, surface_rpm, surface_torque_knm, total_gas,
weight_on_bit_kn`. The simulator surfaces 6 by default; the rest are stored, not hidden.

**No suspicious transformations found.** Unit conversion (SI → driller units) happens once
in the Volve pipeline and is documented.

### Is the 0 m start genuine?

**Yes — genuine, do not change it.** Measured directly:

| Well | First sample with depth > 0 | Samples at depth > 0 |
|---|---|---|
| F-4 | index **14,952** (25% into the recording) | 36,594 / 59,806 |
| F-7 | index 717 (5% in) | — |
| F-9 | index **0** (immediately) | — |

The bit is genuinely at surface. Volve here is a completion/workover recording, not
drilling-ahead (see `ASSUMPTIONS.md` A10). Depth is also non-monotonic — 3,171 decreasing
steps on F-4 — which is real tripping in and out, not corruption.

---

## ML / Risk — what is actually calculated

Chain verified: telemetry → 121 rolling/derivative features → Isolation Forest → anomaly
score persisted (8,130) → hybrid risk indicator → historical correlation → UI.

Live response at F-4, 850 m:

```
mode: hybrid_indicator   probability: None   risk_level: MEDIUM   score: 0.4686
note: "only 147 categorised historical events exist (threshold 200), so risk is
       reported as a historical indicator rather than a supervised prediction,
       and no probability is produced"
```

- No fabricated probability — `null`, and the UI prints a sentence explaining why.
- No fake risk score — composed of `anomaly`, `historical_evidence`, `rules` with weights.
- No claim of confirmed failure anywhere in the UI.
- The mode is **data-driven**: it flips to supervised only if labelled events pass a
  configured threshold.

**Distinction check.** The UI separates *observed anomaly* (a risk component with a value),
*historical evidence* (a labelled list of past events on other wells) and *potential risk*
(the composite indicator). It does **not** have an explicit "CONFIRMED EVENT" state,
because the system cannot establish one from this data — and it never claims one. That is
correct, but see Non-critical #1 for wording.

---

## Analogue intelligence

Live response, well 15/9-13 at 2,500 m:

```
weights: geology 0.30, formation 0.20, depth 0.15, drilling_behaviour 0.20, geography 0.15
match: 15/9-15  score 0.919
  geology     0.9766  "cosine similarity of 32-dimension petrophysical vectors over 2485-2585 m"
  formation   1.0000  "shared stratigraphy: Tor Fm."
  depth       0.8893  "compared interval is 35 m from the query depth"
  geography   0.7258  "8.0 km from the active well"
  dimensions_unavailable: ["drilling_behaviour"]
```

- Embedding source: `well_embeddings`, **3,620 rows, 32 dimensions** each.
- Similarity: cosine, computed server-side (pgvector on Postgres, NumPy on fallback).
- **The UI states only reasons the backend actually returned.** `drilling_behaviour` was
  unavailable, was excluded, weights were renormalised, and this is reported.

**Correction to a common description:** the 3,620 segment embeddings are **32-d
petrophysical**, and the 384-d MiniLM embeddings are the **562 document passages**. These
are two different spaces and must not be described as one. The column types enforce it.

---

## Historical intelligence — one chain traced end to end

```
Mitigation 1
  action       "reamed; set"
  outcome      None            → UI shows the honest absence
  confidence   0.7288
  source       "45_15_9_13_Completion_report_and_log p.108"
        ↑
Event 335  tight_hole @ 304.0 m
  description  "A wiper trip was made using 26\" bity and a tight spot at 304 m was reamed."
  dataset      Sodir wellbore documents
  well         15/9-13
```

Verified: event is real, attached to the correct well, depth 304 m matches the sentence,
the mitigation exists, and the citation names a real document **and page**. The OCR typo
("bity") is left as-is, which is correct — the source text is not cleaned up to look more
authoritative.

Event provenance overall: 184 `witsml_message`, 147 `nlp_document_extraction`.

**Only 1 of 48 mitigations has a parsed `outcome`.** The PROBLEM → ACTION → SOURCE chain
is solid; OUTCOME is usually missing because the outcome vocabulary does not match this
corpus. The UI already says "No data available" for it rather than inventing one.

---

## OIL / eRTMAC

Full detail in `docs/OIL_FACT_CHECK.md`. Summary:

- **Verified on OIL's own site:** eRTMAC exists; it is OIL's real-time drilling monitoring
  and analysis centre; real-time well data is transmitted from rig sites; it is staffed
  round the clock by engineers making real-time decisions; it targets faster anomaly
  detection and NPT reduction under the DRIVE programme.
- **Not publicly verified:** that eRTMAC uses WITSML; its exact location; its data model,
  protocols or API. **Do not claim any of these.**
- **False if claimed:** that NWIS integrates with eRTMAC today, or that Norwegian-trained
  models represent Indian conditions.
- No standalone public eRTMAC page or technical document exists. Do not cite one.

---

## Docker

| Check | Result |
|---|---|
| Database healthy | **PASS** — `Up 41 minutes (healthy)` |
| PostGIS | **PASS** |
| TimescaleDB | **PASS** |
| pgvector | **PASS** — `pg_extension`: `plpgsql postgis timescaledb vector` |
| `fallback_active: false` | **PASS** (observed in the loader's own log) |
| Backend healthy | **NOT REACHED** — waits on loader |
| Frontend healthy | **NOT REACHED** |
| Simulator via container | **NOT REACHED** |
| Engineer action persists in Postgres | **NOT TESTED** — verified on SQLite instead |

**Exact loader cause — it is no longer pyproj:**

```
ImportError: Unable to find a usable engine; tried using: 'pyarrow', 'fastparquet'.
  data_pipeline/load_database.py:137  ingest_force → pandas.read_parquet
```

`pyproj` was fixed and verified in the image (`pyproj 3.8.0`). `pandas` needs a parquet
engine and none is declared. This is the **third** undeclared dependency in the same class
(`pyproj`, `rapidocr_onnxruntime`, now `pyarrow`) — all invisible on the dev machine
because it has them installed transitively.

Schema creation on Postgres now succeeds; the earlier GeoAlchemy2 `KeyError:
'_saved_columns'` was fixed by removing GeoAlchemy2 entirely.

---

## UI / UX

Checked in Chromium at **1440×900** and **1920×1080**.

- Clear hierarchy, one primary visualisation, one intelligence panel, one control bar. PASS
- Not crowded; whitespace holds at both widths. PASS
- Restrained surfaces, no glow/blur/particles/3D/gauges. PASS
- Animation limited to the bit marker and progress bar (700 ms linear). PASS
- Console: **0 simulator errors.** The one 404 is `/api/wells/99/lithology` on a Volve
  well, which has no logs — correct behaviour, and the UI states it.
- Network: all simulator calls 200/201.

---

## Critical bugs

**1 — Depth does not visibly move for the first quarter of the default demo well.**
F-4 stays at 0.0 m until sample 14,952. At 600× that is roughly **4 minutes of demo before
the depth changes**. The data is genuine and must not be altered. The fix is to demo
**NO 15/9-F-9**, which has depth from sample 0, or to expose the existing
`/api/replay/{id}/seek` endpoint in the UI. The backend already supports seek; the
simulator simply does not surface it.

**2 — Investigate does not pause the replay.** Golden demo step 10 says the simulation
pauses. Measured: status was `running` before and after clicking Investigate. Evidence
still displays correctly, but the flow does not match the script.

**3 — Docker loader blocked** on the missing parquet engine (above). One line.

**4 — "0 m away" label was misleading (FIXED).** `distance_from_bit_m` is
`event.depth_start_m - current_bit_depth`, a **vertical depth offset**, not a distance
between wells. The simulator rendered it as "0 m away", which read as though an analogue
well 8 km off were zero metres away. The number was correct — the bit and those F-9 events
are both at 0 m depth — but the wording was not. Now reads "same depth" / "N m deeper" /
"N m shallower". `ActiveWell` and `AlertExplanation` render the same field as `(+35 m)`
beside a depth and were never ambiguous.

---

## Non-critical issues

1. The UI does not have an explicit **CONFIRMED EVENT** state. It never *claims* one, so
   nothing is wrong, but the four-way distinction the brief asks for is currently three-way
   in practice.
2. **Frontend threshold constants are hardcoded** rather than config-driven — the project
   rule holds for Python but was never applied to TypeScript:
   - `Simulator.tsx` `DEPTH_CONTEXT_STEP_M = 25`, `SPEEDS = [60,300,600,1800,3600]`
   - `ActiveWell.tsx` `DEPTH_CONTEXT_STEP_M = 25`, `LITHOLOGY_MATCH_TOLERANCE_M = 10`
   - `Wells.tsx` `PAGE_SIZE = 25`
   `replay.min_speed`/`max_speed` already exist in config and are not used by the UI.
   (`DrillTrack.tsx` geometry constants are SVG layout, not business data — fine.)
3. Simulator shows numeric telemetry only; `TelemetryChart` already exists and could be
   reused for a trend line.
4. Only 1 of 48 mitigations carries an outcome.

**No hardcoded business data was found** — no well names, coordinates, depths, formations,
events, risks, mitigations or model metrics in application code. The only matches were
docstring usage examples and regex comments.

---

## Demo readiness

**READY WITH ISSUES** — ready if you demo **NO 15/9-F-9** and pause manually before
investigating.

Not ready to claim: that the Docker/Postgres stack serves the application, or any OIL
integration.

---

## Recommended fixes

**P0**
1. Demo with **NO 15/9-F-9**, not F-4 — or expose the existing `seek` endpoint. Zero code
   for the first option.
2. Add `pyarrow` to `backend/requirements.txt` — unblocks the entire Docker stack.
3. Ensure no deck slide claims eRTMAC integration, WITSML use by eRTMAC, or Indian-well
   validation.

**P1**
4. Pause the replay when Investigate is clicked (golden demo step 10).
5. Move the frontend threshold constants into config, served via `/api/status`.

**P2**
6. Reuse `TelemetryChart` in the simulator for a trend line.
7. Extend `OUTCOME_TERMS` so more mitigations carry an outcome.
8. Add an explicit CONFIRMED-EVENT state to complete the four-way distinction.
