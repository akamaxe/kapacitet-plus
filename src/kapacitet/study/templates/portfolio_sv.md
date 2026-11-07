# Portfölj av nätstationer, {{quarter}}

UNDERLAG framtaget av Kapacitet+ för {{dso_name}}: internt, inte för publicering, och inte ett
beslut. Kapacitet+ föreslår endast: varje beslut fattas av en person hos nätföretaget.

{{stations}} nätstationer, den med minst marginal per kund först. Marginalen är den minsta av den
publicerade effekten för ny produktion och för ny förbrukning ({{device}}), efter begränsningarna i
överliggande nät och granskarens rättelser, delad med antalet kunder. En station utan värde på
någon sida står först.

{{table}}

Så läses kolumnerna:

- Produktion vid låg / hög mellanspänning: stationens effekt för ny produktion vid bandets två
  ändar; den lägre gäller.
- Timmar med överträdelse i dag: timmar då dagens nät redan går över en gräns, summerat över
  väderåren.
- Uppmätt topp mot Velander: den uppmätta toppen hos de mätta kunderna delad med Velanders
  uppskattning för samma kunder; över ett betyder att Velander underskattar toppen.
- Väntande ärenden: ärenden som har tagits emot och inte är kända som beslutade.
- Scenario: stationen framskriven år för år från sin egen utrustning och sina mätare
  (`kapacitet study scenarios`), med nätföretagets tillväxttakt eller standardvärdena i
  config/assumptions.toml (kolumnen scenarios_rates säger vilka). Värdet är det sista årets
  kvarvarande effekt för ny produktion och ny förbrukning, före begränsningarna i överliggande nät,
  och det första år då det framskrivna nätet går över en gräns, när det gör det. Visas bara när
  scenariot är beräknat på stationens nuvarande beräkning.

Varje värde kommer från stationens egna resultat; maskinläsbara värden finns i portfolio.machine.csv.
