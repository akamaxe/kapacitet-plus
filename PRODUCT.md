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
  connection and has to judge it: how much of their energy would be curtailed, and what that costs them.

For the next weeks the product is also seen by people who are not either of those:

- projected in the course seminar (KTH EI2600), to an examiner and a class reading it from a distance in a lit
  room;
- on the author's laptop (1536×730 CSS px at 125 % scaling) while the author explains it;
- by two teammates who open it on their own without knowing the product, and have to understand it unaided.

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

- Six screens:
  - Sweden: national production, consumption and prices.
  - Neighbourhood: the twin at any hour of the reference year, under three scenarios.
  - Static vs DOE: the central comparison.
  - Connection request: evaluate one house's request live.
  - 2030: adoption scenarios.
  - Method & data: sources, inputs and validation.
- URLs describe what is on screen: `#/<screen>?hour=&scenario=&cid=`.
- Keyboard shortcuts: 1–6 for the screens, Ctrl+K for search, ? for the key sheet, N for notes, F for full
  screen.
- Light and dark themes.
- Used mostly on a laptop and on a projector. It must still work on a phone.

## Capabilities and Constraints

- The pipeline fetches, builds, profiles and computes. The API only reads `data/processed/results/`, plus the
  live request evaluation.
- **Figures live in the data, never in the interface.** Every number on screen comes from the API. The code
  holds only units and labels.
- **Real versus synthetic is labelled on every screen.**
  - Real: buildings, streets, weather, prices, national volumes.
  - Synthetic: the cable network, the transformer, household behaviour.
- **Attribution is mandatory:** OpenStreetMap (ODbL), SMHI (CC BY), and "Elpriser tillhandahålls av Elpriset
  just nu.se".
- Every interface string goes through `web/src/lib/i18n.ts` in English and Spanish.
- MIT licence. The data is not published.
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
- Bilingual English and Spanish throughout.

## Evidence on Hand

- The repository's own simulation results in `data/processed/results/`, including:
  - static against DOE capacity, as a median over 20 adoption orders and 11 weather years;
  - the curtailment curve, verification, fairness, sensitivity, 2030 scenarios, forecast, validation and risk.
- Real sources: OpenStreetMap, SMHI STRÅNG and metobs, elprisetjustnu.se (ENTSO-E when a token exists), eSett,
  SCB.
- There are no customers, users, testimonials, pilots or press. None may be shown or implied.
- The "patented fair capacity-sharing algorithm" in the team's Lean Canvas does not exist and must never
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
- Readable when projected: large enough type and strong enough contrast for a lit seminar room.
