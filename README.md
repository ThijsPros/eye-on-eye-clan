# Eye on Eye

Haalt elke 4 uur de clandata op uit de Clash of Clans API en toont die op een
openbare website via GitHub Pages.

## Installatie

1. Zet in GitHub onder Settings > Pages de bron op `Deploy from a branch`,
   branch `main`, map `/docs`.
2. Start onder Actions de workflow `Clandata bijwerken` met `Run workflow`.
3. Controleer `docs/data/api_check.json`. Daarin staan de gevonden clans en de
   vergelijking met de ClashSpot-export.

De sleutel staat als GitHub-secret `COC_API_KEY` en hoort op IP `45.79.218.79`
van de RoyaleAPI-proxy te staan.

## Instellingen

In `config.json`:

- `clan_tags`: vaste clans. Vul hier alle vier de tags in.
- `clan_naam_begint_met`: alleen clans met deze naam tellen mee.
- `alleen_vaste_clans`: `true` slaat het zoeken via spelers over.
- `venster_dagen`: periode voor aanvallen en gemiddelde.
- `grens_aanvallen`: onder dit aantal wordt een speler rood.

## Bestanden

- `scripts/fetch.py`: het ophaalscript.
- `docs/index.html`: de website.
- `docs/data/baseline.json`: ClashSpot-export van november 2026.
- `docs/data/history.json`: maandstanden per speler.
- `docs/data/wars/`: elke opgeslagen oorlog.
- `docs/data/latest.json`: de data voor de website, gemaakt door het script.

Leiding-keuzes staan hier bewust niet in, omdat repository en website openbaar zijn.
