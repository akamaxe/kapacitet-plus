# Metodunderlag för villkorade avtal

| | |
|---|---|
| Gäller | {{subject}} |
| Skapat | {{generated_at}} |
| Resultaten beräknade | {{computed_at}} |
| Metodversion | {{method_version}} |
| Programvara | Kapacitet+ {{code_version}}, incheckning {{code_commit}} |

Detta underlag beskriver hur Kapacitet+ beräknar ledig kapacitet och dynamiska exportgränser för
villkorade avtal. Enligt {{ei_document}} ska metoden vara godkänd innan något avtal tecknas:
{{ei_method}}. {{approval}}

Varje värde i texten är hämtat ur programmets kod, antagandena i `config/assumptions.toml` och de
beräknade resultaten; inget är inskrivet för hand. `method_dossier.json` listar varje värde med sin
källa. {{kind_note}}

Underlaget redovisar vad modellen visar och vad nätbolaget har angett. Det är aldrig en juridisk
bedömning: om villkoren är uppfyllda avgör Ei.

Språket har inte granskats av någon med svenska som modersmål: granska texten innan den skickas
eller publiceras.

## Vad som beräknas

{{computed}}

## Hur exportgränserna fördelas

En kund som ansluts inom den statiska gränsen har en fast anslutning och begränsas aldrig. Senare
kunder har en villkorad anslutning: varje timme får de en exportgräns, så att nätet håller sina
gränser även om alla villkorade kunder exporterar upp till sin gräns samtidigt. Det som får
exporteras delas mellan dem enligt en av fyra regler:

{{allocation}}

## Verifiering

{{verification}}

## Nedregleringsbudget

{{budget}}

## Planeringsläge dagen före

{{dayahead}}

## Omplanering under dygnet och resultattavla

{{intraday}}

## Aktivering efter dagen före-marknaden och marknadsbaserade alternativ

{{market}}

## Rapport om icke-marknadsbaserad omdirigering

{{redispatch}}

## Mellanspänningen

{{mv}}

## Beviskedja

{{evidence}}

## Data

{{data}}

## Begränsningar

{{limitations}}
