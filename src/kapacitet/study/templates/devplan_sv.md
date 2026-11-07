# Underlag för nätutvecklingsplanen: {{dso_name}}

UNDERLAG framtaget av Kapacitet+ för {{dso_name}}, {{period_word}} {{quarter}}: internt, inte en projektering
och inte ett beslut. Filerna är tänkta som indata till den nätutvecklingsplan som {{regulation}}
beskriver. Kapacitet+ påstår inte att de uppfyller föreskriftens krav: det prövar nätföretaget.

Underlaget omfattar {{stations}} nätstationer, även de som studien inte publicerar. Referensår:
{{reference_year}}. Väderår: {{years}}. {{scenario}}

## Filerna

Varje tabell finns två gånger: `<namn>.csv` för Excel på en svensk dator (semikolon och
decimalkomma) och `<namn>.machine.csv` för program ({{machine_dialect}}). Ett tomt fält betyder att
värdet saknas, aldrig noll.

| Fil | Innehåll |
|---|---|
| `profiles/<station>.parquet` och `.csv` | Nätstationens last och produktion timme för timme under referensåret (kW), med dagens utrustning och ingen ny ansluten, och för varje timme {{p_median}} och {{p_high}} över väderåren. Tid vid timmens början, i UTC och i svensk normaltid. Årets summa är nätstationens energi. |
| `profiles/study.parquet` och `.csv` | Detsamma för hela studien: stationernas summa varje timme och väderår, och percentilerna av den summan, inte summan av percentilerna. |
| `flexibility.csv` | Flexibilitetsbehovet per nätstation och för hela studien, i dagens nät och i scenariot: timmar per år då en gräns överskrids och den energi (kWh per år) som måste kopplas bort (produktion) eller flyttas (förbrukning) för att hålla gränserna, med den begränsande komponenten och den publicerade kapaciteten som jämförelse. Medel och högsta värde över väderåren, och referensåret. Timmar som ingen flexibilitet löser redovisas för sig. För hela studien summeras energin; timmarna gäller per station. |
| `investments.csv` | Investeringslistan för de kommande {{horizon}} åren ({{first_year}}–{{last_year}}) ur `study options`: åtgärden, året den behövs och varför, och kostnaden när nätföretaget har angett den i study.toml [costs], annars "not entered". |

## Hur det är beräknat

Varje timme under varje väderår är löst med det icke-linjära lastflödet. Produktionen prövas vid
båda ändar av mellanspänningsbandet och den ände som kräver mest redovisas; förbrukningen vid den
lägre. Flexibiliteten är lika stor andel av varje kunds solproduktion (produktion), eller av
värmepumpens och elbilsladdningens last (förbrukning), den timmen.

Behovet uppstår med den utrustning som respektive tillstånd har (dagens, eller scenariots), inte med
den publicerade kapaciteten, som bara redovisas som jämförelse. Timmar som ingen flexibilitet löser
(status `unresolved`) räknas inte i energin: noll energi tillsammans med sådana timmar betyder att
gränsen inte kan hållas, inte att behovet saknas.

Kapacitet+ föreslår endast: varje beslut fattas av en person hos nätföretaget.
