# Product

<!-- impeccable:product-schema 1 -->

## Platform

web

## Users

Two users on either side of one connection, served equally:

- **The connection planner at a Swedish distribution system operator (DSO).** They work through a queue of
  applications for solar, EV chargers, heat pumps and batteries. Their job is to decide whether a connection
  fits, on what terms, and to leave evidence behind for the decision.
- **The applicant** — a household, a solar or battery developer, a large consumer — who is offered a non-firm
  connection and has to judge it: how much of their energy would be curtailed, and what that costs them. A
  household has its own small page, the applicant's portal (`#/apply`): pick the address, ask, read the answer
  in plain words, apply, and follow the application by a reference code, without giving a name or contact
  details (the application keeps the address and what it asks for).

## Product Purpose

Kapacitet+ is a digital twin of one Swedish low-voltage neighbourhood, Smedslätten in Bromma (Stockholm, SE3):
120 real villas around the substation OpenStreetMap maps at Smedslättsvägen 71A. It puts two connection
studies side by side: the conservative worst-case hosting capacity DSOs use today, and the capacity unlocked by
Dynamic Operating Envelopes (DOE), export and import limits that vary hour by hour and per customer.

Success means a viewer can see how much more solar the same grid takes with envelopes and what that costs the
customers who join beyond the static limit. They can also test one house's request and get a verdict with its
evidence.

## Positioning

Every envelope is verified hour by hour with a full nonlinear power flow, and only verified envelopes are
published. The interface shows both sides of the same connection: the DSO's decision and the applicant's
curtailment and cost. Incumbents such as envelio sell to the DSO. Almost no tool lets the applicant value a
non-firm offer.

## Operating Context

- Eight screens:
  - Sweden: national production, consumption and prices.
  - Neighbourhood: the twin at any hour of the reference year, under three scenarios.
  - Static vs DOE: the central comparison, what the extra solar is worth, and what deferring the
    reinforcement is worth to the DSO.
  - Connection request: evaluate one house's request live (solar, EV charger, heat pump, battery), keep
    the answer as a printable, sealed record, value connecting now against waiting, apply, and decide on it
    in a queue of approvals.
  - Operation: the applications, each with the DSO's rule's proposal (advisor mode by default: a person
    decides, unless the DSO switched the rule on and signed it for that kind of request; it never declines),
    a form to sign the rule, each day's limits issued the
    day before and the file issued for an inverter, the summer replayed in the shadow against a pilot's
    tests, and a meter checked against the limits issued for it.
  - Annual report: the year's filing for conditional connection agreements on either basis and under any
    sharing rule, its hourly audit trail, each house's own record, and the evidence for Ei (Ei2025:01).
  - 2030: adoption scenarios.
  - Method & data: sources, inputs, validation, protection, forecasts, and other networks imported and
    switched to.
- The applicant's portal (`#/apply`) is its own small page, without the application's chrome.
- URLs describe what is on screen: `#/<screen>?hour=&scenario=&cid=`, plus `job=` for a saved assessment,
  `basis=`/`policy=` on the report, `date=` on the operation screen and `ds=` on an imported network.
- Keyboard shortcuts: 1–8 for the screens, Ctrl+K for search, ? for the key sheet, F for full screen.
- Light and dark themes.
- Used mostly on a desktop or laptop, also projected in meetings. It must still work on a phone.

## Capabilities and Constraints

- The pipeline fetches, builds, profiles and computes. The API reads `data/processed/results/`, runs the live
  request evaluation and the inbox's rule, and writes only to `data/requests/` (assessments, the queue,
  applications, settings and the seal chain) and `data/datasets/` (imported networks). The one
  exception is an empty copy whose server allows it (a codespace): its setup page takes the
  data bundle from the browser and unpacks it into `data/processed/` and `data/raw/`.
- **Figures live in the data, never in the interface.** Every number on screen comes from the API. The code
  holds only units and labels.
- **Real versus synthetic is labelled on every screen.**
  - Real: buildings, streets, weather, prices, national volumes.
  - Synthetic: the cable network, the transformer, household behaviour.
- **Attribution is mandatory:** OpenStreetMap (ODbL), SMHI (CC BY), and "Elpriser tillhandahålls av Elpriset
  just nu.se".
- Every interface string goes through `web/src/lib/i18n/` in English and Swedish, with the terms of `docs/glossary.md`; the Swedish has not yet been reviewed by a native speaker, and the glossary says so.
- MIT licence for the code. The computed data are never committed; they are published separately as the
  `data` release's archive, under ODbL (`DATA-LICENSE.md`).
- Stack: React 19, Vite, TypeScript, zustand, react-query, ECharts (modular), MapLibre GL, plain CSS. FastAPI
  backend.
- Terminology:
  - hosting capacity
  - static (worst-case) study
  - dynamic operating envelope / envelope
  - curtailment
  - firm / non-firm connection
  - feeder
  - pu (per-unit voltage)
  - kWp
  - adoption order
  - weather year
  - bidding zone SE1–SE4
  - CVaR 95 %

## Brand Commitments

- The name **Kapacitet+**. The "+" sign and its colour are open to redesign.
- English and Swedish throughout; the sentences the engine and the API return carry a code, so they are said in both too.

## Evidence on Hand

- The repository's own simulation results in `data/processed/results/`, including:
  - static against DOE capacity, as a median over 20 adoption orders and 11 weather years;
  - the curtailment curve, verification, fairness, sensitivity, 2030 scenarios, forecast, validation and risk.
- Real sources: OpenStreetMap, SMHI STRÅNG and metobs, elprisetjustnu.se (ENTSO-E when a token exists), eSett,
  SCB.
- There are no customers, users, testimonials, pilots or press. None may be shown or implied. The inverter's
  file is modelled on CSIP-AUS v1.2 and the EV charger's profile on OCPP 1.6J; neither is certified, and no
  device is connected.
- Public figures the business case uses, each with its source in `config/assumptions.toml`: Ei's real
  rate for 2024–2027 and its norm values, the energy tax from 2026, Ellevio's grid fee and compensation for
  exported solar, and the end of the tax reduction for exported solar (Skatteverket).
- No patented or proprietary capacity-sharing algorithm exists, and one must never
  appear.

## Product Principles

1. Show the evidence beside the answer: a verdict always comes with the hours, limits and sources it rests on.
2. One connection, two sides: whatever the DSO sees about a request, the applicant can read too.
3. Say what is measured and what is assumed, every time.
4. Plain words first. A viewer who has never heard of hosting capacity can follow every screen, with terms
   defined where they appear.

## Accessibility & Inclusion

- WCAG 2.1 AA contrast in both themes.
- A complete keyboard path.
- Colour never carries meaning alone.
- `prefers-reduced-motion` honoured.
- Readable when projected: large enough type and strong enough contrast for a lit meeting room.
