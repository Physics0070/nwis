# Machine Learning

Four models, each answering a different question. None of them is a general "AI that
predicts drilling risk" — that framing is what produces unjustifiable numbers.

| Model | Question | Type | Status |
|---|---|---|---|
| Lithology | What rock is this? | supervised, 12-class | trained |
| Anomaly | Is the rig behaving normally? | unsupervised | trained |
| Analogue embedding | Which wells saw rock like this? | representation | built |
| Risk | Should the engineer be concerned? | hybrid indicator | rule + evidence, not learned |

Full metrics and limitations: [`MODEL_CARD.md`](MODEL_CARD.md).

---

## Rules this project holds to

1. **No metric is written by hand.** Trainers compute metrics, write them to the registry,
   and the API serves that file. A number in the UI can be traced to a training run with a
   timestamp and git revision.
2. **Unseen wells or it does not count.** Splits are well-level. Two independent unseen-well
   estimates are reported: internal test wells and the official FORCE leaderboard.
3. **Selection never touches test data.** Model selection uses validation wells only. When
   validation and holdout disagree, the disagreement is recorded, not resolved by picking
   the holdout winner.
4. **Accuracy is always shown against a baseline.** Predicting "Shale" everywhere scores
   0.61 on this dataset.
5. **Smoke runs are never registered.** `--smoke` trains on a handful of wells with 20 trees
   for a fast path check; those results cannot reach the API.

---

## Lithology classifier

```bash
python -m ml.lithology.train
python -m ml.lithology.train --smoke          # fast end-to-end check
python -m ml.lithology.train --select-only    # re-decide which model is served
```

RandomForest and XGBoost, 90 features, 1,170,511 rows, well-level 68/15/15 split.

### Two findings worth reading

**Early stopping on log-loss was wrong for this problem.** Log-loss is dominated by the
majority class, so it halted training at iteration 54 — before rare lithologies were
learned. Stopping on macro F1 instead ran to 384 and improved holdout macro F1 from
0.3146 to 0.3441.

**Class balancing is a deliberate trade.** Adding balanced sample weights moved holdout
macro F1 from 0.3441 to 0.3852 and balanced accuracy from 0.3245 to 0.4105, while accuracy
fell from 0.7707 to 0.7498. A model that only predicts common lithologies cannot flag an
unusual interval, so the trade is worth making — but it *is* a trade, and it is stated.

### A configuration mistake, measured

The first RandomForest used `n_estimators=300, min_samples_leaf=5`. It took **36,280 s
(10.1 hours)** and produced a **901 MB** artifact. Retuned to `n_estimators=150,
min_samples_leaf=25` it took **291 s** and produced **179 MB**, with holdout macro F1
changing from 0.3336 to 0.3337 — statistically nothing.

The 10-hour run bought nothing. It is recorded here because "it trained for ten hours" is
not evidence of a better model, and reproducibility on a laptop is a real requirement.

### Leakage prevented deliberately

`X_LOC` and `Y_LOC` are excluded. A model given absolute position memorises geography rather
than petrophysics: it scores well on held-out wells that happen to sit near training wells,
and collapses in a new basin. `Z_LOC` (true vertical depth) is kept, because depth is
genuine petrophysical context. `FORCE_2020_LITHOFACIES_CONFIDENCE` is excluded as an
annotation-quality flag.

Missing values are median-imputed **with missingness indicators**: a curve is usually absent
because a tool was not run over an interval, so its absence is informative.

---

## Anomaly detector

```bash
python -m ml.anomaly.train
```

Isolation Forest over rolling telemetry features: 121 features from 11 usable channels
(2 constant channels detected and excluded automatically), trained on the **8,130
operationally-active rows** of 86,800. Training takes about a second.

Idle rows are excluded from training. They are the overwhelming majority, and including them
would define "normal" as "the rig is doing nothing", making every real operation look
anomalous.

### What it will not say

It reports **"abnormal operational behaviour"** with contributing features. It does not name
stuck pipe, kick or losses, because no labelled incidents exist in either dataset to learn
those from. Naming an event requires historical evidence, which the risk engine supplies
separately from stored records.

The isolation score is **not a calibrated probability** and is never presented as one.

### Evaluation without labels

There is no ground truth, so no accuracy, precision or recall is claimed. The registry
records structural diagnostics instead:

| Diagnostic | Value |
|---|---|
| Requested contamination | 0.02 |
| Achieved anomaly rate | 0.02 |
| Per-well rates | F-4 1.4%, F-7 3.6%, F-9 2.5% |

Stability across wells is the useful signal: a detector that flagged 1% in one well and 30%
in another would be learning well identity rather than behaviour.

The contamination rate is an *assumption* about how much of the data is unusual, not a
measurement. Changing it changes the flag count directly, and the model card says so.

---

## Analogue embeddings

```bash
python -m ml.similarity.build_embeddings
```

3,620 overlapping 100 m segments across 98 wells, stride 50 m so a query never falls between
segments. **32 dimensions**: 10 curves × (mean, std, median) + normalised thickness +
normalised mid-depth.

Features are z-scored using statistics computed once over the whole corpus and stored with
the model, so a query segment is scaled exactly like the indexed ones. Outliers are clipped
at 5σ so one absurd reading cannot dominate cosine similarity.

**Interpretable on purpose.** Every dimension is a named petrophysical statistic, so a match
can be explained in terms of the curves that drove it. A learned ranking model would
probably score better; nothing about NWIS is useful if an engineer cannot see why two wells
were called similar. Engineer feedback is the path to a learned ranker, and the registry
records that as a limitation.

---

## Risk: why there is no trained model here

The engine inspects the database at evaluation time and counts categorised historical
events. Below `risk.min_labelled_events_for_supervised` (default 200) it runs as
`hybrid_indicator` and reports `probability: null`.

This is not a shortcut. Building a supervised risk classifier requires labelled incidents,
and there are none:

- Volve WITSML has telemetry and 184 operational remarks, but no curated incident labels.
  Searching the corpus for drilling-problem vocabulary returns 4 weak matches, and
  inspection shows they are not incidents — *"Kick drill"* is a practice drill, not a kick.
- FORCE 2020 is a lithology dataset with no operational events at all.

Training a classifier on manufactured labels would produce a confident-looking number with
nothing behind it. The engine instead combines an unsupervised anomaly score, retrieved
historical evidence and deterministic rules, and calls the result what it is.

Document ingestion is what changes this. As completion reports are processed, genuine
problem/action/outcome records accumulate with confidences and page-level provenance. When
enough categorised events exist, `risk.mode: auto` switches to supervised **on its own** —
the threshold is data-driven, not a decision someone remembers to make.

---

## Registry

Every training run writes a versioned JSON record: dataset and row count, full feature list,
hyperparameters, split summary, measured metrics, training duration, git revision, Python
version and known limitations. `latest.json` points at the newest version per model.

The API serves these records verbatim. If a model has not been trained, `/api/models/{name}`
returns 404 saying so — never a placeholder metric.

## Retraining

Engineer decisions recorded through the UI are stored in `engineer_actions` and marked
`available_for_training`. **Nothing retrains automatically.** Feedback accumulates for a
deliberate retraining run, because a model that silently changes under an operator is worse
than one that is out of date.
