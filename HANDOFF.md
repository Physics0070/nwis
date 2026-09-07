# NWIS — Handoff

Repo: `D:\SOHAM ALL\hackathons\SIH` · `main` · github.com/Physics0070/nwis
Last session: 2026-09-08 · 35 commits

---

## 1. Goal

An evidence-backed drilling decision-support prototype for SIH 2026 (PS 26121): real data
through ingestion → database → trained models → analogue engine → risk engine → React
dashboard, plus a **drilling simulator** that replays real recorded telemetry.

The rule the whole thing rests on: **no number is shown unless a measurement produced it.**
Unknown values say so instead of rendering a zero.

---

## 2. Current state

**Working.** 68 backend + 22 frontend tests pass. 87 API requests swept, 0 server errors.
0 console errors. Demo runs at http://localhost:5173.

| | Measured |
|---|---|
| Wells | 101 (98 FORCE + 3 Volve), 0 without a real surveyed position |
| Telemetry | 86,800 samples across 3 Volve wells, 16 channels |
| Events / mitigations | 331 / 48 |
| Documents | 2 reports, 429 pages OCR'd, 562 passages embedded |
| Simulator | built, verified end to end |

**Docker: partially verified.** The database container is healthy with `postgis`,
`timescaledb` and `vector` all present and `fallback_active: false`. All four images build.
**The loader still fails** — see §6. The app therefore still runs on the SQLite fallback,
which the UI header states plainly.

**Open:** the loader dependency; Investigate does not pause the replay; frontend threshold
constants are still hardcoded.

---

## 3. Active files

| Path | Role |
|---|---|
| `frontend/src/pages/Simulator.tsx` | the simulator |
| `frontend/src/components/DrillTrack.tsx` | borehole depth visualisation |
| `frontend/src/pages/ActiveWell.tsx` | main dashboard (shares the risk panel fix) |
| `frontend/src/lib/api.ts` | **every** UI value passes through here |
| `backend/app/services/risk.py` | risk assessment + the new `evaluated` flag |
| `backend/app/schemas/models.py` | API response shapes |
| `backend/requirements.txt` | **where the Docker blocker lives** |
| `docker-compose.yml` | 4 services incl. the one-shot `loader` |
| `docs/SIMULATOR_AUDIT.md` | audit findings and evidence |
| `docs/OIL_FACT_CHECK.md` | verified vs unverified OIL/eRTMAC claims |
| `docs/TEAM_STATUS.md` | onboarding brief |

---

## 4. Changes made

- **Built the drilling simulator** (`/simulator`). Replays real stored telemetry; no new
  backend endpoint — it drives the existing replay, risk, analogue, events, formations and
  engineer-action APIs. Verified live: 6,367 samples replayed, investigation drawer with a
  real WITSML citation, engineer action persisted as DB row 16.
- **Audited the simulator end to end** → `docs/SIMULATOR_AUDIT.md`. Confirmed no
  random/synthetic generator exists anywhere, replay speed changes pacing only, and depth
  is the stored column.
- **Fact-checked OIL/eRTMAC** against oil-india.com directly → `docs/OIL_FACT_CHECK.md`.
- **Fixed: unevaluable risk reported as `INFO` score `0`.** At a depth where nothing can
  contribute, the engine returned 0.0, which classifies as INFO and reads as "assessed and
  fine". `RiskResult` now carries `evaluated: bool`; the risk panels render the explicit
  absence. Added additively (defaults to true) so nothing else breaks.
- **Fixed: "0 m away" evidence label.** `distance_from_bit_m` is a vertical depth offset,
  not a distance between wells. Now "same depth" / "N m deeper" / "N m shallower".
- **Removed GeoAlchemy2**, which broke `create_all` on Postgres with
  `KeyError: '_saved_columns'`. Nothing else used it.
- **Declared `pyproj` and `rapidocr-onnxruntime`**, both imported but undeclared.
- **Moved Docker storage to D:** via a directory junction (see §5).
- Earlier: report search + passage embeddings, grouped cross-validation, and two extraction
  fixes (negation, routine operations).

---

## 5. Failed attempts — do not repeat

- **`DataFolder` in `settings-store.json` does not move Docker's storage** on the WSL2
  backend. Docker ignores it and refills C:. `wsl --manage --move` does not help either —
  the data disk is not a registered distro. **Use a directory junction:**
  `mklink /J "%LOCALAPPDATA%\Docker\wsl\disk" "D:\DockerData\disk"` with Docker stopped.
  Already done; `LinkType` reports `Junction`.
- **Do not let C: approach zero.** A build took it to 2.2 GB and Windows began failing to
  start processes (`fork: Resource temporarily unavailable`, PowerShell unable to start the
  CLR). A full build needs ~10 GB.
- **Bash heredocs mangle regex backslashes.** `\b` became a literal backspace byte inside a
  compiled regex and silently matched nothing. Write Python source with the Write/Edit
  tools, and commit messages with `git commit -F <file>`.
- **`pkill` does not kill the server on Windows.** Use
  `Get-NetTCPConnection -LocalPort 8000 -State Listen` → `Stop-Process -Force`.
- **A stale Vite bundle looks exactly like broken features.** "Report search broken" and
  "Models stuck loading" were both a stale dev server — the API answered in 25 ms. Restart
  Vite and clear `node_modules/.vite` before debugging the application.
- **`api.wells` does not exist — it is `api.listWells`.** Cost one silently empty dropdown.
- **Do not select the lithology model on the holdout.** Cross-validation showed the two
  candidates are not separable (fold spread 3.1× the gap, p = 0.224).
- **Bit depth 0.0 m early in Volve is genuine**, not a bug — the bit is at surface on a
  completion/workover run. Do not "fix" it.

---

## 6. Next steps, in order

1. **Add `pyarrow` to `backend/requirements.txt`.** One line. The loader dies at
   `load_database.py:137` on `pandas.read_parquet` with
   `ImportError: Unable to find a usable engine; tried using: 'pyarrow', 'fastparquet'`.
   This is the third undeclared dependency of the same class — after adding it, audit all
   imports against requirements once so the class is closed. Then
   `docker compose build loader && docker compose up -d` and confirm `/api/status` reports
   `fallback_active: false`.
2. **Demo with `NO 15/9-F-9`, not `F-4`.** F-4 stays at 0 m until sample 14,952 of 59,806 —
   roughly 4 minutes at 600× before depth moves. F-9 has depth from sample 0. Alternatively
   expose the existing `/api/replay/{id}/seek` endpoint in the simulator UI.
3. **Pause the replay when Investigate is clicked.** It currently keeps running; the golden
   demo script says it pauses.
4. **Move frontend thresholds into config**, served via `/api/status`:
   `DEPTH_CONTEXT_STEP_M` (Simulator + ActiveWell), `LITHOLOGY_MATCH_TOLERANCE_M`,
   `PAGE_SIZE`, and the `SPEEDS` list (`replay.min_speed`/`max_speed` already exist in
   config and are unused by the UI).
5. Optional: reuse `TelemetryChart` in the simulator for a trend line.

**Before any demo:** never claim eRTMAC integration, WITSML use by eRTMAC, or validation on
Indian wells. See `docs/OIL_FACT_CHECK.md` for what is and is not publicly verified.

---

## Run it

```bash
uvicorn backend.app.main:app --reload   # terminal 1
cd frontend && npm run dev              # terminal 2 → http://localhost:5173/simulator
```

`GET /api/status` shows what is loaded and which storage backend is actually active.
