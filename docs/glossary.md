# Glossary: the product's terms in English and Swedish

The interface speaks two languages (`web/src/lib/i18n/{en,sv}.ts`). These are the terms every
string uses, so a screen, its printed record and the data labels say the same thing. English is the
reference. **The Swedish has not yet been reviewed by a native speaker:** it follows this list, and a
Swedish-speaking DSO engineer should check it before it is shown to one.

| English | Swedish | Note |
|---|---|---|
| hosting capacity | acceptansgräns | the term of the Swedish research literature on solar in distribution grids |
| static limit, static study (worst case) | statisk gräns, statisk studie (värsta fall) | |
| dynamic operating envelope, envelope | dynamisk exportgräns, exportgräns | an export limit per house that changes hour by hour |
| import limit (of an EV charger) | importgräns | the import-side envelope, issued the day before |
| planning ceiling | planeringstak | the most a house is assumed to import when its import limit is set (the demand the other weather years reached near that date, plus a margin, with no solar); an hour it imports more is an hour over it |
| with envelopes | med dynamiska gränser | |
| curtailment, curtailed | nedreglering, nedreglerad | of solar export |
| firm connection | fast anslutning | |
| non-firm connection | villkorad anslutning | |
| conditional connection agreement | villkorat avtal | Ei's term (Ei2025:01) |
| connection request | anslutningsansökan, ansökan | |
| assessment | bedömning | |
| approve, decline | godkänna, avslå | |
| sign in, sign out | logga in, logga ut | sign-in with the DSO's identity provider (docs/sso.md) |
| planner, approver, admin, auditor | planerare, beslutsfattare, administratör, granskare | the four roles sign-in grants |
| case handler | handläggare | whoever decides an application in the DSO; the role that may do it is approver |
| queue (of approvals) | kö | |
| neighbourhood | området | |
| secondary substation | nätstation | |
| transformer | transformator | |
| feeder | utgående ledning | |
| cable cabinet | kabelskåp | |
| service connection | servisledning | |
| main fuse | huvudsäkring | |
| fuse subscription | säkringsabonnemang | |
| voltage, overvoltage, undervoltage | spänning, överspänning, underspänning | |
| loading, overload | belastning, överbelastning | |
| reverse power flow | bakmatning | |
| unbalance | obalans | |
| solar (PV), inverter | solceller (solel), växelriktare | |
| heat pump | värmepump | |
| EV charger | elbilsladdare | |
| battery | batteri | |
| adoption order | anslutningsordning | the order in which houses add solar |
| weather year, reference year | väderår, referensår | |
| spot price, day-ahead | spotpris, dagen före | |
| re-issued during the day (intraday), gate | utfärdat på nytt under dygnet, tidpunkt | envelopes re-planned at fixed hours with the latest meter readings |
| forecast, perfect foresight | prognos, perfekt framförhållning | |
| quantile forecast, exceedance | kvantilprognos, överskridande | the intraday forecast variant; how often the year beat it |
| reinforcement | förstärkning | |
| audit trail, annual report | granskningsspår, årsrapport | not årsredovisning, which reads as a company's accounts |
| real, synthetic | verklig, syntetisk | the label on every figure's data |
| DSO | nätbolag | |
| settings (of the DSO) | inställningar | changed in Operation › Settings; every change is sealed |
| screen (of the interface) | vy | as in "Gå till en vy" |
| application (to connect) | ansökan | an assessment answers "what if"; an application asks for the connection |
| reference code | referenskod | how a household follows its application, without a name or contact details |
| decided by the rule | beslutad av regeln | the DSO's automatic rule, which never declines |
| shadow operation | skuggdrift | limits issued every day as if in force, judged without delivering them |
| schedule (of limits) | schema | a house's limits for one day, one per interval (a quarter-hour by default) |
| issued the day before | utfärdad dagen före | the basis an operator can issue |
| default control (no schedule) | gräns utan schema | DefaultDERControl in IEEE 2030.5 |
| meter check | mätarkontroll | |
| evidence (for Ei) | underlag | evidence, never a legal assessment |
| day-ahead market result | resultatet från dagen före-marknaden | no schedule is issued before it |
| market alternatives (consulted) | marknadsbaserade alternativ (som undersökts) | flexibility markets, bilateral flexibility, tariffs |
| non-market-based redispatch | icke-marknadsbaserad omdirigering | EU 2019/943, art. 13; here, curtailed solar |
| imported network | importerat nät | a network built from an operator's export |
| project | projekt | one low-voltage network with its studies: what the start window lists and opens |
| start window | startfönster | what one sees on entering: the projects, and the way to make one |
| example network | exempelnät | one of pandapower's low-voltage benchmark networks; synthetic, and says so |
| project file | projektfil | a project's tables and what it is, as one .zip; results are computed again where it is opened |
| menu bar; File, View, Study, Results, Tools, Help | menyrad; Arkiv, Visa, Studie, Resultat, Verktyg, Hjälp | the menus of the workbench |
| project tree | projektträd | the network, the studies, the results and the reference, down the left |
| output window, output | utdatafönster, utdata | what the program did, with its time |
| status bar | statusrad | the server, the project and its year, real against synthetic, language and keys |
| computation status | beräkningsstatus | what is computed for a project, and the command that computes each part |
| build (of a project) | bygge | the process that computes a project made in the app |

Money is always **SEK**, never "kr", in every language. Numbers use a narrow no-break space for
thousands and each language's own decimal mark (a comma in Swedish).
