# OIL / eRTMAC fact-check

Checked against Oil India Limited's own website. Search-engine summaries were **not**
treated as evidence; every "Yes" below was read on an official OIL page.

Where something could not be confirmed on an official source it is marked
**Not publicly verified** and left there. It is not filled in with a plausible guess.

---

## Re-verification

Every URL below was fetched again during the final feature audit (2026-09-08) and each
still returns HTTP 200 and still contains the text quoted from it:

- **Technology and Innovation** — contains, verbatim: *"eRTMAC OIL's Real time data
  monitoring and analysis center is equipped with state-of-the-art technologies to monitor
  the various drilling operations in real time with advanced visualization platform and
  cutting-edge sensor technology. It includes transmission of real time critical well data
  to the command center."*
- **Digitalization** — the eRTMAC entry is a modal on the page (`id="ERTMAC"`), titled
  *"Enhanced - Real Time Monitoring & Control Center for Drilling"*, listing *"Central
  command center (RTOC) set-up at FHQ"*, *"Sensor & real-time connectivity set up between
  rig sites and…"* and a visualisation dashboard *"leading to faster anomaly detection, NPT
  reduction, and enhanced drilling performance"*. Because it is a modal rather than a
  separate page, cite the Digitalization URL — there is still no standalone eRTMAC page.
- **Drilling** — returns 200 and mentions eRTMAC.

No claim in the table below changed. Nothing previously marked *Not publicly verified* has
become verifiable.

---

## Official sources used

| Page | URL | What it supports |
|---|---|---|
| Technology and Innovation | https://www.oil-india.com/leveraging-technology | eRTMAC exists; described as OIL's real-time data monitoring and analysis centre; round-the-clock engineer staffing |
| Digitalization | https://www.oil-india.com/index.php/digitalfootprint | "Enhanced – Real Time Monitoring & Control Center for Drilling" under OIL's DRIVE programme; central command centre (RTOC) at FHQ; sensor and real-time connectivity between rig sites and the centre; drilling analytics and visualisation dashboard for faster anomaly detection and NPT reduction |
| Drilling | https://www.oil-india.com/drilling | OIL drilling services, 3000+ wells, RSS/LWD, real-time monitoring centre |

**Most relevant single page for our claims:** the Digitalization page — it is the only one
that ties eRTMAC to *analytics, anomaly detection and NPT reduction*, which is the part
our positioning depends on.

**There is no dedicated public eRTMAC page or public technical documentation.** eRTMAC
appears only as a paragraph within these broader pages. Do not cite a standalone eRTMAC
URL — it does not exist.

---

## Claim-by-claim

| Claim | Correct? | Evidence / source | Notes |
|---|---|---|---|
| OIL is an Indian public-sector oil and gas company | **Yes** | oil-india.com (Drilling page describes it as a major public sector integrated energy company) | Our deck should not state a specific *ratna* status unless we cite the page that says it — not confirmed in the pages read here |
| OIL operates upstream drilling across India and abroad | **Yes** | Drilling page: "more than 3000+ wells till date across Pan India and abroad"; Andaman deep water, KG-DSF | |
| eRTMAC exists | **Yes** | Technology & Innovation page names it | |
| eRTMAC is a real-time drilling monitoring centre | **Yes** | "equipped with state-of-the-art technologies to monitor the various drilling operations in real time with advanced visualization platform and cutting-edge sensor technology" | Direct quote |
| eRTMAC receives real-time well data from rigs | **Yes** | "transmission of real time critical well data to the command center"; Digitalization page: "Sensor & real-time connectivity set up between rig sites and command center for CH rigs" | Note: **CH rigs** specifically |
| eRTMAC is staffed round the clock by engineers who make real-time decisions | **Yes** | "monitored round the clock by Skilled Engineers who analyse the data and make real time decisions" | Directly supports our "engineer stays in control" framing |
| The "e" stands for *Enhanced* | **Yes** | Digitalization page: "Enhanced – Real Time Monitoring & Control Center for Drilling" | The Technology page calls it "Real time data monitoring and analysis center" without expanding the acronym. **The two OIL pages word it differently** — quote whichever you cite, do not merge them |
| eRTMAC is used for anomaly detection / NPT reduction | **Yes** | Digitalization page: drilling analytics software and visualisation dashboard "to enable faster anomaly detection and reduce non-productive time (NPT)" | This is the strongest single fact for our positioning |
| eRTMAC is located at Duliajan | **Not publicly verified** | Search summaries assert it; the official pages read here say "central command center (RTOC) set-up at FHQ" without naming the town | Say "at OIL's field headquarters" or say nothing |
| eRTMAC uses WITSML | **Not publicly verified** | No official OIL page read here mentions WITSML or any data standard | **Do not claim this.** WITSML is a real Energistics standard and our Volve data is WITSML, but that is a fact about *our prototype*, not about eRTMAC |
| eRTMAC is proprietary / internal to OIL | **Not publicly verified** (but strongly implied) | No public API, schema or technical documentation was found | Treat as internal. Do not claim we can integrate today |
| NWIS integrates with eRTMAC today | **No — false** | No integration exists; NWIS has never touched an OIL system | Must be described as *intended target context*, never as existing |
| Norwegian-trained models represent Indian drilling conditions | **No — false** | FORCE and Volve are Norwegian North Sea | Must be stated as a limitation, not omitted |

---

## What is publicly documented vs not

**Publicly documented:** that eRTMAC exists; that it monitors drilling in real time; that
data is transmitted from rig sites; that engineers staff it continuously; that it is part
of the DRIVE digitalisation programme and targets anomaly detection and NPT.

**Not publicly documented:** its data model, protocols or standards; whether it uses
WITSML; its API surface; its internal analytics methods; how third-party software could
integrate; its precise location.

Every integration statement we make must therefore be phrased as an intention conditional
on OIL's own data access — never as a capability we already have.

---

## Correct positioning

```
OIL / eRTMAC   =  the existing operational drilling ecosystem, and the
                  target context NWIS is designed for
NWIS           =  an additional intelligence / decision-support layer

Volve          =  replay and demo telemetry (real, Norwegian, public)
FORCE 2020     =  public geological / lithology development dataset (Norwegian)
```

**NWIS does not replace eRTMAC.** eRTMAC already does real-time monitoring and already has
engineers making decisions. NWIS adds a historical-analogue and evidence-retrieval layer
on top of that — "which past well is relevant here, and what was actually done" — which is
a different question from "what is the rig doing right now".

Wording that is safe:

> NWIS is designed to sit alongside a real-time monitoring centre such as OIL's eRTMAC,
> adding historical analogue intelligence and evidence retrieval. The prototype is built
> and validated on public Norwegian datasets; integration with OIL systems and retraining
> on Indian well data would be the next step and has not been done.

Wording that is **not** safe:

- "NWIS integrates with eRTMAC" — no.
- "NWIS ingests WITSML from eRTMAC" — WITSML use by eRTMAC is unverified.
- "NWIS replaces / upgrades eRTMAC" — no.
- Any accuracy figure presented as applying to Indian wells.
