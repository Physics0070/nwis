# Demo Script

A ten-minute walkthrough of the complete decision-support loop, using only real data.

**Before you start**

```bash
uvicorn backend.app.main:app --reload      # terminal 1
cd frontend && npm run dev                 # terminal 2
```

Open <http://localhost:5173>. Check the header badge: it states whether the Postgres stack
or the local storage backend is active. Say which one you are on — it is a strength, not
something to hide.

---

## 1 · Overview — this is a real knowledge base (1 min)

The dashboard loads from `/api/status`. Point at the counts:

> 101 wells, 86,800 telemetry samples, 331 historical events, 3,620 segment embeddings,
> 562 searchable report passages — and **101 of 101 wells have a real surveyed position**.
> Not one is placed at a guess.

Scroll to **System notes**. The interface volunteers its own limitations.

> A dashboard that tells you what it cannot do is more trustworthy than one that never
> admits a gap.

---

## 1b · The simulator — the whole loop in one screen (3 min)

Open **Simulator** and select **NO 15/9-F-4**. Press **Start**, then drag the position
scrubber to roughly sample **43,400 of 59,806**. The recording sits near the surface for
its first quarter, and the scrubber is there so a demo does not have to wait it out.

The bit lands at about **2,375 m**. Read the intelligence panel top to bottom — it is the
entire product in one column:

> A LOW indicator, and no probability, because there are no labelled incidents to calibrate
> one against.
>
> Three components, each with its own sentence. The anomaly score comes from an Isolation
> Forest, and underneath it the readings that were furthest from normal for this sample.
> The rules component names the channel and the limit it crossed.
>
> The **historical risk radar** places the bit on the engine's own 150 m look-ahead window
> and puts each historical record at its real depth offset. Here it reports a **fishing
> operation recorded in 16/7-5, 7 m behind the bit** — a well 15 km away, retrieved
> because of where it sits in the hole, not because it is near.

Click **Investigate**. The replay pauses — the evidence was retrieved for one depth, and
letting the bit run on behind the drawer would leave you reading about a depth the well has
already passed.

The drawer is the reasoning chain: analogue well and its similarity, what happened there,
what was done about it, what came of it, and the page of the report it came from. Press
**Search reports** to pull passages about this event out of the scanned corpus, each with
its page citation. Record a decision at the bottom; the confirmation names the alert it was
filed against. Close the drawer and the replay resumes.

Worth saying plainly:

> Every one of those numbers is a stored measurement or a retrieved record. The only thing
> the simulator invents is the passage of time.

**A note on what the panel will sometimes say.** At many depths it reports that risk could
not be evaluated, or that no telemetry lies within 25 m of the queried depth. That is the
system refusing to describe a depth it has no measurement for, rather than borrowing a
reading from elsewhere in the hole. Show it — it is the point.

---

## 2 · Active well — live telemetry from real recordings (2 min)

Open **Wells → NO 15/9-F-4**.

Press **start** on the replay panel and raise speed to ~600×.

Watch the KPI row and the chart. Then make the key point:

> Every point on this chart is a row stored in the database, with its original 2016
> timestamp. The replay engine plays back real recorded telemetry — it does not generate
> signals. The label under the panel says *Volve WITSML replay*, so nobody can mistake it
> for a live rig feed.

Press **pause**, then **resume**. State moves because the *server* owns it — two people
watching this well see the same position.

---

## 3 · The offset map — computed in the database (1 min)

The map shows the active well, offset wells and ranked analogues in a distinct colour.

> Proximity is a PostGIS query on the server, or an equivalent SQL haversine on the
> fallback. The frontend never computes a distance. Coordinates came from the Norwegian
> Offshore Directorate, because the WITSML files ship with the positions zeroed — so rather
> than invent them, we sourced them.

Worth adding:

> Volve resolves to 58.441 °N, 1.886 °E and the nearest FORCE well is 3.6 km away in the
> same licence block, 15/9. Two independent datasets, two independent coordinate sources,
> agreeing. That is how we know the projection is right.

---

## 4 · The analogue engine — the part that is not a distance ranking (2 min)

**This is the centrepiece.** Scroll to *Contextual analogue wells*.

Each match shows its component scores: geology, formation, depth, drilling behaviour,
geography — with the configured weights in the subtitle.

The strongest demonstration is on a well with logs. Open **15/9-13** and set depth ~2500 m:

> `16/10-1` ranks in the top five at **36 km away**. Its geography score is 0.2372 — it is
> one of the *furthest* candidates. It ranks because its geology similarity is 0.9873, the
> highest of any well, and it shares the Tor Formation.
>
> That is the difference between "nearby wells" and "relevant wells".

Then point at `dimensions_unavailable` on a Volve match:

> This well has telemetry but no wireline logs, so geology could not be computed. The
> system says so and renormalises the remaining weights, instead of quietly scoring it on
> fewer signals and presenting the number as equivalent.

---

## 5 · Risk and evidence — what the system will not claim (2 min)

Look at the **Risk state** panel, then read the line beneath the score:

> *No calibrated probability: this is a historical risk indicator, not a supervised
> prediction.*

Explain why that sentence exists:

> There are no labelled drilling incidents in either public dataset. We searched — the only
> vocabulary match in the Volve remarks is "Kick drill", which is a practice drill, not a
> kick. A supervised risk model trained on manufactured labels would print a confident
> number with nothing behind it. So the engine combines an unsupervised anomaly score,
> retrieved historical evidence and deterministic rules, reports `probability: null`, and
> calls itself an indicator.
>
> The mode is chosen by the data, not by us. When enough categorised events exist, it
> switches to supervised on its own.

Scroll to **Historical evidence near this depth** — real records from a real analogue well,
with distance from the bit and the note that depth was recovered by time-join.

---

## 6 · Alert explanation and the feedback loop (2 min)

Open **Alerts → any alert**.

The page answers each question the brief demands: what, why, which wells, at what depth,
which measurements, what happened historically, what was done.

> The evidence here is read back from the assessment stored when the alert fired — not
> recomputed. The engineer sees exactly what the system acted on.

Where no mitigation exists, the page says **"No validated historical mitigation found."**

> NWIS never generates an engineering recommendation. It retrieves one, or it says there
> isn't one. A decision-support tool that invents a procedure is worse than no tool.

Record a decision at the bottom, submit, and note the confirmation:

> Stored as institutional memory and made available for future training — and nothing
> retrains automatically. A model that silently changes under an operator is worse than one
> that is out of date.

---

## 7 · Report search — institutional memory that cites its source (2 min)

Open **Reports**. Type: `stuck pipe and fishing operations`.

> These are passages from 1980s scanned completion reports, OCR'd at 0.957 confidence.
> The top hit is a real fishing operation: an angle iron dropped in the hole, a reverse
> circulating basket run to retrieve it, and **17½ hours of rig time lost**. Every result
> carries the well, the document and the page number.

Make the boundary explicit:

> This retrieves passages. It does not summarise them and there is no language model
> writing prose about your well. The engineer reads what the report actually says, and the
> citation tells them which page to open. A generated summary would be an unciteable claim.

Now type something the corpus does not contain — `orbital mechanics`:

> Zero results, and the interface says the nearest passage did not clear the similarity
> threshold. It would rather return nothing than hand you the least-bad paragraph.

Worth stating if asked how the corpus was built:

> The first 30 pages of these reports are geological sample descriptions. The drilling
> narrative is deeper in, which is where the recorded actions live — real actions, each
> traceable to a page. Check the live counts before the demo rather than quoting a figure
> from this document; the search panel prints how many of the stored passages are indexed
> on every query.
>
> It also exposed two extraction bugs worth admitting: "No tight spot" was being stored as
> a tight-hole *event*, and a leak-off test — a planned integrity test — was being recorded
> as an equipment failure. Both are now excluded, and the count of what was excluded and
> why is reported rather than hidden. 37 mentions were dropped.

---

## 8 · Model insights — the numbers behind the claims (1 min)

Open **Models**.

> Every figure here was measured by a training run and written to a registry entry with a
> timestamp and git revision. Lithology was evaluated on 10 wells it has never seen — the
> competition's own holdout, not a split we chose.

Scroll to the per-class table and point at the zeros:

> Chalk, Dolomite and Anhydrite score 0.000. We are showing you the failures. Chalk is
> predicted as Limestone 99.8% of the time — and that is geologically coherent, because
> chalk *is* a fine-grained limestone and the two are near-identical on these curves.
>
> The model card says NWIS must not present a Limestone prediction as evidence that an
> interval is not chalk. That is the kind of statement that makes the rest of the numbers
> believable.

---

## Questions you should expect

**"Is this just a distance ranking?"**
No. Open a FORCE well with logs — `16/2-16` at 1,100 m ranks `31/2-9`, **231 km away**,
fourth, on geology 0.801 and a shared formation, with a geography component of **exactly
zero**.

Be straight about the other side of this: on the Volve replay wells the ranking *cannot* be
contextual, because those wellbores have no wireline logs and no stratigraphy. The
simulator prints which dimensions actually contributed and which were unavailable, so the
degradation is visible rather than hidden. What still moves with depth on those wells is
the historical evidence and the radar, which is the correlation the simulator is about.

**"Are those 'historical events' actually risks?"**
Only categorised ones count. The Volve WITSML stream is an operations log — most of its
records are remarks like "Toolbox Talk Prior to Rig Up Tubing Equipment". Counting those
would manufacture a risk signal out of routine housekeeping, so the engine ignores
uncategorised remarks. They stay visible on the borehole track as recorded events; they do
not raise an indicator.

**"What's your accuracy?"**
Read the figure off the **Models** page rather than from this document — it is written by
the training run, and it moves when the model is retrained. At the time of the last audit:
**0.7523 accuracy on the ten unseen FORCE holdout wells**, macro F1 **0.3848**. Accuracy is
the wrong headline on a dataset that is 61% shale, which is why the majority baseline is
shown beside it and the per-class table shows exactly where the model fails.

**"Which model did you pick, and why?"**
RandomForest, on validation wells only — a rule fixed before we looked. XGBoost scores
better on the external holdout, but selecting on the holdout *is* selecting on the test
set, so we did not. The registry records the disagreement rather than hiding it, and it
reproduced on the audit rebuild: RandomForest 0.3847 validation macro F1 against XGBoost
0.3335, with XGBoost ahead on the holdout at 0.3848 to 0.3345. We then ran grouped cross-validation over 83 wells to settle it:
XGBoost 0.3840 ± 0.0543, RandomForest 0.3667 ± 0.0462, p = 0.224.

The honest finding is that **the two models are not separable** — the spread between folds
is 3.1× the difference between the models. The original disagreement was never a contest
between models, it was the variation between draws of wells. So we report that, and we did
not swap the served model on noise.

**"Why no risk probability?"**
No labelled incidents exist, so a probability would be fabricated. The mode is data-driven
and flips to supervised automatically when the evidence supports it.

**"Is the frontend real?"**
Every value routes through `src/lib/api.ts`. Stop the backend and the UI shows error and
unavailable states — it does not fall back to placeholder numbers. That is a fair thing to
demonstrate live.

**"Does it work on Indian wells?"**
Not yet, and the model card says so. It is trained on Norwegian North Sea data; behaviour
elsewhere is unverified until retrained locally. The pipeline is basin-agnostic — the
lithology model is not.

---

## Reset between runs

```bash
python -m data_pipeline.load_database --reset
python -m data_pipeline.load_embeddings
python -m data_pipeline.documents.embed        # rebuild the passage search index
```

## Controls worth knowing before you present

Three pages are deliberately read-only — **Overview**, **Alerts** and **Models** are
reporting surfaces with no buttons. Everything interactive lives here:

| Page | Control | What it does |
|---|---|---|
| Wells | search box, Previous / Next | paginates 101 wells, 25 at a time |
| Active well | Start · Pause · Resume · Stop | server-owned replay of real telemetry |
| Active well | speed slider, context depth | 1×–3600×; depth drives geology, analogues, risk |
| Alert explanation | Record decision | writes an engineer action to the database |
| Reports | search box, example chips | semantic search over 562 passages |

`Previous` is greyed out on page 1 of Wells — that is correct, not a broken button.
