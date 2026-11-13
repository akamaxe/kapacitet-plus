# Kort handbok för planerare och handläggare

Den här handboken är för nätbolagets planerare och handläggare. Den beskriver vad varje vy i
Kapacitet+ är till för, hur en ansökan går från mottagen till beslutad, hur kapaciteten
publiceras, och var nätbolagets inställningar och bevis finns.

> **Om språket.** Ingen med svenska som modersmål har granskat den här texten än. Den följer
> termerna i [glossary.md](glossary.md), samma termer som gränssnittet använder. Hör av er om ett
> ord låter fel.

Handboken innehåller inga siffror från beräkningarna. Den säger var en siffra finns, inte vad den
är: siffrorna står i vyerna och i filerna som kommandona skriver. Gränssnittet märker vilka data
som är verkliga och vilka som är syntetiska; förklaringen står i statusraden.

Kommandona skrivs här som `kapacitet …`. Samma kommando fungerar som `python -m kapacitet …`.
`kapacitet <kommando> --help` visar alla val.

## Innan du börjar: inloggning och roller

En installation för ett nätbolag kräver oftast inloggning. Du loggar in med organisationens konto
hos er identitetsleverantör. Kapacitet+ ser aldrig ditt lösenord. Det sparar ditt användar-id,
ditt namn och din roll med varje beslut och varje sigill du gör.

Rollen tilldelas hos identitetsleverantören, inte i Kapacitet+. Det finns fyra roller:

| Roll | Får |
|---|---|
| planerare | bedöma anslutningar, kontrollera ett näts filer, registrera ansökningar, kontrollera en elmätare |
| beslutsfattare | allt som en planerare får, och dessutom godkänna och avslå, tömma kön, ändra nätbolagets inställningar, signera regeln, utfärda scheman och registrera marknadsbaserade alternativ |
| administratör | allt som en planerare får, och dessutom ändra inställningarna, signera regeln, utfärda scheman, registrera marknadsbaserade alternativ, och importera och byta nät |
| granskare | läsa allt och ladda ner underlaget till Ei; ändrar ingenting |

Bara beslutsfattaren godkänner och avslår. Den som sköter installationen beslutar alltså inte om
ansökningar, om inte samma person har båda rollerna. En knapp som kräver en annan roll är grå och
säger vilken roll som behövs. Är du inloggad utan roll säger sidan det; be då administratören om
en roll.

Utan inloggning (en demo eller en lokal kopia) får alla göra allt. Hur inloggningen slås på står i
[sso.md](sso.md).

## Startfönstret och projekten

Starta servern med `kapacitet serve` och öppna adressen den skriver ut. Först visas
**startfönstret**: projekten på servern i en tabell, det valda projektets uppgifter och knappen
*Öppna projekt*. Ett projekt är ett lågspänningsnät med sina studier. Det projekt som servern
redan står på öppnas direkt. Ett annat projekt kräver att servern startar om i projektets mapp:
fönstret frågar först och går in i projektet när servern är tillbaka. Bedömningar, kö och
ansökningar hör till varje projekt och stannar där.

*Nytt projekt…* skapar ett projekt i tre steg (källa, kontroll, bygge) från en nätexport med fyra
CSV-tabeller, från ett exempelnät eller från en projektfil; det kräver rollen administratör. Ett
exempelnät är syntetiskt och märks så i projektlistan, i statusraden och på utskrifterna.
*Exportera (.zip)* skriver ett projekt som en projektfil: dess tabeller och vad det är, utan
resultat, som beräknas på nytt där filen öppnas.

Inne i ett projekt ramas vyn in av fyra delar:

- **Menyraden** överst: Arkiv, Visa, Studie, Resultat, Verktyg och Hjälp. Varje vy finns i en meny
  med sin siffertangent. *Studie › Beräkningsstatus…* visar vad som är beräknat för projektet,
  när, och med vilket kommando varje del beräknas på nytt. De tunga studierna körs från
  kommandoraden, inte från webbläsaren.
- **Projektträdet** till vänster: nätet (kartan, transformatorn, de utgående ledningarna och husen
  på var och en), studierna, resultaten och referensen. En studie öppnar sin vy, och ett hus väljs
  i alla vyer. T fäller ihop trädet till en rad med ikoner.
- **Utdatafönstret** nederst: vad programmet har gjort för dig, med klockslag: en bedömning och
  dess steg, ett projekt som öppnas eller byggs, en fil som förseglas. Det är stängt tills du
  öppnar det med O eller med sista raden i statusraden.
- **Statusraden**: om servern svarar, projektet och referensåret, vilka data som är verkliga och
  vilka som är syntetiska, språk och tangenter.

## Vyerna

Tangenten 0 går till startfönstret och siffertangenterna 1–8 väljer en vy. Ctrl + K söker vyer,
adresser och det menyerna gör, och ? visar alla tangenter. Adressfältet
beskriver det du tittar på, så en länk visar samma sak för den som får den.

| Vy | Till för |
|---|---|
| 1 **Sverige** | Bakgrunden: produktion och förbrukning per elområde, och varför nätkapaciteten är flaskhalsen. |
| 2 **Området** | Nätstationen och husen timme för timme. Årsremsan väljer timme; scenariot väljer statisk gräns, utan styrning eller med dynamiska gränser. Tabellen över husen går att söka och sortera. |
| 3 **Statisk mot dynamisk** | Acceptansgränsen med den statiska studien och med dynamiska exportgränser, nätet på kartan i 3D, verifieringen timme för timme, när gränserna begränsar, fördelningsreglerna, värdet och jämförelsen med förstärkning. |
| 4 **Anslutningsansökan** | Bedöm en anslutning för ett hus: solceller, elbilsladdare, värmepump, batteri. Här registrerar ni en ansökan som kommit via er egen kanal, och här beslutar ni om den. |
| 5 **Drift** | Inkorgen med ansökningar, ett hus schema för en dag, marknaden först, sommaren i skuggdrift och mätarkontrollen. |
| 6 **Årsrapport** | Det som nätbolaget lämnar in till Ei: nedregleringen som sista utväg, avtalen, underlaget till Ei, icke-marknadsbaserad omdirigering och uppgifterna om ett hus. |
| 7 **Scenarier 2030** | Hur långt området kan elektrifieras med solceller, elbilar och värmepumpar innan någon metod kräver förstärkning. |
| 8 **Metod & data** | Datakällorna och deras licenser, modellens indata med källa, valideringen, prognosen, skydden, kontrollen av ert eget nät (Ditt nät) och begränsningarna. |

**Sökandeportalen** (`#/apply`) är en egen sida för hushållen, inte för nätbolaget. Hushållet väljer
sin adress, anger vad det vill ansluta, läser svaret och ansöker. Portalen frågar inte efter namn,
e-post eller personnummer. Hushållet får en referenskod, och det är bara med den som ansökan går
att följa. Portalen visar ert namn, er logotyp och er kontakt (se Inställningarna nedan). Länken
*Öppna sökandeportalen* finns i Drift, under Ansökningar.

## Dagens ansökningar

### 1. Ansökan kommer in

En ansökan kommer på ett av två sätt:

- **Via portalen.** Hushållet bedömer och ansöker själv. Ansökan hamnar i inkorgen.
- **Via er egen kanal** (telefon, e-post, ett ärendesystem). Öppna **Anslutningsansökan**, välj huset
  på kartan eller sök adressen, ange vad det gäller och tryck *Bedöm anslutningen*. Under svaret
  trycker du *Ansök* i blocket *Ansök om den här anslutningen*. Ansökan får en referenskod och
  hamnar i samma inkorg.

Referenskoden visas en gång, för den som ansöker. Inkorgen visar bara dess början. Skriv aldrig
personuppgifter i en motivering eller anteckning: den förseglas och sparas som bevis.

### 2. Bedömningen

Inkorgen finns i **Drift**, under **Ansökningar**. En ansökan bygger på en sparad bedömning. Har kön
ändrats sedan dess bedöms ansökan på nytt mot nätet som det ser ut nu. Därefter ger nätbolagets
regel sitt förslag:

- **fast anslutning** när den statiska studien godtar allt och husets huvudsäkring och faser klarar det;
- **med dynamisk exportgräns** när exportgränsen håller nedregleringen och den oförsörjda laddningen
  inom nätbolagets trösklar, och ingen gräns i nätet överskrids;
- **till en handläggare** i alla andra fall, med skälen.

Regeln avslår aldrig. Ett avslag är alltid en persons beslut.

### 3. Beslutet: rådgivande läge och den signerade regeln

Från början står regeln i **rådgivande läge**. Den föreslår bara, och en handläggare avgör varje
ansökan.

Nätbolaget kan slå på **Automatiska beslut**. Regeln beslutar då själv, men bara det som en person
har signerat. Tryck *Signera regeln…* i blocket *Nätbolagets regel* och fyll i ditt namn, din roll,
vilka slags ansökningar den får avgöra, den största solcellsanläggningen och sista giltighetsdag.
Signaturen förseglas. En ansökan utanför signaturen, under andra trösklar eller efter
giltighetsdagen går till en handläggare. Ett beslut av regeln är märkt *beslutad av regeln* och
sparar signaturens kontrollsumma. För en studie signeras regeln med
`kapacitet study sign-rule <studie> --by "namn, roll" --kinds pv,ev --max-pv-kwp <kWp> --valid-until <datum>`.

En ansökan som väntar på en handläggare avgörs med *Besluta* i inkorgen. Valen är *Anslut fast*,
*Anslut med den här exportgränsen* och *Avslå*, med en motivering. *Utvärdera igen* skickar
tillbaka ansökan för en ny bedömning. Adressen i inkorgen öppnar bedömningen i
**Anslutningsansökan**, där samma beslut finns i blocket *Beslut*.

Överst i inkorgen står nyckeltalen: mottagna ansökningar, andelen som regeln beslutade, mediantiden
till beslut och den anslutna solelen. Siffrorna räknas från inkorgens egna händelser.

### 4. Kön, och varför ett godkännande kontrollerar varje väderår

Ett godkänt hus går in i **kön**. Nästa ansökan bedöms mot nätet med det huset inräknat: först till
kvarn, och ett godkänt hus behåller sin exportgräns.

Svaret som du ser när du bedömer en ansökan räknas för referensåret. När ansökan godkänns
kontrolleras den på nytt för **varje väderår** med fullständig lastflödesberäkning, och nätets
tillstånd räknas om för varje år med huset inräknat. Ett år med annat väder kan ge andra timmar vid
gränsen. Kontrollen ser till att kön aldrig tar nätet över dess gränser, oavsett väderår. Därför tar
ett godkännande längre tid än en bedömning; vyn visar hur långt den har kommit.

En bedömning som gjordes mot en äldre version av kön kan inte godkännas. Vyn säger då *Kön har
ändrats sedan det här svaret*; bedöm ansökan på nytt. Godkänner regeln en ansökan går den samma
väg som ett beslut av en person.

*Töm kön* tar bort alla beslut ur kön, och nästa ansökan bedöms mot det publicerade nätet igen.
Besluten finns kvar i beviskedjan, och *Beslutslogg (CSV)* listar alla beslut som någonsin
förseglats.

## Publiceringen per kvartal eller månad

En studie täcker många nätstationer och ligger i en egen mapp utanför Kapacitet+:s egna filer.
En körning är ett kvartal (skrivet som `ÅÅÅÅQn`) eller en månad (`ÅÅÅÅ-MM`). Med
`cadence = "monthly"` under `[publication]` i `study.toml` blir månaden standard när `--run`
utelämnas. Studiens indata beskrivs i [study-inputs.md](study-inputs.md).

Kommandona i ordning:

```bash
kapacitet study fetch <studie>        # väder och spotpriser
kapacitet study import <studie>       # en mapp per nätstation från nätexporten
kapacitet study check <studie>        # datakvaliteten för varje nätstation
kapacitet study build <studie>        # bygger de nätstationer vars indata ändrats
kapacitet study status <studie>       # vad varje nätstation är och hur bygget gick
kapacitet study collect <studie> --run <körning>   # alla nätstationers kapacitet
kapacitet study validate <studie>     # modellen mot mätningarna
kapacitet study review <studie> --run <körning>    # granskningslistan
kapacitet study sign <studie> --run <körning> --by "namn, roll"
kapacitet study publish <studie> --run <körning>
kapacitet study diff <studie> --to <körning>       # vad som ändrats sedan förra körningen, och varför
```

**Granskningen.** `study review` skriver `runs/<körning>/review.csv`. Nätstationen med minst
marginal står först, med det som bör kontrolleras. Vill du rätta en siffra skriver du det i
`runs/<körning>/corrections.csv`, med kolumnerna `station_id`, `field`, `value`, `reason` och `by`.
En rättelse kan bara göra publiceringen försiktigare: sänka `generation_kw` eller `consumption_kw`,
dölja en nätstation (`hidden`) eller lägga till en anmärkning (`note`). Den skriver aldrig över det
beräknade värdet; publiceringen visar båda. En rättelse som inte kan gälla (utan skäl, med ett högre
värde, en okänd nätstation) listas som ett fynd och tillämpas inte. Kör `study review` igen efter en
ändring.

**Signaturen.** `study sign` förseglar varje utdatafil i studiens egen beviskedja. Ändras något
efter signaturen blir publiceringen ett utkast igen tills någon signerar på nytt.

**Publiceringen.** `study publish` skriver `runs/<körning>/publication/`: kapaciteten som CSV och
GeoJSON, beräkningsgrunderna, valideringsrapporten, manifestet och kartan för inbäddning (`embed/`).
Är körningen inte signerad, eller har den ändrats efter signaturen, står det UTKAST på varje fil.
`disclosure_report.csv`, som säger vilken nätstation som ligger i vilken cell, stannar bredvid
mappen och publiceras aldrig.

**Små celler.** I en cell med färre än 20 kunder avrundas varje värde nedåt till närmaste 10 kW
(`[disclosure] round_kw` och `round_below_customers` i `study.toml`). Det gör skillnaden mellan två
publiceringar grövre, men tar inte bort den: sjunker värdet i en liten cell har något anslutits där.
Värdena 10 kW och 20 kunder är programmets förval, inte en sekretessbedömning av ert nät.

**Delad kapacitet längre upp.** Sätts ett värde av en nivå längre upp i nätet (`upstream.csv`)
anger kolumnerna `generation_shared_by_stations` och `consumption_shared_by_stations` hur många
nätstationer som delar på den kapaciteten. Värdena för de stationerna är samma kapacitet och får
inte summeras.

**Validering mot mätningar.** `study validate` jämför modellen med era mätningar men avgör
ingenting. Vad som är godkänt bestämmer ni själva i `[validation]` i `study.toml`: största
avvikelse för kundspänningen och för transformatorns belastning (95:e percentilen) och minsta andel
kunder med mätvärden, med källa. Utan sådana kriterier redovisas ingen nätstation som validerad.
Med dem publiceras en nätstation som ligger utanför inte alls: cellen saknar värde och fyndet
anger varför.

**Kartan på er webbplats.** Kopiera hela mappen `publication/` till webbplatsen och bädda in
`embed/index.html` i en iframe. Kartan behöver inget bibliotek och hämtar inget utanför sin mapp,
om inte studien anger en bakgrundskarta.

**Publiceringens API.** Servern kan också lämna ut de signerade publiceringarna själv. Det är
avstängt från början; administratören slår på det med `KAPACITET_PUBLICATIONS=1` och
`KAPACITET_STUDY_DIR` och startar om servern. Bara en signerad publicering lämnas ut; ett utkast
lämnas aldrig ut. Detaljerna, och hur kartan bäddas in, står i [publication.md](publication.md).

**Ärenden i grupp.** `kapacitet study cases <studie> <fil.csv>` kontrollerar en grupp ansökningar
nätstation för nätstation och skriver förslagen och de svenska bilagorna.
`kapacitet study sync <studie> --approved <fil.csv>` bygger om varje nätstations kö från era
godkända anslutningar.

## Drift

**Drift** visar det som nätbolaget utfärdar för husen med villkorat avtal. Allt här är
utfärdat dagen före; perfekt framförhållning är en planeringssiffra och utfärdas aldrig.

**En dag.** Välj ett hus och ett datum. Vyn visar husets schema: en exportgräns per intervall
(en kvart som standard), bredvid den export huset skulle ha utan gränserna. *Utfärda filen (XML)*
laddar ner filen för växelriktaren, och utfärdandet förseglas. *Gräns utan schema (XML)* är gränsen
som gäller när inget schema når fram. Har huset en elbilsladdare med importgräns finns också
*Laddprofil (OCPP 1.6J)*. Filerna är modellerade efter standarderna men inte certifierade.

Under laddprofilen visar blocket **Importgränser för elbilsladdare** fönstret som importgränserna
utfärdades med, bredvid fönstret i inställningarna, och hur ofta husen gick över sitt planeringstak.
Skiljer sig fönstren åt nekas laddprofilen tills importgränserna utfärdas igen; blocket visar
kommandot, `kapacitet compute --stage extra --only operation`. Fönstret ändras i samma block.

**Marknaden först.** En dag utfärdas först efter resultatet från dagen före-marknaden, vid den tid
som blocket anger. Måste en dag utfärdas tidigare skriver du skälet; skälet förseglas med
utfärdandet. Innan ett avtal tecknas, och innan en period med gränser, registrerar nätbolaget vilka
marknadsbaserade alternativ det har undersökt: *Registrera en undersökning*, fyll i alternativen och
resultatet, och *Registrera och försegla*. En dag som ingen sådan post täcker får en varning i sin
post när den utfärdas.

**Sommaren i skuggdrift.** Gränserna utfärdas varje dag under säsongen som om de gällde, med årets
verkliga väder, och bedöms med de två prov som ett pilotprojekt måste klara innan det tas i drift.
Vyn säger om proven är klarade. Välj en dag i tabellen för att öppna dess schema.

**En elmätare.** Ladda upp ett hus mätvärden som CSV, eller prova den syntetiska dagen. Vyn jämför
varje mätvärde med gränsen för dess intervall och listar överträdelserna. Filformatet står under
knappen. För alla avtal i en studie samtidigt: `kapacitet meter-compliance --study <studie>`.

## Årsrapporten och underlaget till Ei

**Årsrapport** visar det som nätbolaget lämnar in till Ei för sina villkorade avtal. Överst väljer
du underlag: *Utfärdad dagen före* (det som faktiskt kan utfärdas) eller *Perfekt framförhållning*.
*Fördelningsregel* visar rapporten med en annan regel; *Gör den till nätbolagets regel* byter regel.

- **Sista utväg**: varje nedreglerad timme, och vilken del av nätet som annars hade överskridit sitt
  märkvärde.
- **Avtal**: varje villkorat avtal med årets nedreglering.
- **Till Ei**: varje villkor i Ei2025:01 bredvid det som modellen visar om det. Under *Det som bara
  nätbolaget kan intyga* skriver nätbolaget sina egna intyg; appen intygar dem aldrig åt er.
  *Ladda ner underlaget (förseglat)* laddar ner paketet och förseglar vad som laddades ner.
- **Omdirigering**: den årliga rapporten om icke-marknadsbaserad omdirigering (EU 2019/943,
  artikel 13.4), alltid på underlaget utfärdat dagen före.
- **Ett hus**: det som en kund kan läsa om sin egen anslutning, med varje nedreglerad timme.

Allt detta är **underlag, aldrig en juridisk bedömning**. Om ett villkor är uppfyllt avgör Ei.

Samma underlag finns som filer:

- `kapacitet compliance`: granskningsspåret och årsrapporten;
- `kapacitet redispatch-report`: rapporten om omdirigering;
- `kapacitet method-dossier`: metodbeskrivningen till Ei, på svenska, med varje värde ur data
  (`--study <studie>` för en studie).

## Nätbolagets inställningar

Inställningarna är nätbolagets. Varje ändring förseglas. De sparas i `data/requests/settings.json`,
eller i PostgreSQL när installationen använder en databas. Administratören och beslutsfattaren får
ändra dem.

Alla inställningar finns samlade under Drift › Inställningar, ordnade efter vad de styr: ansökningarna
och regeln, exportgränserna och avtalen, marknaden och omdirigeringen, portalen och intygen till Ei.
Där står varje inställnings värde, vad den betyder och standardvärdet när det skiljer sig, och när
inställningarna senast ändrades och med vilken roll. De som har en egen kontroll någon annanstans
visas där med en länk till den:

| Inställning | Var den ändras |
|---|---|
| Automatiska beslut, på eller av | Drift › Ansökningar, *Automatiska beslut* |
| Regelns signatur | Drift › Ansökningar, *Signera regeln…*; för en studie `kapacitet study sign-rule` |
| Portalens namn, kontakt, accentfärg och logotyp | Drift › Ansökningar, blocket *Portal* bredvid länken till portalen |
| Importgränsernas fönster | Drift › En dag, blocket *Importgränser för elbilsladdare* |
| Fördelningsregeln | Årsrapport, *Fördelningsregel* och *Gör den till nätbolagets regel* |
| Nätbolagets intyg till Ei | Årsrapport › Till Ei, *Det som bara nätbolaget kan intyga* |
| Regelns trösklar, nätet som ansökningar bedöms mot, den avtalade nivån och antalet år som förstärkningen skjuts upp | Drift › Inställningar, grupperna *Ansökningar och regeln* och *Exportgränser och avtal* |
| Tiden för resultatet från dagen före-marknaden och den utbetalda ersättningen för omdirigering | Drift › Inställningar, gruppen *Marknaden och omdirigering* |

Varje grupp sparas för sig, och bara det som ändrats skickas. En tröskel som lämnas tom följer den
avtalade nivån. Gränserna för varje fält kommer från servern. Den som inte har rätt roll ser värdena men
kan inte ändra dem.

Accentfärgen och logotypen har krav som blocket *Portal* anger. Logotypen rensas från text och
metadata innan den sparas.

**Nät.** Under Metod & data › Ditt nät kontrollerar en planerare ett näts export, och i
startfönstret gör en administratör den till ett projekt med *Nytt projekt…*. Servern byter projekt
bara när någon öppnar ett annat projekt och bekräftar, aldrig av sig själv; bedömningar, kö och
ansökningar hör till varje projekt och stannar där. Samma kontroll från kommandoraden:
`kapacitet check <mapp>`.

## Beviskedjan

Varje bedömning, beslut, tömning av kön, ansökan, ändring av inställningarna, utfärdande,
registrering av marknadsbaserade alternativ och nedladdning av underlaget **förseglas**. Posten får
en kontrollsumma (SHA-256), läggs i en kedja där varje rad bygger på den förra, och signeras med
installationens nyckel. Ändras en sparad post, eller tas en rad bort, syns det.

- *Kontrollera* under ett sparat svar kontrollerar dess sigill direkt i vyn.
- `kapacitet seals verify` kontrollerar hela kedjan: kontrollsummor, signaturer och tidsstämplar.
  En studie har en egen kedja i `<studie>/evidence`.
- **Tidsstämplar.** Med `KAPACITET_TSA_URL` satt skickas varje rads kontrollsumma (bara den) till en
  tidsstämpeltjänst. `kapacitet seals timestamp` hämtar de tidsstämplar som fortfarande saknas.
- `kapacitet seals rotate-key` byter signeringsnyckel. Bytet signeras av både den gamla och den
  nya nyckeln.

Den publika nyckeln följer med i underlaget till Ei och i studiernas manifest, så den som får
underlaget kan kontrollera det själv. En signatur är inte en betrodd tid; det är tidsstämpeln.

## Säkerhetskopiering

```bash
kapacitet instance check                        # kontrollerar installationen; slutar med 1 vid fel
kapacitet instance backup <tom mapp>            # säkerhetskopian, med signeringsnyckeln för sig
kapacitet instance backup <tom mapp> --key-out <annan mapp>   # nyckeln i en egen, skyddad mapp
kapacitet instance restore <mapp>               # återställer till en tom installation
```

Säkerhetskopian tar med kön, ansökningarna, inställningarna, beviskedjan och de sparade
bedömningarna. Nätet och dess resultat räknas om och ingår inte. Utan databas tas kopian av
`data/requests/`: stoppa servern först, kommandot vägrar köra bredvid den. Signeringsnyckeln är hemlig: förvara
den åtskild från kopian. Återställningen kontrollerar varje fils kontrollsumma först och skriver
ingenting om något är skadat. Prova en återställning till en tom testinstallation då och då; en
säkerhetskopia bevisar sig först när den har återställts.

**Gallring.** `kapacitet purge --older-than <dagar> --dry-run` visar vilka sparade bedömningar som
är äldre än gränsen; utan `--dry-run` tas de bort. Deras sigill raderas, men kedjan behåller
kontrollsumman och förblir giltig. En bedömning som ett beslut i kön eller en väntande ansökan
bygger på behålls. Hur länge bedömningar sparas bestämmer nätbolaget.

Mer för den som driver installationen: [deployment.md](deployment.md) och
[postgres.md](postgres.md).
