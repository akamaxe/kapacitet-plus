# Glossary: the product's terms in English, Spanish and Swedish

The interface speaks three languages (`web/src/lib/i18n/{en,es,sv}.ts`). These are the terms every
string uses, so a screen, its printed record and the data labels say the same thing. English is the
reference. **Nobody on the team reads Swedish well enough to review the Swedish:** it follows this
list, and a Swedish-speaking DSO engineer should check it before it is shown to one.

| English | Spanish | Swedish | Note |
|---|---|---|---|
| hosting capacity | capacidad de acogida | acceptansgräns | the term of the Swedish research literature on solar in distribution grids |
| static limit, static study (worst case) | límite estático, estudio estático (peor caso) | statisk gräns, statisk studie (värsta fall) | |
| dynamic operating envelope, envelope | envolvente dinámica, envolvente | dynamisk exportgräns, exportgräns | an export limit per house that changes hour by hour |
| with envelopes | con envolventes | med dynamiska gränser | |
| curtailment, curtailed | recorte, recortado | nedreglering, nedreglerad | of solar export |
| firm connection | conexión firme | fast anslutning | |
| non-firm connection | conexión no firme | villkorad anslutning | |
| conditional connection agreement | acuerdo condicionado | villkorat avtal | Ei's term (Ei2025:01) |
| connection request | solicitud de conexión | anslutningsansökan, ansökan | |
| assessment | evaluación | bedömning | |
| approve, decline | aprobar, rechazar | godkänna, avslå | |
| queue (of approvals) | cola | kö | |
| neighbourhood | barrio | området | |
| secondary substation | centro de transformación | nätstation | |
| transformer | transformador | transformator | |
| feeder | alimentador | utgående ledning | |
| cable cabinet | armario de cables | kabelskåp | |
| service connection | acometida | servisledning | |
| main fuse | fusible principal | huvudsäkring | |
| fuse subscription | potencia contratada (fusible) | säkringsabonnemang | |
| voltage, overvoltage, undervoltage | tensión, sobretensión, subtensión | spänning, överspänning, underspänning | |
| loading, overload | carga, sobrecarga | belastning, överbelastning | |
| reverse power flow | flujo inverso | bakmatning | |
| unbalance | desequilibrio | obalans | |
| solar (PV), inverter | solar (FV), inversor | solceller (solel), växelriktare | |
| heat pump | bomba de calor | värmepump | |
| EV charger | cargador de VE | elbilsladdare | |
| battery | batería | batteri | |
| adoption order | orden de adopción | anslutningsordning | the order in which houses add solar |
| weather year, reference year | año meteorológico, año de referencia | väderår, referensår | |
| spot price, day-ahead | precio spot, el día antes | spotpris, dagen före | |
| forecast, perfect foresight | previsión, previsión perfecta | prognos, perfekt framförhållning | |
| reinforcement | refuerzo | förstärkning | |
| audit trail, annual report | rastro de auditoría, informe anual | granskningsspår, årsrapport | not årsredovisning, which reads as a company's accounts |
| real, synthetic | real, sintético | verklig, syntetisk | the label on every figure's data |
| DSO | distribuidora | nätbolag | |
| screen (of the interface) | pantalla | vy | as in "Gå till en vy" |
| application (to connect) | solicitud | ansökan | an assessment answers "what if"; an application asks for the connection |
| reference code | código de referencia | referenskod | how a household follows its application, without personal data |
| decided by the rule | decidida por la regla | beslutad av regeln | the DSO's automatic rule, which never declines |
| shadow operation | operación en sombra | skuggdrift | limits issued every day as if sent, judged without sending them |
| schedule (of limits) | horario | schema | a house's 24 limits for one day |
| issued the day before | emitida el día antes | utfärdad dagen före | the basis an operator can send |
| default control (no schedule) | límite sin horario | gräns utan schema | DefaultDERControl in IEEE 2030.5 |
| meter check | comprobación del contador | mätarkontroll | |
| evidence (for Ei) | pruebas | underlag | evidence, never a legal assessment |
| imported network | red importada | importerat nät | a network built from an operator's export |

Money is always **SEK**, never "kr", in every language. Numbers use a narrow no-break space for
thousands and each language's own decimal mark (a comma in Spanish and Swedish).
