# Eye on Eye

Haalt elk uur de clandata op uit de Clash of Clans API en toont die op een
openbare website via GitHub Pages.

## Installatie

1. Zet in GitHub onder Settings > Pages de bron op `Deploy from a branch`,
   branch `main`, map `/docs`.
2. Start onder Actions de workflow `Clandata bijwerken` met `Run workflow`.
3. Controleer `docs/data/api_check.json`. Daarin staan de gevonden clans.

Elke speler die ooit in een van de clans is gezien, blijft bewaard, ook na een
overstap of vertrek.

De sleutel staat als GitHub-secret `COC_API_KEY` en hoort op IP `45.79.218.79`
van de RoyaleAPI-proxy te staan.

## Live-koppeling

De map `worker/` is een Cloudflare Worker die de site elke 30 seconden actuele
oorlogsdata geeft. De API-sleutel blijft daarbij afgeschermd. Installeren gaat met
de workflow `Live-koppeling installeren`, die de secrets `CLOUDFLARE_API_TOKEN`,
`CLOUDFLARE_ACCOUNT_ID` en `COC_API_KEY` nodig heeft.

## Geschiedenis

Oude oorlogen en in- en uitstappen komen een keer per dag van ClashKing
(api.clashk.ing), omdat de officiele API die niet bewaart. Een storing daar stopt
de gewone ophaalronde niet.

## Instellingen

In `config.json`:

- `clans`: de vier clans in vaste volgorde, met hun rol op de site.
- `venster_dagen`: periode voor aanvallen en gemiddelde.
- `bij_ons_sinds`: vaste startdatums per speler-tag, bijvoorbeeld `{"#QUYLU8C0": "2014"}`.
- `grens_aanvallen`: onder dit aantal wordt een speler rood.

## Bestanden

- `scripts/fetch.py`: het ophaalscript.
- `docs/index.html`: de website.
- `docs/data/history.json`: maandstanden per speler, voor de groei per maand.
- `docs/data/leiding.json`: opstelling en leiding-keuzes, bewerkbaar op de site.
- `docs/data/wars/`: elke opgeslagen oorlog.
- `docs/data/archief.json`: korte regel per afgelopen oorlog voor de tab Oorlogen.
- `docs/data/latest.json`: de data voor de website, gemaakt door het script.

Alle spelerdata komt uit de API. Alleen de opstelling en leiding-keuzes worden
met de hand bepaald.

## Opstelling bewerken

Maak op GitHub een fine-grained token met alleen toegang tot deze repository en
`Contents: Read and write`. Vul die op de site in bij Opstelling > Bewerken. De
token blijft in je eigen browser en gaat alleen naar GitHub.
