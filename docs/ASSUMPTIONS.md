# Assumptions and Environment Decisions

Every assumption below was forced by something observed on the build machine or in the
data. Each records what was found, what was decided, and what would change the decision.

---

## A1 — No Stitch UI assets exist

**Observed.** A full search of the machine (home directory, Desktop, Downloads, OneDrive,
`D:\SOHAM ALL`) found the SIH 2026 submission (`NWIS_SIH2026_IdeaSubmission_v6.pdf`,
`NWIS_SIH26121.pptx`) but no Stitch export, screenshot, design file or HTML.

**Decision.** The frontend is built as a design-token-driven component library: colours,
spacing, typography, radii and elevation live in one theme file; components consume tokens
only. Data flow is completely separate from presentation.

**Consequence.** When Stitch assets arrive, the visual layer is replaced by editing the
token file and component internals. No API call, hook, page route or state logic changes.
This is the cheapest possible path back to "Stitch is the source of truth".

---

## A2 — Docker and PostgreSQL are not installed; WSL2 is absent

**Observed.**
- `docker` is not on PATH and `C:\Program Files\Docker\Docker` does not exist
  (the installer is sitting unrun in `Downloads`).
- `wsl --status` reports the Windows Subsystem for Linux is not installed.
- No PostgreSQL service, no `psql`, no `C:\Program Files\PostgreSQL`.

Installing Docker Desktop requires WSL2, administrator rights and a reboot — none of which
can be done from inside this session.

**Decision.** The schema targets PostgreSQL with PostGIS, TimescaleDB and pgvector, and
`docker-compose.yml` provisions exactly that stack. To keep the prototype runnable on this
machine today, spatial and vector columns are **dialect-adaptive**
(`backend/app/models/types.py`):

| Concern | PostgreSQL target | Local fallback |
|---|---|---|
| Well location | `geography(Point,4326)` + PostGIS `ST_DWithin` | lat/lon float columns + haversine in SQL |
| Embeddings | `vector(n)` + pgvector cosine operator | JSON float array + NumPy cosine |
| Telemetry | TimescaleDB hypertable | ordinary table with a composite index |

The fallback is genuinely functional, not a stub: SQLite 3.45 (bundled with Python 3.12)
provides `sin`/`cos`/`acos`/`radians`, so nearby-well search runs as a real SQL haversine
query, and JSON1 stores embeddings losslessly.

**Critical point.** Latitude and longitude are stored as ordinary float columns in *both*
backends. PostGIS and pgvector make queries exact and fast; they are never a data
dependency. No record is reachable in one backend and missing in the other.

**What would change this.** Installing Docker Desktop. Then `docker compose up --build`
runs the real stack and `NWIS_DB_BACKEND` resolves to Postgres with no code change.

**Not yet verified on this machine:** PostGIS/TimescaleDB/pgvector execution paths. They
are written against the documented APIs but cannot be executed here. This is stated plainly
rather than claimed as working.

---

## A3 — Volve WITSML surface coordinates are zeroed

**Observed.** Every `_wellInfo/*.xml` carries
`<wellLocation><latitude uom="rad">0</latitude><longitude uom="rad">0</longitude>`.
The wells are real (NO 15/9-F-4, F-7, F-9, Volve field, North Sea) but positions were
stripped from this mirror.

**Decision.** Surface coordinates are ingested from the public Norwegian Offshore
Directorate factpages, keyed on wellbore name. **Coordinates are never invented.** If NPD
lookup fails for a well, its location stays `NULL` and the API reports the well as
non-mappable rather than placing it at a plausible-looking guess.

---

## A4 — FORCE X_LOC/Y_LOC are projected metres, not degrees

**Observed.** X_LOC spans 426,899–572,633 and Y_LOC spans 6,406,641–6,856,661 — UTM
metres, not lat/lon. Null fraction 0.9%.

**Decision.** Treated as ED50 / UTM zone 31N (`EPSG:23031`, the Norwegian North Sea
sector convention for this dataset) and transformed to WGS84 via `pyproj` during ingestion.
Both CRS values are configuration, not constants in code. Rows with null coordinates are
kept for ML and excluded from mapping.

---

## A5 — X_LOC/Y_LOC are excluded from lithology model features

**Observed.** Coordinates are present in 99.1% of rows and are highly predictive — because
nearby depths in the same well share lithology.

**Decision.** Excluded from the feature set. A model given absolute position memorises
geography rather than learning petrophysics; it scores well on held-out wells that happen to
sit near training wells and collapses in a new area. `Z_LOC` (true vertical depth) is kept,
since depth is legitimate petrophysical context. This is configurable via
`force_pipeline.excluded_features` — the choice is recorded, not hidden.

---

## A6 — Severe lithology class imbalance is reported, not engineered away

**Observed.** Shale 61.58% (720,803 rows) down to Basement 0.01% (103 rows).
Imbalance ratio 6,998:1.

**Decision.** No class is dropped, merged or oversampled into a flattering number.
Headline metrics are **macro F1 and balanced accuracy** alongside plain accuracy, with a
full per-class table and confusion matrix. Accuracy alone is misleading here: predicting
"Shale" for everything scores 61.6%.

Classes below `lithology_model.min_class_support` are reported separately so a metric
computed from 103 rows is never presented as equal evidence to one computed from 720,803.

---

## A7 — Anomalies are not labelled drilling failures

**Observed.** Volve WITSML supplies telemetry and 184 free-text `message` records, but no
curated stuck-pipe / kick / loss event labels.

**Decision.** The Isolation Forest reports **"abnormal drilling behaviour"** with
contributing features — never a named failure mode. A specific event type is asserted only
where an extracted historical record supports it, and that record is carried in the alert
as evidence. Per the project brief, where labels are insufficient the output is called a
**historical risk indicator**, not a supervised prediction.

---

## A8 — No LLM in the decision path

**Decision.** No generative model authors risk assessments or mitigations. Mitigations are
retrieved from stored historical records or the system states that none was found.

Transformer use is limited to local, offline embedding of report text
(`sentence-transformers/all-MiniLM-L6-v2`) and spaCy NER. No external API, no API key, no
per-call cost — which is also what makes the on-premise deployment claim in the submission
credible, since drilling data could not be sent to a third-party service.

---

## A9 — Repository root

The project root is `D:\SOHAM ALL\hackathons\SIH`, alongside the pre-existing submission
files. Those binaries (`*.pptx`, `*.xlsx`) are git-ignored: they remain on disk but stay out
of version control.

---

## A10 - The Volve WITSML mirror is completion/workover data, not drilling-ahead

**Observed.** After parsing all 312 XML files into 86,800 normalised telemetry rows:

- `ROP_AVG` is **0.00 in every row of all three wells** - the rate-of-penetration channel
  is constant zero, because no drilling-ahead took place during these recordings.
- `MSE` (mechanical specific energy) is likewise constant.
- The `Depth` channel is a **constant** equal to wellbore total depth (F-4: 3510 m,
  F-7: 1083 m, F-9: 1206 m), not live hole depth. Only `BITDEP` varies.
- WITSML `message` records carry that same constant as their `md`, so they are not
  usefully depth-tagged as published.
- Message text is unambiguous: "pull tubing", "P/U spear and set spear in 7 5/8 Tubing",
  "rih with tubing", "pooh with RT and 5 1/2 DP", "BOP test", cementing operations.
- Recording windows are short: F-4 seven days (Sep-Oct 2016), F-7 two days, F-9 1.5 days.

These are **completion, workover and plug-and-abandonment campaigns** on the Volve field
during its final years, not exploration drilling.

**Consequences, and what was done about each.**

1. **No on-bottom test is derivable.** `hole_depth - bit_depth` would be computed against
   a constant, producing an artefact. It is therefore not computed. Rig state is inferred
   only from signals that genuinely vary: flow (`circulating`), rotation (`rotating`) and
   rate of change of bit depth (`tripping`). 8,130 of 86,800 rows are operationally
   active.

2. **Message depths are recovered, not invented.** Each remark is matched to the nearest
   telemetry sample within 10 minutes, giving real depths (F-4 spans 0-3510 m instead of a
   single constant). Both the reported and recovered values are retained, along with a
   `depth_source` column, so the substitution is auditable. All 184 messages resolved.

3. **Constant channels are reported, not fed to models.** `rop_m_per_hr`, `mse_bar` and
   the mud-weight channels are detected automatically as zero-variance and listed in the
   quality report. A feature with no variance contributes nothing and would only pad an
   impressive-looking feature count.

4. **Anomaly detection is scoped honestly.** The anomaly model describes *rig operational
   behaviour* - tripping, circulating, pressure testing, cementing - using hookload,
   standpipe pressure, flow, torque, RPM, pit gain/loss and string movement, all of which
   vary meaningfully. It is **not** a drilling-ahead ROP/WOB optimiser, and NWIS must not
   describe it as one.

**What would change this.** The full Equinor Volve release (~5 TB, ~40,000 files) contains
drilling-phase WITSML for other wellbores. Ingesting a drilling-ahead subset would restore
ROP/WOB/MSE behaviour. The pipeline needs no change to accept it - the channel map already
resolves those mnemonics, they are simply constant in this mirror.
