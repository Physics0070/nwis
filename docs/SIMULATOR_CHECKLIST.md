# Drilling Simulator — Checklist

Replay of **real recorded telemetry**, presented as a live drilling session.
Nothing is generated: every value comes from an existing endpoint.

Legend: `[ ]` not started · `[~]` in progress · `[x]` complete

---

## Reuse (no rebuilding)

| Existing capability | Reused via |
|---|---|
| Telemetry replay (server-owned) | `POST /api/replay/{id}/{start,pause,resume,stop,speed}` |
| Live stream | `ws /ws/telemetry/{id}` via `useTelemetryStream` |
| Wells | `GET /api/wells` |
| Analogues | `GET /api/wells/{id}/analogues?depth_m=` |
| Risk + evidence + mitigations + citations | `GET /api/wells/{id}/risk?depth_m=` |
| Events | `GET /api/wells/{id}/events` |
| Engineer feedback | `POST /api/engineer-actions` |
| Lithology / formations | `GET /api/wells/{id}/{lithology,formations}` |

No new backend endpoint was required.

---

## Build

- [x] Inspect existing architecture
- [x] Simulator page (`/simulator`)
- [x] Well selection from API
- [x] Real telemetry replay
- [x] WebSocket integration
- [x] Play / Pause / Resume / Stop / Reset
- [x] Replay speed controls
- [x] Depth-synchronized drilling visualization
- [x] Live telemetry updates
- [x] Anomaly updates
- [x] Analogue intelligence
- [x] Historical event correlation
- [x] Risk indicator (hybrid, never a fabricated probability)
- [x] Investigation mode
- [x] Historical mitigation evidence
- [x] Source / document / page citation
- [x] Engineer action + feedback persistence
- [x] Simulation completion summary
- [x] Missing-data / error states
- [x] WebSocket reconnect handling
- [x] No hardcoded business data
- [x] Tests
- [x] Chromium / console verification
- [~] Docker verification (images build; loader blocked, see HANDOFF)

---

## Rules held

- **REPLAY MODE** labelled in the top bar at all times.
- Risk shown as `hybrid_indicator`; `probability: null` rendered as an explicit
  statement, never as a number.
- Any absent value renders "No data available" — never a zero, never invented.
- One intelligence panel; secondary detail lives in a drawer.
- Restrained industrial surfaces, subtle motion only.
