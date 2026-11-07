# Grunder för beräkning av tillgänglig kapacitet

{{status}}

| | |
|---|---|
| Nätföretag | {{dso_name}} |
| Studie | {{study_id}} |
| {{period_label}} | {{quarter}} |
| Publicerad | {{published_at}} |
| Metodversion | {{method_version}} |
| Programvara | Kapacitet+ {{code_version}}, incheckning {{code_commit}} |

## Vad som publiceras

För varje nätstation anges hur mycket ny produktion (solceller) och ny förbrukning
({{consumption_device}}) som kan anslutas till lågspänningsnätet bakom stationens transformator
utan att någon av gränserna nedan överskrids under någon timme. Värdet gäller stationen som helhet,
inte en enskild anslutningspunkt, och ersätter inte nätföretagets prövning av en ansökan om
anslutning.

Publiceringen omfattar {{stations_published}} av studiens {{stations_total}} nätstationer, i
{{cells}} celler. Dolda stationer: {{stations_hidden}}. Stationer som inte publiceras därför att
de har för få kunder även sammanslagna: {{stations_suppressed}}.

## Underlag

- Nät: lågspänningsnätet som nätföretaget har lämnat, en nätstation i taget, med kablar,
  kabelskåp, servisledningar, säkringar och transformator.
- {{equipment}}
- {{load_basis}}
- Väder: väderåren {{weather_years}}, {{weather_source}}.
- Tid: varje timme av varje väderår, i svensk normaltid.

## Gränser

- Spänningen hos varje kund ligger mellan {{v_min_pu}} och {{v_max_pu}} p.u., med en
  säkerhetsmarginal på {{v_margin_pu}} p.u.
- Belastningen på kablar och transformator är högst {{thermal_limit_pct}} procent av märkvärdet.
- Mellanspänningen vid stationen kan ligga var som helst mellan {{mv_low_pu}} och {{mv_high_pu}}
  p.u. ({{mv_source}}).
- Lastens effektfaktor är {{load_power_factor}}.

## Ny produktion

Solceller läggs till ett hus i taget, bland de hus som har tak och saknar solceller, i
{{orders}} slumpade ordningar. För varje ordning räknas hur många hus som får plats innan någon
gräns överskrids någon timme, vid båda ändarna av spänningsbandet, och det lägre värdet gäller.
Stationens värde är percentil {{percentile}} av ordningarna, avrundad nedåt till en verklig
ordning. Den utbyggnaden kontrolleras sedan med en fullständig lastflödesberäkning för varje
timme vid båda ändarna, och minskas tills den klarar varje timme.

## Ny förbrukning

{{consumption_device_capitalised}} läggs till ett hus i taget på samma sätt, vid den lägre änden
av spänningsbandet, där spänningsfallet och strömmen genom transformatorn är störst. Ett nät som
redan i dag överskrider en gräns får ingen kapacitet: det redovisas som ett fynd med antalet
timmar, aldrig som noll.

## Trefaskontroll

{{three_phase}}

## Gränser längre upp i nätet

{{upstream}}

## Villkorad kapacitet

{{conditional}}

## Granskning och korrigeringar

{{corrections}}

## Sekretess

{{disclosure}}

## Antaganden som studien har ändrat

{{overrides}}
