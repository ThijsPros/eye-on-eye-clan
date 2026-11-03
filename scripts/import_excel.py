"""Eenmalige import van de oude maandlijsten (Lijst.xlsx en Lijst2.xlsx).

Gebruik: python scripts/import_excel.py Lijst.xlsx Lijst2.xlsx
Schrijft docs/data/history.json bij en maakt docs/data/historie/TAG.json.
Donaties, capital en de Excel-bestanden zelf blijven buiten de repository.
"""
import json
import re
import sys
from pathlib import Path

import openpyxl

DATA = Path(__file__).resolve().parent.parent / "docs" / "data"
MAANDEN = {
    "Oktober": "2025-10", "November": "2025-11", "December": "2025-12", "Januari": "2026-01",
    "Februari": "2026-02", "Maart": "2026-03", "April": "2026-04", "Mei": "2026-05", "Juni1": "2026-06",
    "Juli": "2026-07", "Augustus": "2026-08", "September2026": "2026-09", "Oktober2026": "2026-10",
    "November2026": "2026-11",
}
HELDEN = ["Barbarian King", "Archer Queen", "Minion Prince", "Grand Warden", "Royal Champion", "Dragon Duke"]
BUILDER = {"Battle Machine", "Battle Copter"}
GEEN_LEVEL = {"Tag", "Name", "TH", "BH", "Heldenlevels", "Dierenlevels", "Helden", "Dieren", "Equipment", "Totaal", None}
HUIDIG = "2026-11"


def blad_soort(naam):
    for prefix, soort in (("War", "war"), ("PYQ2RLCQ-war", "war"), ("Equipment", "equip"), ("PYQ2RLCQ-hero-e", "equip"),
                          ("Heroes", "heroes"), ("PYQ2RLCQ-heroes", "heroes"), ("Leden", "leden"), ("PYQ2RLCQ-members", "leden")):
        if naam.startswith(prefix):
            return soort


def rijen(blad):
    """Geeft per regel met een geldige tag een dict op kolomnaam; eerste kolom met die naam wint."""
    alles = list(blad.iter_rows(values_only=True))
    kop = alles[0]
    sleutel = "ID" if "ID" in kop else "Tag"
    plek = kop.index(sleutel)
    uit = []
    for rij in alles[1:]:
        tag = rij[plek]
        if isinstance(tag, str) and tag.startswith("#"):
            d = {}
            for k, v in zip(kop, rij):
                if k is not None and k not in d:
                    d[k] = v
            uit.append(d)
    return uit


def getal(v):
    if isinstance(v, bool) or v is None:
        return None
    if isinstance(v, (int, float)):
        return v
    try:
        return float(str(v).replace(",", "."))
    except ValueError:
        return None


def heel(v):
    n = getal(v)
    return int(n) if n is not None else None


def league_nummer(naam):
    naam = str(naam or "").replace(" League", "").strip()
    if naam in ("Legend", "Legend III"):
        return 34
    if naam == "Legend II":
        return 35
    if naam == "Legend I":
        return 36
    m = re.search(r"\s(\d+)$", naam)
    return int(m.group(1)) if m else None


def niveaus(rij, overslaan=()):
    return {k: heel(v) for k, v in rij.items() if k not in GEEN_LEVEL and k not in overslaan and not str(k).startswith("Som") and heel(v)}


def lees(pad, clan, uit):
    wb = openpyxl.load_workbook(pad, data_only=True, read_only=True)
    maand = None
    for blad in wb.worksheets:
        if blad.title in MAANDEN:
            maand = MAANDEN[blad.title]
            soort = "maand"
        elif blad.title == "September":
            maand = None
            continue
        else:
            soort = blad_soort(blad.title)
        if not maand or not soort:
            continue
        for r in rijen(blad):
            tag = r.get("ID") or r["Tag"]
            s = uit.setdefault(tag, {}).setdefault(maand, {}).setdefault(clan, {})
            if soort == "maand":
                s.update(naam=r.get("Speler"), th=heel(r.get("TH") or r.get("TH level")),
                         helden=heel(r.get("Helden") or r.get("Heldenlevels")), dieren=heel(r.get("Dieren") or r.get("Dierenlevels")),
                         equip=heel(r.get("Equipment")), plek=r.get("Plek"), keuze=r.get("Keuze leiding"),
                         aanmelding=r.get("Aanmelding in app"))
                if maand != "2025-10":  # Toen gold nog het oude trofeeënsysteem.
                    s["league"] = r.get("League")
            elif soort == "leden":
                s.setdefault("naam", r.get("Name"))
                s["xp"] = heel(r.get("Level"))
                s["th"] = s.get("th") or heel(r.get("TH"))
                if maand != "2025-10":
                    s["league"] = s.get("league") or r.get("League")
            elif soort == "heroes":
                alle = niveaus(r, BUILDER)
                s["heldenLvl"] = {k: v for k, v in alle.items() if k in HELDEN}
                s["dierenLvl"] = {k: v for k, v in alle.items() if k not in HELDEN}
            elif soort == "equip":
                s["equipLvl"] = niveaus(r)
            elif soort == "war":
                s["war"] = {
                    "aanv": heel(r.get("Attack: count")), "gem": getal(r.get("Attack: Average Stars")),
                    "drie": getal(r.get("Attack: % of 3 Stars")), "gemist": heel(r.get("Attack: Miss attacks")),
                    "verd": heel(r.get("Defense: count")), "verdGem": getal(r.get("Defense: Average Stars")),
                    "verdDrie": getal(r.get("Defense: % of 3 Stars")),
                }


def tekst(v):
    if v is None or (isinstance(v, str) and not v.strip()):
        return None
    if isinstance(v, str):
        v = v.strip()
        return v[0].upper() + v[1:]
    return int(v) if float(v).is_integer() else v


def samenvoegen(per_clan):
    """Staat iemand in één maand in beide lijsten, dan telt de lijst met de meeste aanvallen."""
    volgorde = sorted(per_clan, key=lambda c: ((per_clan[c].get("war") or {}).get("aanv") or 0, c == "I"), reverse=True)
    r = dict(per_clan[volgorde[0]])
    for c in volgorde[1:]:
        for k, v in per_clan[c].items():
            if r.get(k) in (None, {}):
                r[k] = v
    r["lijst"] = volgorde[0]
    return r


def som_of(r, kort, lang):
    return r.get(kort) or (sum(r[lang].values()) if r.get(lang) else None)


def main():
    uit = {}
    lees(sys.argv[1], "I", uit)
    lees(sys.argv[2], "II", uit)
    hist = json.loads((DATA / "history.json").read_text())
    map_ = DATA / "historie"
    map_.mkdir(exist_ok=True)
    gewijzigd = nieuw = 0
    for tag, maanden in uit.items():
        reeks = hist["spelers"].setdefault(tag, {})
        if not reeks:
            nieuw += 1
        detail = {}
        for m in sorted(maanden):
            r = samenvoegen(maanden[m])
            stand = [som_of(r, "helden", "heldenLvl"), som_of(r, "dieren", "dierenLvl"), som_of(r, "equip", "equipLvl"), league_nummer(r.get("league"))]
            oud = reeks.get(m)
            if m == HUIDIG and oud:
                stand = oud
            elif oud:
                stand[3] = stand[3] or (oud[3] if len(oud) > 3 else None)
                gewijzigd += stand[:3] != oud[:3]
            if stand[0] is not None or stand[3] is not None:
                reeks[m] = stand
            w = r.get("war") or {}
            detail[m] = {k: v for k, v in {
                "naam": r.get("naam"), "lijst": r["lijst"], "th": r.get("th"), "xp": r.get("xp"), "league": r.get("league"),
                "plek": tekst(r.get("plek")), "keuze": tekst(r.get("keuze")), "aanmelding": tekst(r.get("aanmelding")),
                **w, "heldenLvl": r.get("heldenLvl"), "dierenLvl": r.get("dierenLvl"), "equipLvl": r.get("equipLvl"),
            }.items() if v not in (None, {})}
        (map_ / f"{tag[1:]}.json").write_text(json.dumps(detail, ensure_ascii=False, separators=(",", ":")))
        eerste = min(detail) + "-01"
        eerst = hist.setdefault("eerst", {})
        if not hist.get("eerstExact", {}).get(tag) and eerste < eerst.get(tag, "9999"):
            eerst[tag] = eerste
    hist["maanden"] = sorted({*hist["maanden"], *(m for s in uit.values() for m in s)})
    (DATA / "history.json").write_text(json.dumps(hist, ensure_ascii=False, indent=1))
    print(f"{len(uit)} spelers, {nieuw} nieuw, {gewijzigd} maandstanden gewijzigd, maanden {hist['maanden'][0]} t/m {hist['maanden'][-1]}")


main()
