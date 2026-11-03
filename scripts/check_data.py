"""Controle na elke ophaalronde. Faalt deze, dan wordt er niets opgeslagen."""
import hashlib
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "docs" / "data"
fouten = []


def lees(pad):
    return json.loads(pad.read_text(encoding="utf8"))


def eerder(pad):
    """Versie uit de laatste commit, of None als die er niet is."""
    uit = subprocess.run(["git", "show", f"HEAD:{pad}"], cwd=ROOT, capture_output=True, text=True)
    return json.loads(uit.stdout) if uit.returncode == 0 and uit.stdout else None


def vorm(v):
    vol = (v + [None] * 6)[:6]
    return vol[:3] + [vol[3] or None] + vol[4:]


def main():
    tekst = (ROOT / "data" / "excel-basis.json").read_bytes()
    if hashlib.sha256(tekst).hexdigest() != (ROOT / "data" / "excel-basis.sha256").read_text().strip():
        fouten.append("De Excel-basis is gewijzigd.")
    basis = json.loads(tekst)["spelers"]
    hist, latest = lees(DATA / "history.json"), lees(DATA / "latest.json")
    maanden, spelers = hist["maanden"], hist["spelers"]

    if maanden != sorted(set(maanden)):
        fouten.append("De maandlijst is niet geordend.")
    if latest["maanden"] != maanden:
        fouten.append("latest.json en history.json hebben andere maanden.")
    for tag, reeks in spelers.items():
        for m, v in reeks.items():
            if m not in maanden or len(v) != 6 or not all(x is None or isinstance(x, int) for x in v):
                fouten.append(f"Ongeldige maandstand {tag} {m}: {v}")
            elif v[3] is not None and not 1 <= v[3] <= 36:
                fouten.append(f"Ongeldige league {tag} {m}: {v[3]}")

    for tag, reeks in basis.items():
        pad = DATA / "historie" / f"{tag[1:]}.json"
        detail = lees(pad) if pad.exists() else {}
        for m, r in reeks.items():
            stand = [r.get("helden"), r.get("dieren"), r.get("equip"), r.get("lgn")]
            if any(x is not None for x in stand) and spelers.get(tag, {}).get(m, [])[:4] != stand:
                fouten.append(f"Maandstand wijkt af van de basis: {tag} {m}")
            if detail.get(m) != r:
                fouten.append(f"Maanddetail wijkt af van de basis: {tag} {m}")

    # Een eenmaal vastgelegde maand mag niet verdwijnen of veranderen.
    oud = eerder("docs/data/history.json")
    for tag, reeks in (oud or {}).get("spelers", {}).items():
        for m, v in reeks.items():
            if m in basis.get(tag, {}):
                continue
            if m not in spelers.get(tag, {}):
                fouten.append(f"Maand verdwenen: {tag} {m}")
            elif any(o is not None and o != n for o, n in zip(vorm(v), spelers[tag][m])):
                fouten.append(f"Vaste maand gewijzigd: {tag} {m}")

    roof, roof_oud = lees(DATA / "roof.json"), eerder("docs/data/roof.json")
    if set((roof_oud or {}).get("weekenden", {})) - set(roof["weekenden"]):
        fouten.append("Er zijn roofweekenden verdwenen.")

    for p in latest["players"]:
        if len(p["s"]) != len(maanden):
            fouten.append(f"Reeks heeft de verkeerde lengte: {p['tag']}")
    if len(latest["players"]) < len(basis) * 0.5:
        fouten.append(f"Te weinig spelers in latest.json: {len(latest['players'])}")

    if fouten:
        print("\n".join(fouten[:40]))
        sys.exit(f"{len(fouten)} fouten in de data. Niets opgeslagen.")
    print(f"Data in orde: {len(spelers)} spelers, {len(maanden)} maanden, {len(basis)} spelers in de basis.")


main()
