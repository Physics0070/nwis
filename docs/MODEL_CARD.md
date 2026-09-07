# NWIS Model Cards

Every number in this document was produced by a training run in this repository and is
reproducible via the commands shown. Nothing is estimated or quoted from elsewhere.

Metrics are read back from `artifacts/models/<model>/latest.json`, which is also what the
API serves. If a model has not been trained, the API reports that rather than a number.

---

## 1. Lithology Classifier

### Purpose

Predict lithofacies from wireline logs at a given depth, so that NWIS can describe the
geological context of the active well and use lithology as one dimension of analogue-well
similarity.

This is **decision support**. It does not steer, control or automate any drilling
equipment, and it does not replace a petrophysicist's interpretation.

### Dataset

| | |
|---|---|
| Source | FORCE 2020 Machine Learning Competition (Norwegian North Sea, public) |
| Rows | 1,170,511 |
| Wells | 98 |
| Depth sampling | 0.152 m (measured, not assumed) |
| Classes | 12 lithofacies |
| Duplicate (well, depth) rows | 0 |
| Wells with non-monotonic depth | 0 |

Class distribution is severely imbalanced — Shale 61.58% down to Basement 0.01%, a ratio
of **6,998:1**.

### Features

15 base curves selected by *measured* coverage (≥ 30% of rows), expanded to **90 features**:

- Base curves: `BS, CALI, DEPTH_MD, DRHO, DTC, GR, NPHI, PEF, RDEP, RHOB, RMED, ROP, RSHA, SP, Z_LOC`
- Rolling mean and standard deviation over 5 m and 15 m depth windows, computed per well
- First derivative with respect to depth

Window sizes are configured in **metres** and converted to sample counts using the measured
sampling interval, so the same configuration behaves correctly if log density changes.

**Deliberately excluded**

| Excluded | Reason |
|---|---|
| `FORCE_2020_LITHOFACIES_LITHOLOGY` | the label |
| `FORCE_2020_LITHOFACIES_CONFIDENCE` | annotation-quality flag — leakage |
| `X_LOC`, `Y_LOC` | absolute map position. A model given coordinates memorises geography instead of petrophysics: it scores well on held-out wells that happen to sit near training wells and collapses in a new area. |
| `GROUP`, `FORMATION` | human stratigraphic interpretations, not measurements. Available via config, off by default. |

`Z_LOC` (true vertical depth) is retained — depth is genuine petrophysical context.

Missing values are median-imputed **with missingness indicators**: a curve is usually absent
because a tool was not run over an interval, so its absence is itself informative.

### Split

**Well-level.** Every row of a well belongs to exactly one split, so test metrics answer
"how does this behave on a well it has never seen".

| Split | Wells | Rows |
|---|---:|---:|
| Train | 68 | 769,983 |
| Validation | 15 | 217,144 |
| Test | 15 | 183,384 |
| **External holdout** (FORCE leaderboard) | **10** | **136,786** |

The external holdout is the competition's own test set — 10 wells that appear nowhere in
the 98 training wells. It is an independently-defined benchmark, not a split we chose.

Rare classes are actively distributed across splits (classes present in ≤ 15 wells).
Model selection uses **validation only**; test and holdout are never consulted.

### Results

Measured on the **10 unseen leaderboard wells**:

| Metric | XGBoost | RandomForest | Majority baseline |
|---|---:|---:|---:|
| Accuracy | 0.7498 | 0.7189 | 0.6139 |
| **Macro F1** | **0.3852** | 0.3336 | 0.0761 |
| Balanced accuracy | 0.4105 | 0.3738 | 0.0833 |
| Cohen κ | 0.5659 | 0.5055 | 0.0 |
| FORCE penalty (lower better) | **0.6322** | 0.7301 | — |
| Training time | **196 s** (GPU) | 36,280 s (CPU) | — |
| Artifact size | 5 MB | 901 MB | — |

Accuracy is reported next to the majority-class baseline throughout, because predicting
"Shale" for every row already scores 0.61. Macro F1 and balanced accuracy are the honest
headline numbers.

**Per-class, external holdout (XGBoost):**

| Class | Support | Precision | Recall | F1 |
|---|---:|---:|---:|---:|
| Shale | 83,975 | 0.851 | 0.849 | 0.850 |
| Sandstone | 24,048 | 0.807 | 0.837 | 0.822 |
| Sandstone/Shale | 17,558 | 0.440 | 0.335 | 0.381 |
| Limestone | 4,798 | 0.420 | 0.708 | 0.527 |
| Marl | 3,306 | 0.194 | 0.296 | 0.234 |
| Tuff | 1,245 | 0.528 | 0.250 | 0.339 |
| Coal | 690 | 0.604 | 0.830 | 0.699 |
| Chalk | 625 | 0.000 | 0.000 | 0.000 |
| Dolomite | 416 | 0.000 | 0.000 | 0.000 |
| Anhydrite | 125 | 0.000 | 0.000 | 0.000 |

### Known limitations

**1. The carbonate–evaporite family collapses into Limestone.** This is the most important
limitation, and it is geologically coherent rather than a defect:

| Actual | Predicted as |
|---|---|
| Chalk | **Limestone 99.8%**, Marl 0.2% |
| Dolomite | Limestone 54.8%, Shale 44.2% |
| Anhydrite | Limestone 49.6%, Shale 44.0% |

Chalk *is* a fine-grained limestone; on the available curves (GR, RHOB, NPHI, DTC) the two
are close to indistinguishable without cuttings or image logs. **NWIS must not present a
Limestone prediction as evidence that an interval is not chalk or dolomite.**

**2. Basement is unmeasurable.** It occurs in exactly 1 of 98 wells (103 rows dataset-wide),
so it cannot appear in both training and evaluation. No metric is reported for it.

**3. Well-to-well variance is large.** Validation accuracy 0.632 vs test 0.734 on
equal-sized well sets. A single-well result can differ substantially from the average.

**4. Basin-specific.** Trained entirely on Norwegian North Sea wells. Behaviour on Indian
basins (the eventual OIL deployment target) is unverified and should be assumed poor until
retrained on local data.

**5. Balanced weighting trades accuracy for coverage.** Class balancing raised macro F1
from 0.3441 to 0.3852 and balanced accuracy from 0.3245 to 0.4105, while accuracy fell from
0.7707 to 0.7498. This trade is deliberate: a model that only ever predicts common
lithologies is useless for flagging unusual intervals.

### Potential bias

The training wells are exploration wells from one operator region. Lithology labels are
human interpretations carrying their own uncertainty (FORCE publishes a per-row confidence
flag, which is excluded from features as leakage). Rare lithologies are under-represented
in a way no reweighting fully repairs.

### Intended use

- Describing geological context of an interval as **one input among several**
- Contributing a geology dimension to analogue-well similarity
- Flagging where the geological picture is uncertain

### Non-intended use

- Any autonomous or automated drilling control action
- Casing, cement or mud-programme decisions without petrophysical review
- Reserves or volumetric estimation
- Use outside the Norwegian North Sea without retraining and re-evaluation
- Treating a Limestone prediction as ruling out chalk, dolomite or anhydrite

### Reproduce

```bash
python scripts/download_datasets.py --only force
python -m data_pipeline.force.profile
python -m data_pipeline.force.prepare
python -m ml.lithology.train
```

Hyperparameters live in `config/default.yaml` under `lithology_model`. XGBoost uses the GPU
when one is available (`device: auto`) and falls back to CPU otherwise. Early stopping is
driven by **macro F1 on the validation wells**, not log-loss — stopping on log-loss halted
training at iteration 54 instead of 384, before the rare classes were learned.

---

## 2. Drilling Anomaly Detector

**Status: not yet trained.** No metrics are claimed. See `CHECKLIST.md` Phase 7.

Planned: Isolation Forest over Volve telemetry features. It will report *"abnormal drilling
behaviour"* with contributing features — never a named failure mode such as stuck pipe or
kick, because no labelled failure events exist in either dataset.

## 3. Risk Indicator

**Status: not yet built.** See `CHECKLIST.md` Phase 10.

Neither FORCE nor the Volve mirror contains curated drilling-failure labels, so this will be
a **hybrid historical risk indicator** — anomaly score combined with analogue-well evidence
and rules — not a supervised risk classifier. It will not report a calibrated probability,
because nothing in the data would make such a probability meaningful.
