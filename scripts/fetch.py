#!/usr/bin/env python3
"""Haalt de clandata op uit de Clash of Clans API en schrijft docs/data/*.

Vereist de omgevingsvariabele COC_API_KEY. De sleutel wordt nooit gelogd of
weggeschreven. Geen externe pakketten nodig.
"""
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import zlib
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "docs" / "data"
WARS = DATA / "wars"
HISTORIE = DATA / "historie"
BASIS = ROOT / "data" / "excel-basis.json"
CWL = DATA / "cwl"
CONFIG = json.loads((ROOT / "config.json").read_text(encoding="utf8"))
BASE = os.environ.get("COC_API_BASE", "https://cocproxy.royaleapi.dev/v1").rstrip("/")
NOW = datetime.now(timezone.utc)
CLASHKING = "https://api.clashk.ing"
ROMEINS = {1: "I", 2: "II", 3: "III", 4: "IV"}
RANKED_START, RANKED_WEEK = 1793595600, 604800  # Maandag 05:00 UTC, zoals currentLeagueSeasonId.
SUPER = {"Sneaky Goblin", "Inferno Dragon", "Ice Hound", "Rocket Balloon"}

PETS = {
    "L.A.S.S.I", "Electro Owl", "Mighty Yak", "Unicorn", "Frosty", "Diggy",
    "Poison Lizard", "Phoenix", "Spirit Fox", "Angry Jelly", "Sneezy",
    "Greedy Raven",
}
ROLES = {"leader": "Leader", "coLeader": "Co-leader", "admin": "Elder", "member": "Member"}


class ApiError(Exception):
    def __init__(self, status, reason):
        super().__init__(f"HTTP {status}: {reason}")
        self.status, self.reason = status, reason


class Private(Exception):
    """De clan deelt deze gegevens niet (oorlogslog staat op privé)."""


def get(path):
    """GET op de API. Geeft None bij 404."""
    request = urllib.request.Request(
        BASE + path,
        headers={"Authorization": "Bearer " + os.environ["COC_API_KEY"], "Accept": "application/json"},
    )
    for attempt in range(4):
        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                return json.load(response)
        except urllib.error.HTTPError as error:
            try:
                reason = json.load(error).get("reason", "")
            except Exception:
                reason = ""
            if error.code == 404:
                return None
            if error.code == 403 and "invalidIp" in reason:
                raise ApiError(403, "IP niet toegestaan: stel de sleutel in op het IP van de proxy") from None
            if error.code == 403 and "invalid" in reason.lower():
                raise ApiError(403, "sleutel ongeldig") from None
            if error.code == 403:
                raise Private() from None
            if not (error.code == 429 or error.code >= 500) or attempt == 3:
                raise ApiError(error.code, reason) from None
        except (urllib.error.URLError, TimeoutError) as error:
            if attempt == 3:
                raise ApiError(0, type(error).__name__) from None
        time.sleep(2 ** attempt)


def tag_url(tag):
    return urllib.parse.quote(tag, safe="")


def read_json(path, default=None):
    """Een ontbrekend bestand geeft de standaard. Een kapot bestand stopt de ronde, zodat het nooit leeg wordt overschreven."""
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text(encoding="utf8"))
    except ValueError:
        sys.exit(f"Kapot databestand: {path}. Ronde gestopt, niets opgeslagen.")


def write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, separators=(",", ":")), encoding="utf8")
    temporary.replace(path)


def parse_time(value):
    return datetime.strptime(value, "%Y%m%dT%H%M%S.%fZ").replace(tzinfo=timezone.utc) if value else None


def league_of(player):
    """Naam en volgnummer van de league. Volgnummer dient alleen om te sorteren."""
    tier = player.get("leagueTier") or player.get("league") or {}
    name = (tier.get("name") or "Onbekend").replace(" League", "")
    match = re.search(r"(\d+)$", name)
    if match:
        return name, int(match.group(1))
    number = tier.get("id")
    return name, number % 1000 if isinstance(number, int) else 0


def clan_eisen(clan):
    tier = clan.get("requiredLeagueTier") or {}
    naam, nummer = league_of({"leagueTier": tier})
    return {"th": clan.get("requiredTownhallLevel") or 0, "trofeeen": clan.get("requiredTrophies") or 0,
            "lgn": nummer if tier and naam != "Unranked" else 0, "league": naam if tier and naam != "Unranked" else None}


def is_troep(x):
    """Gewone thuistroep: geen dier, geen tijdelijke supertroep."""
    return x["name"] not in PETS and x["name"] not in SUPER and not x["name"].startswith("Super ")


def summarize_player(raw):
    home = lambda items: [x for x in items if x.get("village", "home") == "home"]
    league, league_number = league_of(raw)
    clan = raw.get("clan") or {}
    return {
        "tag": raw["tag"],
        "naam": raw["name"],
        "th": raw.get("townHallLevel"),
        "league": league,
        "lgn": league_number,
        "helden": sum(x["level"] for x in home(raw.get("heroes", []))),
        "dieren": sum(x["level"] for x in home(raw.get("troops", [])) if x["name"] in PETS),
        "equip": sum(x["level"] for x in home(raw.get("heroEquipment", []))),
        "trofeeen": raw.get("trophies"),
        "gamesTotaal": next((a.get("value") for a in raw.get("achievements", []) if a.get("name") == "Games Champion"), None),
        "spreuken": sum(x["level"] for x in home(raw.get("spells", []))),
        "troepen": sum(x["level"] for x in home(raw.get("troops", [])) if is_troep(x)),
        "rol": ROLES.get(raw.get("role"), raw.get("role") or ""),
        "clan": clan.get("name"),
        "clanTag": clan.get("tag"),
        "don": raw.get("donations", 0),
        "donOntv": raw.get("donationsReceived", 0),
        "warSterren": raw.get("warStars", 0),
    }


def load_clans():
    """Haalt de vaste clans op, in de volgorde van config.json."""
    clans = []
    for number, entry in enumerate(CONFIG["clans"], 1):
        raw = get("/clans/" + tag_url(entry["tag"]))
        if raw:
            clans.append({**raw, "nr": number, "rol": entry["rol"]})
        else:
            print("Clan niet gevonden: " + entry["tag"])
    return clans


def side_info(side):
    info = {k: side.get(k) for k in ("tag", "name", "stars", "destructionPercentage")}
    info["aanvallen"] = side.get("attacks", sum(len(m.get("attacks", [])) for m in side.get("members", [])))
    info["logo"] = (side.get("badgeUrls") or {}).get("medium")
    return info


def max_stars(stars, attacks_left, attacks, size):
    """Hoogst haalbare sterren: elke resterende aanval pakt de grootste winst.

    Per basis telt alleen de beste aanval, dus een basis met 2 sterren levert
    nog maar 1 ster op. Dit is een bovengrens.
    """
    best = {}
    for attack in attacks:
        best[attack["doel"]] = max(best.get(attack["doel"], 0), attack["sterren"])
    open_stars = [3 - value for value in best.values()] + [3] * max(0, size - len(best))
    return stars + sum(sorted(open_stars, reverse=True)[:attacks_left])


def normalize_war(raw, war_id, kind, clan_tag, season=None):
    side, opponent = raw["clan"], raw["opponent"]
    if side.get("tag") != clan_tag:
        side, opponent = opponent, side
    members = [
        {
            "tag": m["tag"],
            "naam": m["name"],
            "th": m.get("townhallLevel"),
            "pos": m.get("mapPosition"),
            "aanvallen": [
                {"sterren": a["stars"], "procent": a["destructionPercentage"], "doel": a["defenderTag"], "volgorde": a.get("order"), "duur": a.get("duration")}
                for a in m.get("attacks", [])
            ],
        }
        for m in side.get("members", [])
    ]
    return {
        "id": war_id,
        "soort": kind,
        "seizoen": season,
        "status": raw["state"],
        "grootte": raw.get("teamSize"),
        "aanvallenPerLid": raw.get("attacksPerMember", 1),
        "start": raw.get("startTime"),
        "einde": raw.get("endTime"),
        "clan": side_info(side),
        "tegenstander": side_info(opponent),
        "tegenLeden": {
            m["tag"]: {"naam": m.get("name"), "th": m.get("townhallLevel")}
            for m in opponent.get("members", [])
        },
        "tegenAanvallen": [
            {"sterren": a["stars"], "doel": a["defenderTag"], "duur": a.get("duration")}
            for m in opponent.get("members", []) for a in m.get("attacks", [])
        ],
        "leden": members,
    }


def save_war(war):
    write_json(WARS / (war["id"] + ".json"), war)


def collect_wars(clan):
    """Bewaart de lopende oorlog en de CWL-oorlogen. Geeft een statusregel terug."""
    tag, name = clan["tag"], clan["name"]
    status = {"oorlogslog": "openbaar", "cwl": None}
    safe_tag = tag.strip("#")
    try:
        war = get(f"/clans/{tag_url(tag)}/currentwar")
        if war and war.get("state") in ("preparation", "inWar", "warEnded") and war.get("preparationStartTime"):
            save_war(normalize_war(war, f"{safe_tag}-{war['preparationStartTime'][:13]}", "oorlog", tag))
    except Private:
        status["oorlogslog"] = "privé"

    try:
        group = get(f"/clans/{tag_url(tag)}/currentwar/leaguegroup")
    except Private:
        group = None
    if not group or group.get("state") == "notInWar":
        return status
    status["cwl"] = {"seizoen": group.get("season"), "status": group.get("state")}
    results = {}
    status["cwl"]["clans"] = [
        {"tag": c["tag"], "naam": c["name"], "logo": (c.get("badgeUrls") or {}).get("medium")}
        for c in group.get("clans", [])
    ]
    status["cwl"]["rondes"] = {}
    for number, round_ in enumerate(group.get("rounds", []), 1):
        for war_tag in round_.get("warTags", []):
            if war_tag == "#0":
                continue
            file_id = war_tag.strip("#")
            cached = read_json(CWL / (file_id + ".json"), None)
            if not cached or cached["status"] != "warEnded":
                war = get("/clanwarleagues/wars/" + tag_url(war_tag))
                if not war:
                    continue
                cached = {
                    "status": war["state"],
                    "grootte": war.get("teamSize"),
                    "kanten": [
                        {
                            "tag": side.get("tag"), "sterren": side.get("stars") or 0, "procent": side.get("destructionPercentage") or 0,
                            "aanvallen": side.get("attacks", sum(len(m.get("attacks", [])) for m in side.get("members", []))),
                            "max": side_maximum(side, war.get("teamSize") or 0),
                            "maxPct": side_max_percent(side, war.get("teamSize") or 0),
                            "verdeling": [
                                sum(a["stars"] == stars for m in side.get("members", []) for a in m.get("attacks", []))
                                for stars in (3, 2, 1, 0)
                            ],
                        }
                        for side in (war["clan"], war["opponent"])
                    ],
                }
                write_json(CWL / (file_id + ".json"), cached)
                if tag in (war["clan"].get("tag"), war["opponent"].get("tag")):
                    normalized = normalize_war(war, f"cwl-{safe_tag}-{file_id}", "cwl", tag, group.get("season"))
                    normalized["ronde"] = number
                    save_war(normalized)
            status["cwl"]["rondes"].setdefault(str(number), {})[file_id] = cached
            if cached["status"] != "preparation":
                results.setdefault(number, []).append(cached)
    status["cwl"]["stand"] = cwl_table(group, results)
    return status


def max_percent(attacks, attacks_left, size):
    """Hoogst haalbare vernietiging: resterende aanvallen pakken de grootste winst per basis."""
    if not size:
        return None
    best = {}
    for attack in attacks:
        best[attack.get("doel")] = max(best.get(attack.get("doel"), 0), attack.get("procent") or 0)
    gaps = sorted([100 - v for v in best.values()] + [100] * max(0, size - len(best)), reverse=True)
    return round((sum(best.values()) + sum(gaps[:attacks_left])) / size, 2)


def side_max_percent(side, size):
    attacks = [{"doel": a["defenderTag"], "procent": a["destructionPercentage"]} for m in side.get("members", []) for a in m.get("attacks", [])]
    return max_percent(attacks, max(0, size - len(attacks)), size)


def side_maximum(side, size):
    """Maximaal haalbare sterren van deze kant in een CWL-oorlog (1 aanval per lid)."""
    attacks = [{"doel": a["defenderTag"], "sterren": a["stars"]} for m in side.get("members", []) for a in m.get("attacks", [])]
    return max_stars(side.get("stars") or 0, max(0, size - len(attacks)), attacks, size)


def cwl_outcome(war, own, other):
    """w, v, g of bezig. Een uitslag telt zodra die niet meer kan veranderen."""
    size = war.get("grootte") or 0
    finished = war["status"] == "warEnded" or (size and own.get("aanvallen", 0) >= size and other.get("aanvallen", 0) >= size)
    mine, theirs = (own["sterren"], own["procent"]), (other["sterren"], other["procent"])
    if finished:
        return "w" if mine > theirs else "v" if mine < theirs else "g"
    if "max" in other and (own["sterren"] > other["max"] or (own["sterren"] == other["max"] and other.get("maxPct") is not None and own["procent"] > other["maxPct"] + 0.02)):
        return "w"
    if "max" in own and (other["sterren"] > own["max"] or (other["sterren"] == own["max"] and own.get("maxPct") is not None and other["procent"] > own["maxPct"] + 0.02)):
        return "v"
    return "bezig"


def cwl_table(group, results):
    """Tussenstand: sterren plus 10 per gewonnen ronde, daarna percentage."""
    table = {
        c["tag"]: {"tag": c["tag"], "naam": c["name"], "logo": (c.get("badgeUrls") or {}).get("medium"),
                   "sterren": 0, "bonus": 0, "vernietigd": 0, "aanvallen": 0, "verdeling": [0, 0, 0, 0],
                   "winst": 0, "verlies": 0, "gelijk": 0, "rondes": {}}
        for c in group.get("clans", [])
    }
    for number, wars in results.items():
        for war in wars:
            a, b = war["kanten"]
            for own, other in ((a, b), (b, a)):
                row = table.get(own["tag"])
                if not row:
                    continue
                outcome = cwl_outcome(war, own, other)
                won, lost, tied = outcome == "w", outcome == "v", outcome == "g"
                row["sterren"] += own["sterren"]
                row["bonus"] += 10 if won else 0
                row["vernietigd"] = round(row["vernietigd"] + own["procent"] * (war.get("grootte") or 0), 1)
                row["aanvallen"] += own.get("aanvallen", 0)
                row["verdeling"] = [x + y for x, y in zip(row["verdeling"], own.get("verdeling", [0, 0, 0, 0]))]
                row["winst"] += won
                row["verlies"] += lost
                row["gelijk"] += tied
                row["rondes"][number] = {
                    "sterren": own["sterren"], "aanvallen": own.get("aanvallen", 0),
                    "uitslag": outcome,
                }
    return sorted(table.values(), key=lambda r: (-(r["sterren"] + r["bonus"]), -r["vernietigd"]))


def clashking(path):
    """Haalt geschiedenis op bij ClashKing. Een storing stopt de ophaalronde niet."""
    request = urllib.request.Request(CLASHKING + path, headers={"User-Agent": "eye-on-eye-clan"})
    try:
        with urllib.request.urlopen(request, timeout=90) as response:
            return json.load(response).get("items", [])
    except (urllib.error.URLError, TimeoutError, ValueError):
        return None


def clashking_los(path):
    """ClashKing-antwoord zonder lijst eromheen."""
    request = urllib.request.Request(CLASHKING + path, headers={"User-Agent": "eye-on-eye-clan"})
    try:
        with urllib.request.urlopen(request, timeout=45) as response:
            return json.load(response)
    except (urllib.error.URLError, TimeoutError, ValueError):
        return None


def to_date(value):
    return f"{value[:4]}-{value[4:6]}-{value[6:8]}"


def war_result(ours, theirs):
    """w, v of g. Sterren eerst, daarna percentage."""
    a = (ours.get("stars") or 0, ours.get("destructionPercentage") or 0)
    b = (theirs.get("stars") or 0, theirs.get("destructionPercentage") or 0)
    return "w" if a > b else "v" if a < b else "g"


def unbeaten_streak(results):
    """Aantal normale oorlogen op rij zonder verlies, nieuwste eerst."""
    count = 0
    for end in sorted(results, reverse=True):
        if results[end] == "v":
            break
        count += 1
    return count


ARCHIVE = {}


def archive_add(tag, wars):
    """Korte regel per afgelopen oorlog, voor de tab Oorlogen."""
    rows = {}
    wars = sorted((w for w in wars if w.get("state", "warEnded") == "warEnded" and w.get("endTime") and w.get("startTime")), key=lambda w: w["startTime"])
    months = {}
    for war in wars:
        ours, theirs = (war["clan"], war["opponent"]) if war["clan"].get("tag") == tag else (war["opponent"], war["clan"])
        league = bool(war.get("tag")) or not war.get("attacksPerMember")
        number = None
        if league:  # Rondes zijn opeenvolgende dagen; zo tellen dubbele of ontbrekende regels niet mee.
            start = datetime.strptime(war["startTime"][:15], "%Y%m%dT%H%M%S")
            gat = (start - months["einde"]).total_seconds() if months.get("einde") else None
            if gat is None or gat >= 7 * 86400:  # Nieuw seizoen; juni 2026 had er twee.
                number = 1
            else:  # Elke hele dag tussen twee rondes is een ontbrekende ronde.
                number = min(7, months["ronde"] + 1 + max(0, int(gat // 86400)))
            months["ronde"], months["einde"] = number, datetime.strptime(war["endTime"][:15], "%Y%m%dT%H%M%S")
        per_member = 1 if league else war.get("attacksPerMember") or 2
        rows[war["endTime"]] = {
            "soort": "cwl" if league else "oorlog", "ronde": number, "start": war["startTime"], "einde": war["endTime"],
            "grootte": war.get("teamSize"), "totaal": (war.get("teamSize") or 0) * per_member, "gebruikt": ours.get("attacks") or 0,
            "tegen": theirs.get("name"), "tegenLogo": (theirs.get("badgeUrls") or {}).get("small"),
            "s": [ours.get("stars") or 0, theirs.get("stars") or 0],
            "p": [ours.get("destructionPercentage") or 0, theirs.get("destructionPercentage") or 0],
        }
    ARCHIVE[tag] = rows


def leden_uit_historie(clan_tag, events, first, laatst):
    """Iedereen die minstens een dag in de clan zat, ook zonder ooit een oorlog te spelen."""
    per_speler = {}
    for event in sorted(events or [], key=lambda e: e.get("time") or ""):
        if event.get("tag") and event.get("time"):
            per_speler.setdefault(event["tag"], []).append(event)
    for tag, reeks in per_speler.items():
        totaal, sinds = 0, None
        for event in reeks:
            moment = datetime.fromisoformat(event["time"][:19])
            if event.get("type") == "join":
                sinds = moment
            elif sinds:
                totaal += (moment - sinds).total_seconds()
                sinds = None
        al_lid = reeks[0].get("type") != "join"
        if sinds or al_lid or totaal >= 86400:
            begin, einde = reeks[0]["time"][:10], reeks[-1]["time"][:10]
            first[tag] = min(first.get(tag, begin), begin)
            laatst[tag] = max(laatst.get(tag, einde), einde)


def backfill(clans, history):
    """Vult oorlogen en eerste datums aan. Draait een keer per dag."""
    today = NOW.strftime("%Y-%m-%d")
    if history.get("ckDatum") == today:
        return
    first = history.setdefault("eerst", {})
    laatst = history.setdefault("laatst", {})
    cutoff = NOW - timedelta(days=max(CONFIG["venster_dagen"] + 35, CONFIG["maand_venster_dagen"] + 10))
    complete = True
    for clan in clans:
        tag, safe_tag = clan["tag"], clan["tag"].strip("#")
        wars = clashking(f"/v2/clan/{tag_url(tag)}/wars?limit=2000")
        if wars is None:
            wars = clashking(f"/v2/clan/{tag_url(tag)}/wars?limit=500")
        if wars is None:
            complete = False
            continue
        # De oudere route gaat verder terug en vult eerste datums en het archief aan.
        known = {w.get("endTime") for w in wars}
        older = clashking(f"/war/{tag_url(tag)}/previous?limit=2000") or []
        for war in older:
            if war.get("startTime"):
                side = war["clan"] if war["clan"].get("tag") == tag else war["opponent"]
                date = to_date(war["startTime"])
                for member in side.get("members", []):
                    first[member["tag"]] = min(first.get(member["tag"], date), date)
                    laatst[member["tag"]] = max(laatst.get(member["tag"], date), date)
        archive_add(tag, list(wars) + [w for w in older if w.get("endTime") not in known])
        wars = [w for w in wars if w.get("state") == "warEnded" and w.get("startTime")]
        results = history.setdefault("uitslagen", {}).setdefault(tag, {})
        for war in wars:
            if not war.get("tag"):
                ours, theirs = (war["clan"], war["opponent"]) if war["clan"].get("tag") == tag else (war["opponent"], war["clan"])
                results.setdefault(war["endTime"], war_result(ours, theirs))
        rounds = {}
        for war in sorted((w for w in wars if w.get("tag")), key=lambda w: w["startTime"]):
            month = war["startTime"][:6]
            rounds[month] = rounds.get(month, 0) + 1
            war["_ronde"] = rounds[month]
        for war in wars:
            normalized_id = (
                f"cwl-{safe_tag}-{war['tag'].strip('#')}" if war.get("tag")
                else f"{safe_tag}-{war['preparationStartTime'][:13]}"
            )
            side = war["clan"] if war["clan"].get("tag") == tag else war["opponent"]
            date = to_date(war["startTime"])
            for member in side.get("members", []):
                first[member["tag"]] = min(first.get(member["tag"], date), date)
                laatst[member["tag"]] = max(laatst.get(member["tag"], date), date)
            if parse_time(war["startTime"]) < cutoff:
                continue
            stored = read_json(WARS / (normalized_id + ".json"), None)
            if stored and stored["status"] == "warEnded" and "tegenLeden" in stored:
                continue
            season = f"{war['startTime'][:4]}-{war['startTime'][4:6]}-01" if war.get("tag") else None
            normalized = normalize_war(war, normalized_id, "cwl" if war.get("tag") else "oorlog", tag, season)
            if war.get("tag"):
                normalized["ronde"] = war["_ronde"]
            save_war(normalized)

    for clan in clans:
        leden_uit_historie(clan["tag"], clashking(f"/clan/{tag_url(clan['tag'])}/join-leave?limit=50000"), first, laatst)

    leagues = history.setdefault("cwlLeague", {})
    for clan in clans:
        for member in clan.get("memberList", [])[:5]:  # De league per seizoen staat alleen bij spelers.
            for item in clashking(f"/v2/player/{tag_url(member['tag'])}/cwl/history") or []:
                eigen = item.get("clan") or {}
                naam = (eigen.get("warLeague") or {}).get("name")
                if naam and item.get("season") and eigen.get("tag") in {c["tag"] for c in clans}:
                    leagues.setdefault(eigen["tag"], {})[item["season"][:7]] = naam.replace(" League", "")

    ours = {clan["tag"] for clan in clans}
    done = history.setdefault("inUitGehad", [])
    exact = history.setdefault("eerstExact", {})
    begin = time.monotonic()
    for tag in history["spelers"]:
        if tag in done:
            continue
        if time.monotonic() - begin > 120:
            complete = False  # De rest volgt in de volgende ronde.
            break
        events = clashking(f"/player/{tag_url(tag)}/join-leave?limit=1000")
        if events is None:
            complete = False
            continue
        own_events = sorted((e for e in events if e.get("clan") in ours and e.get("time")), key=lambda e: e["time"])
        if own_events:
            laatst[tag] = max(laatst.get(tag, ""), own_events[-1]["time"][:10])
            date = own_events[0]["time"][:10]
            if date <= first.get(tag, date):
                first[tag] = date
                # Alleen een join is een echte datum; een eerste leave betekent dat hij er al zat.
                exact[tag] = own_events[0].get("type") == "join"
        done.append(tag)
        time.sleep(0.2)
    if complete:
        history["ckDatum"] = today


def load_wars():
    return sorted(
        (json.loads(p.read_text(encoding="utf8")) for p in WARS.glob("*.json")),
        key=lambda w: w.get("start") or "",
    )


def war_is_final(war):
    if war["status"] == "warEnded":
        return True
    end = parse_time(war.get("einde"))
    return False if not end else end < NOW - timedelta(hours=1)


def own_stats(wars, days=None):
    """Eigen telling per speler over het ingestelde venster."""
    cutoff = NOW - timedelta(days=days or CONFIG["venster_dagen"])
    stats = {}
    for war in wars:
        start = parse_time(war.get("start"))
        if war["status"] == "preparation" or not start or start < cutoff:
            continue
        for member in war["leden"]:
            s = stats.setdefault(member["tag"], {"aanv": 0, "moest": 0, "sterren": 0, "drie": 0, "gemis": 0, "oorlogen": 0, "cwlOorlogen": 0, "laatste": None})
            s["oorlogen"] += 1
            s["cwlOorlogen"] += war["soort"] == "cwl"
            kind = s.setdefault("soorten", {}).setdefault(war["soort"], {"aanv": 0, "moest": 0, "sterren": 0, "gemis": 0})
            kind["aanv"] += len(member["aanvallen"])
            kind["moest"] += war["aanvallenPerLid"] if war["status"] == "warEnded" else len(member["aanvallen"])
            kind["sterren"] += sum(a["sterren"] for a in member["aanvallen"])
            if war["status"] == "warEnded":
                kind["gemis"] += max(0, war["aanvallenPerLid"] - len(member["aanvallen"]))
            s["aanv"] += len(member["aanvallen"])
            s["moest"] += war["aanvallenPerLid"] if war["status"] == "warEnded" else len(member["aanvallen"])
            s["sterren"] += sum(a["sterren"] for a in member["aanvallen"])
            s["drie"] += sum(a["sterren"] == 3 for a in member["aanvallen"])
            s["laatste"] = max(s["laatste"] or "", war["start"][:8])
            # Gemist telt alleen als de oorlog zeker is afgelopen en volledig is gezien.
            if war["status"] == "warEnded":
                s["gemis"] += max(0, war["aanvallenPerLid"] - len(member["aanvallen"]))
    # CWL-seizoenen tellen helemaal mee, ook als een deel buiten het venster valt.
    for war in wars:
        if war["soort"] != "cwl" or not war.get("seizoen") or war["status"] == "preparation":
            continue
        for member in war["leden"]:
            s = stats.setdefault(member["tag"], {"aanv": 0, "moest": 0, "sterren": 0, "drie": 0, "gemis": 0, "oorlogen": 0, "cwlOorlogen": 0, "laatste": None})
            season = s.setdefault("cwlSeizoenen", {}).setdefault(war["seizoen"][:7], {"sterren": 0, "aanv": 0, "rondes": 0, "clan": war["clan"].get("tag")})
            season["sterren"] += sum(a["sterren"] for a in member["aanvallen"])
            season["aanv"] += len(member["aanvallen"])
            season["rondes"] += 1
    for s in stats.values():
        s["gem"] = round(s["sterren"] / s["aanv"], 2) if s["aanv"] else None
        s["drieProcent"] = round(100 * s["drie"] / s["aanv"], 1) if s["aanv"] else None
        s["laatste"] = f"{s['laatste'][:4]}-{s['laatste'][4:6]}-{s['laatste'][6:]}" if s["laatste"] else None
    return stats


def defence_stats(wars, days):
    """Verdediging per speler, voor de maandregels."""
    cutoff = NOW - timedelta(days=days)
    uit = {}
    for war in wars:
        start = parse_time(war.get("start"))
        if war["status"] == "preparation" or not start or start < cutoff:
            continue
        for attack in war.get("tegenAanvallen", []):
            s = uit.setdefault(attack.get("doel"), {"keer": 0, "sterren": 0, "drie": 0})
            s["keer"] += 1
            s["sterren"] += attack["sterren"]
            s["drie"] += attack["sterren"] == 3
    return uit


def basis_inpassen(history):
    """Zet de vaste Excel-basis terug en brengt elke maandstand in dezelfde vorm."""
    basis = read_json(BASIS, None)
    if not basis or not basis.get("spelers"):
        sys.exit("De Excel-basis ontbreekt. Data niet bijgewerkt.")
    eerst = history.setdefault("eerst", {})
    exact = history.setdefault("eerstExact", {})
    for tag, maanden in basis["spelers"].items():
        series = history["spelers"].setdefault(tag, {})
        for maand, r in maanden.items():
            stand = [r.get("helden"), r.get("dieren"), r.get("equip"), r.get("lgn")]
            if any(x is not None for x in stand):
                series[maand] = stand + (series.get(maand) or [None] * 6)[4:6]
        lid = [m for m, r in maanden.items() if r.get("lijst")]
        if lid and not exact.get(tag) and min(lid) + "-01" < eerst.get(tag, "9999"):
            eerst[tag] = min(lid) + "-01"
    for series in history["spelers"].values():
        for maand, stand in series.items():
            vol = (stand + [None] * 6)[:6]
            series[maand] = vol[:3] + [vol[3] or None] + vol[4:]
    history["maanden"] = sorted({*history["maanden"], *basis["maanden"]})
    return basis["spelers"]


def laatst_bijwerken(history, basis, wars, in_clan):
    """Laatste datum waarop iemand bij ons bekend was: Excel, oorlog of lidmaatschap."""
    laatst = history.setdefault("laatst", {})
    def zet(tag, datum):
        if datum and datum > laatst.get(tag, ""):
            laatst[tag] = datum
    for tag, maanden in basis.items():
        for maand, r in maanden.items():
            if r.get("lijst"):  # Alleen als iemand die maand echt op de ledenlijst stond.
                zet(tag, maand + "-01")
    for war in wars:
        if war["status"] != "preparation" and war.get("start"):
            for member in war["leden"]:
                zet(member["tag"], to_date(war["start"]))
    for tag in in_clan:
        zet(tag, NOW.strftime("%Y-%m-%d"))


def roof_bijwerken(clans):
    """Roofweekenden per clan. Oude weekenden blijven staan, ook als de API ze niet meer geeft."""
    pad = DATA / "roof.json"
    opslag = read_json(pad, {"weekenden": {}})
    weekenden = opslag["weekenden"]
    for clan in clans:
        tag = clan["tag"]
        bekend = any(k.startswith(tag + "|") for k in weekenden)
        try:
            data = get(f"/clans/{tag_url(tag)}/capitalraidseasons?limit={8 if bekend else 100}") or {}
        except ApiError:
            continue
        for w in data.get("items", []):
            sleutel = f"{tag}|{w['startTime']}"
            oud = weekenden.get(sleutel, {})
            rooster = set(oud.get("rooster") or [])
            if w.get("state") == "ongoing":
                rooster |= {m["tag"] for m in clan.get("memberList", [])}
            weekenden[sleutel] = {
                "clan": tag, "start": w["startTime"], "einde": w.get("endTime"), "status": w.get("state"),
                "buit": w.get("capitalTotalLoot"), "raids": w.get("raidsCompleted"), "aanvallen": w.get("totalAttacks"),
                "wijken": w.get("enemyDistrictsDestroyed"), "beloning": w.get("offensiveReward"), "verdediging": w.get("defensiveReward"),
                # De API geeft deelnemers alleen bij het laatste weekend, dus eerder bewaarde blijven staan.
                "leden": [{"tag": m["tag"], "naam": m["name"], "aanv": m.get("attacks", 0), "limiet": m.get("attackLimit", 0),
                           "bonus": m.get("bonusAttackLimit", 0), "buit": m.get("capitalResourcesLooted", 0)}
                          for m in w.get("members", [])] or oud.get("leden", []),
                "rooster": sorted(rooster) or None,
            }
    write_json(pad, opslag)


def games_bijwerken(players, clan_tags):
    """Clan Games: de API kent alleen het levenslange totaal, dus de punten zijn stand min beginstand."""
    pad = DATA / "clangames.json"
    opslag = read_json(pad, {"edities": {}})
    start = NOW.replace(day=22, hour=7, minute=0, second=0, microsecond=0)
    einde = NOW.replace(day=28, hour=9, minute=0, second=0, microsecond=0)
    bevriezen = einde + timedelta(hours=2)
    editie = opslag["edities"].setdefault(NOW.strftime("%Y-%m"), {
        "start": start.strftime("%Y%m%dT%H%M%S.000Z"), "einde": einde.strftime("%Y%m%dT%H%M%S.000Z"),
        "status": "komt", "basis": {}, "stand": {}, "clan": {},
    })
    if NOW > bevriezen:
        editie["status"] = "klaar"
    for tag, player in players.items():
        waarde = player.get("gamesTotaal")
        if waarde is None or player.get("clanTag") not in clan_tags or player.get("verouderd"):
            continue
        if NOW < start:
            editie["basis"][tag] = waarde
        elif NOW <= bevriezen:
            editie["status"] = "actief"
            editie["basis"].setdefault(tag, waarde)
            editie["stand"][tag] = waarde
            editie["clan"][tag] = player["clanTag"]
    write_json(pad, opslag)


def catalogus_bijwerken(raws):
    """Alles wat in het spel bestaat met het hoogste level, zodat ontbrekende items als 0 tellen."""
    pad = DATA / "catalogus.json"
    cat = read_json(pad, {"helden": {}, "dieren": {}, "equipment": {}, "spreuken": {}, "troepen": {}})
    def zet(soort, items):
        for x in items:
            if x.get("village", "home") == "home":
                cat[soort][x["name"]] = max(cat[soort].get(x["name"], 0), x.get("maxLevel") or 0)
    for raw in raws.values():
        zet("helden", raw.get("heroes", []))
        zet("equipment", raw.get("heroEquipment", []))
        zet("spreuken", raw.get("spells", []))
        zet("dieren", [x for x in raw.get("troops", []) if x["name"] in PETS])
        zet("troepen", [x for x in raw.get("troops", []) if is_troep(x)])
    write_json(pad, cat)


def ranked_bijwerken(players, raws, leden, clan_tags):
    """Ranked per week per lid. Battlelog geeft de laatste 50 gevechten, dus elke ronde aanvullen."""
    pad = DATA / "ranked.json"
    opslag = read_json(pad, {"weken": {}})
    weken = opslag["weken"]
    nu = int(NOW.timestamp())
    week_nu = str(RANKED_START + (nu - RANKED_START) // RANKED_WEEK * RANKED_WEEK)
    for tag in leden:
        if tag not in raws or players[tag].get("clanTag") not in clan_tags:
            continue
        player, raw = players[tag], raws[tag]
        try:
            log = (get(f"/players/{tag_url(tag)}/battlelog") or {}).get("items", [])
        except ApiError:
            continue
        momenten = [int(parse_time(b["battleTimestamp"]).timestamp()) for b in log if b.get("battleTimestamp")]
        oudste = min(momenten) if momenten else nu
        laatst = opslag.setdefault("laatst", {})
        for b, moment in zip((b for b in log if b.get("battleTimestamp")), momenten):
            if b.get("battleType") in ("ranked", "legend"):
                laatst[tag] = max(laatst.get(tag, 0), moment)
        for b, moment in zip((b for b in log if b.get("battleTimestamp")), momenten):
            if b.get("battleType") not in ("ranked", "legend"):
                continue
            week = str(RANKED_START + (moment - RANKED_START) // RANKED_WEEK * RANKED_WEEK)
            regel = weken.setdefault(week, {"spelers": {}})["spelers"].setdefault(tag, {"aanv": {}})
            soort = "aanv" if b.get("attack") else "verd"
            regel.setdefault(soort, {})[str(moment)] = [b.get("stars", 0), b.get("destructionPercentage", 0), b.get("opponentTownHallLevel")]
        regel = weken.setdefault(week_nu, {"spelers": {}})["spelers"].setdefault(tag, {"aanv": {}})
        groep = raw.get("currentLeagueGroupTag")
        regel.update(naam=player["naam"], clan=player.get("clanTag"), lgn=player["lgn"] or None, league=player["league"],
                     groep=bool(groep and groep != "#0"), trofeeen=player.get("trofeeen"))
        regel.setdefault("lgnStart", player["lgn"] or None)
        if player["league"] == "Legend I":  # Legenddag loopt van 05:00 tot 05:00 UTC.
            regel.setdefault("dagTrofee", {}).setdefault(str((nu - 18000) // 86400), player.get("trofeeen"))
        for week, inhoud in weken.items():
            if tag in inhoud["spelers"] and oudste <= int(week):
                inhoud["spelers"][tag]["volledig"] = True
        time.sleep(0.05)
    write_json(pad, opslag)


def ranked_zone(lgn):
    """Aantal promotie- en degradatieplekken in een groep van 100. Legend III en II vast, de rest geschat."""
    if lgn == 34:
        return 5, 15
    if lgn == 35:
        return 3, 15
    for van, tot, op, neer in CONFIG["ranked_zones"]:
        if van <= (lgn or 0) <= tot:
            return op, neer
    return None, None


def ranked_tier(item, tiers):
    """League uit leaguehistory in dezelfde vorm als bij de spelers."""
    naam = tiers.get(item.get("leagueTierId"), "")
    nummer = naam.rsplit(" ", 1)[-1]
    if nummer.isdigit():
        return naam.replace(" League", ""), int(nummer)
    return naam, {"Legend III": 34, "Legend II": 35, "Legend I": 36}.get(naam, 0)


def ranked_logregel(rijen):
    uit = {}
    for r in rijen or []:
        moment = int(datetime.strptime(r["creationTime"], "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc).timestamp())
        uit[str(moment)] = [r.get("stars", 0), r.get("destructionPercentage", 0), r.get("trophies", 0)]
    return uit


def ranked_groep(regel, data, tag, lgn):
    """Plek, tellingen en logs uit de officiële groep. Logs worden aangevuld, nooit vervangen."""
    leden = data.get("members") or []
    plek = next((i for i, m in enumerate(leden) if m.get("playerTag") == tag), None)
    if plek is None:
        return False
    rij = leden[plek]
    op, neer = ranked_zone(lgn)
    regel.update(plek=plek + 1, grootte=len(leden), trofeeen=rij.get("leagueTrophies"),
                 ao=rij.get("attackWinCount", 0) + rij.get("attackLoseCount", 0),
                 vo=rij.get("defenseWinCount", 0) + rij.get("defenseLoseCount", 0))
    if op and op <= len(leden):
        regel["grensOp"] = leden[op - 1].get("leagueTrophies")
        regel["zone"] = "op" if plek < op else ""
    if neer and len(leden) > neer:
        regel["grensNeer"] = leden[len(leden) - neer - 1].get("leagueTrophies")
        if plek >= len(leden) - neer:
            regel["zone"] = "neer"
    regel.setdefault("logA", {}).update(ranked_logregel(data.get("attackLogs")))
    regel.setdefault("logV", {}).update(ranked_logregel(data.get("defenseLogs")))
    return True


def ranked_officieel(players, raws, leden, clan_tags):
    """Groepsplek, eindplaats en volledige logs uit de officiële API."""
    pad = DATA / "ranked.json"
    opslag = read_json(pad, {"weken": {}})
    weken = opslag["weken"]
    nu = int(NOW.timestamp())
    week_nu = RANKED_START + (nu - RANKED_START) // RANKED_WEEK * RANKED_WEEK
    try:
        tiers = {t["id"]: t["name"] for t in (get("/leaguetiers") or {}).get("items", [])}
    except ApiError:
        tiers = {}
    gehaald = opslag.setdefault("histWeek", {})
    for tag in leden:
        if tag not in raws or players[tag].get("clanTag") not in clan_tags:
            continue
        raw, player = raws[tag], players[tag]
        try:
            for groep, seizoen, klaar in ((raw.get("currentLeagueGroupTag"), raw.get("currentLeagueSeasonId"), False),
                                          (raw.get("previousLeagueGroupTag"), raw.get("previousLeagueSeasonId"), True)):
                if not groep or groep == "#0" or not seizoen or int(seizoen) < week_nu - RANKED_WEEK:
                    continue
                regel = weken.setdefault(str(seizoen), {"spelers": {}})["spelers"].setdefault(tag, {"aanv": {}})
                if klaar and regel.get("eindGroep"):
                    continue
                data = get(f"/leaguegroup/{tag_url(groep)}/{seizoen}?playerTag={tag_url(tag)}")
                if data and ranked_groep(regel, data, tag, regel.get("lgn") or player["lgn"]):
                    regel.setdefault("naam", player["naam"])
                    regel.setdefault("clan", player.get("clanTag"))
                    if klaar:
                        regel["eindGroep"] = True
            if tiers and gehaald.get(tag) != week_nu:
                for item in (get(f"/players/{tag_url(tag)}/leaguehistory") or {}).get("items", []):
                    league, lgn = ranked_tier(item, tiers)
                    if lgn == 36 or (item.get("maxBattles") or 0) > 100:
                        continue  # Legend I is een toernooi van vier weken; zie ranked_legend.
                    regel = weken.setdefault(str(item["leagueSeasonId"]), {"spelers": {}})["spelers"].setdefault(tag, {"aanv": {}})
                    regel.update(plek=item.get("placement"), trofeeen=item.get("leagueTrophies"), maxB=item.get("maxBattles"),
                                 ao=item.get("attackWins", 0) + item.get("attackLosses", 0),
                                 vo=item.get("defenseWins", 0) + item.get("defenseLosses", 0), verdSter=item.get("defenseStars", 0),
                                 league=league, lgn=lgn or None, grootte=regel.get("grootte", 100))
                    regel.setdefault("naam", player["naam"])
                    regel.setdefault("clan", player.get("clanTag"))
                    regel.setdefault("lgnStart", lgn or None)
                    op, neer = ranked_zone(lgn)
                    plek = item.get("placement") or 0
                    regel["zone"] = "op" if op and plek and plek <= op else "neer" if neer and plek > 100 - neer else ""
                gehaald[tag] = week_nu
        except ApiError:
            continue
        time.sleep(0.05)
    ranked_legend(opslag, leden)
    write_json(pad, opslag)


def ranked_legend(opslag, leden):
    """Eindrang per Legend I-toernooi en de trofeeën op rang 10.000. Eén keer per toernooi."""
    lijst = opslag.setdefault("legend", {})
    try:
        seizoenen = [s["id"] for s in (get("/leagues/29000022/seasons") or {}).get("items", []) if str(s.get("id", "")).startswith("v2-")][-2:]
        for seizoen in seizoenen:
            if seizoen in lijst:
                continue
            rijen = (get(f"/leagues/29000022/seasons/{urllib.parse.quote(seizoen, safe='')}?limit=10000") or {}).get("items", [])
            if rijen:
                eigen = {r["tag"]: [r.get("rank"), r.get("trophies")] for r in rijen if r.get("tag") in leden}
                lijst[seizoen] = {"grens": rijen[-1].get("trophies"), "laatsteRang": rijen[-1].get("rank"), "spelers": eigen}
    except ApiError:
        pass


def activiteit(players, clan_tags):
    """Vijf signalen per lid. None betekent dat het signaal niet te meten is."""
    weken = read_json(DATA / "ranked.json", {"weken": {}})["weken"]
    nu = int(NOW.timestamp())
    week = RANKED_START + (nu - RANKED_START) // RANKED_WEEK * RANKED_WEEK
    roof = read_json(DATA / "roof.json", {"weekenden": {}})["weekenden"]
    laatste = {}
    for w in roof.values():
        if w.get("status") == "ended" and w.get("leden") and w["start"] > laatste.get(w["clan"], {}).get("start", ""):
            laatste[w["clan"]] = w
    edities = read_json(DATA / "clangames.json", {"edities": {}})["edities"]
    editie = next((edities[m] for m in sorted(edities, reverse=True) if edities[m]["status"] in ("klaar", "actief")), None)
    def tel(tag, w):
        r = weken.get(str(w), {}).get("spelers", {}).get(tag) or {}
        return r["ao"] if r.get("ao") is not None else len(r.get("aanv") or {})
    for tag, p in players.items():
        if p.get("clanTag") not in clan_tags or not p.get("inClan", True):
            continue
        cw = p.get("cw") or {}
        ranked = [tel(tag, week), tel(tag, week - RANKED_WEEK)]
        w = laatste.get(p["clanTag"])
        roofa = None if not w else next((m.get("aanv") or 0 for m in w["leden"] if m["tag"] == tag), 0)
        games = None
        if editie and tag in editie.get("stand", {}):
            games = max(0, editie["stand"][tag] - editie["basis"].get(tag, editie["stand"][tag]))
        don = p.get("don") or 0
        tekens = [(cw.get("aanv") or 0) > 0, sum(ranked) > 0, None if roofa is None else roofa > 0,
                  None if games is None else games > 0, True if don else (None if NOW.day < 7 else False)]
        p["act"] = {"o": cw.get("aanv") or 0, "r": ranked, "w": roofa, "g": games, "d": don, "t": tekens,
                    "s": sum(1 for x in tekens if x), "n": sum(1 for x in tekens if x is not None)}


def dekking_bepalen(archive, wars):
    """Markeert oorlogen zonder aanvalsdetail en telt ze per clan over het venster."""
    if not wars:
        return {}
    eerste = wars[0]["start"][:8]
    grens = (NOW - timedelta(days=CONFIG["venster_dagen"])).strftime("%Y%m%d")
    dekking = {}
    for tag, rows in archive.items():
        eigen = [(w["soort"], parse_time(w["einde"])) for w in wars if w["clan"].get("tag") == tag and w.get("einde") and w.get("leden")]
        for row in rows:
            if row["einde"][:8] < eerste:
                continue
            einde = parse_time(row["einde"])
            row["detail"] = any(soort == row["soort"] and abs((moment - einde).total_seconds()) < 6 * 3600 for soort, moment in eigen)
        recent = [r for r in rows if r["start"][:8] >= grens and "detail" in r]
        dekking[tag] = {"oorlogen": len(recent), "metDetail": sum(1 for r in recent if r["detail"])}
    return dekking


def records_bijwerken(clans):
    """Ranglijsten voor Nederland en de wereld, plus de eigen records. Vier keer per dag."""
    pad = DATA / "records.json"
    oud = read_json(pad, {})
    if oud.get("ronde") and NOW.timestamp() - oud["ronde"] < 6 * 3600:
        return
    NL = 32000166
    rij = lambda c, waarde: [c.get("tag"), c.get("name"), waarde, c.get("clanLevel"), (c.get("location") or {}).get("countryCode"),
                             (c.get("badgeUrls") or {}).get("small")]
    lijsten = {"nl": {}, "wereld": {}}
    for scope, plek in (("nl", NL), ("wereld", "global")):
        try:
            punten = (get(f"/locations/{plek}/rankings/clans?limit=200") or {}).get("items", [])
            kapitaal = (get(f"/locations/{plek}/rankings/capitals?limit=200") or {}).get("items", [])
        except ApiError:
            return
        lijsten[scope]["punten"] = [rij(c, c.get("clanPoints")) for c in punten]
        lijsten[scope]["kapitaal"] = [rij(c, c.get("clanCapitalPoints")) for c in kapitaal]
    reeks = clashking("/v2/leaderboard/clan/win-streak?limit=1000") or []
    maak = lambda c, w: [c.get("tag"), c.get("name"), w, None, None, (c.get("badgeUrls") or {}).get("small")]
    lijsten["wereld"]["reeks"] = [maak(c, c.get("war_win_streak")) for c in reeks][:200]
    lijsten["nl"]["reeks"] = [maak(c, c.get("war_win_streak")) for c in reeks if c.get("location_id") == NL][:200]
    goud = clashking(f"/v2/leaderboard/{NL}/clan/capital-gold?limit=200") or []
    lijsten["nl"]["goud"] = [maak(c, c.get("capital_gold_total")) for c in goud]
    eigen = {}
    for clan in clans:
        record = clashking_los(f"/v2/clan/{tag_url(clan['tag'])}/records") or {}
        eigen[clan["tag"]] = {
            "punten": clan.get("clanPoints"), "kapitaal": clan.get("clanCapitalPoints"), "reeks": clan.get("warWinStreak"),
            "winst": clan.get("warWins"), "gelijk": clan.get("warTies"), "verlies": clan.get("warLosses"), "niveau": clan.get("clanLevel"),
            "recordPunten": record.get("clanPoints"), "recordReeks": record.get("warWinStreak"),
        }
    write_json(pad, {"ronde": int(NOW.timestamp()), "lijsten": lijsten, "eigen": eigen})


def stand_bijwerken(series, month, earlier, current, stale=False):
    """De eerste meting van een maand staat vast. Geeft de groei sinds die stand en de leaguewissel."""
    if not stale and month not in series:
        series[month] = current
    base, before = series.get(month), series.get(earlier)
    if not base:
        return None, None
    if not stale:  # Spreuken en troepen kwamen later; de eerste meting vult ze aan.
        for i in (4, 5):
            if base[i] is None:
                base[i] = current[i]
    verschil = lambda i: current[i] - base[i] if current[i] is not None and base[i] is not None else None
    growth = {"h": verschil(0), "p": verschil(1), "e": verschil(2), "s": verschil(4), "t": verschil(5),
              "l": verschil(3) if current[3] and base[3] else None}
    return growth, base[3] - before[3] if before and base[3] and before[3] else None


def maand_record(player, raw, nummer, oorlog, verdediging):
    """Maandregel uit de live data, met dezelfde velden als de Excel-basis."""
    home = lambda items: {x["name"]: x["level"] for x in items if x.get("village", "home") == "home"}
    troepen = home(raw.get("troops", []))
    oorlog, verdediging = oorlog or {}, verdediging or {}
    aantal = verdediging.get("keer")
    record = {
        "bron": "live", "naam": player["naam"], "lijst": ROMEINS.get(nummer), "th": player["th"], "xp": raw.get("expLevel"),
        "league": player["league"], "lgn": player["lgn"] or None,
        "helden": player["helden"], "dieren": player["dieren"], "equip": player["equip"],
        "spreuken": player.get("spreuken"), "troepen": player.get("troepen"),
        "aanv": oorlog.get("aanv"), "gem": oorlog.get("gem"), "drie": oorlog.get("drieProcent"), "gemist": oorlog.get("gemis"),
        "verd": aantal, "verdGem": round(verdediging["sterren"] / aantal, 2) if aantal else None,
        "verdDrie": round(100 * verdediging["drie"] / aantal, 2) if aantal else None,
        "heldenLvl": {k: v for k, v in home(raw.get("heroes", [])).items() if k not in ("Battle Machine", "Battle Copter")},
        "dierenLvl": {k: v for k, v in troepen.items() if k in PETS},
        "equipLvl": home(raw.get("heroEquipment", [])),
        "spreukLvl": home(raw.get("spells", [])),
        "troepLvl": {k: v for k, v in troepen.items() if is_troep({"name": k})},
    }
    return {k: v for k, v in record.items() if v not in (None, {})}


def historie_bijwerken(basis, month, records):
    """Maanddetail per speler: de basis gaat altijd voor, een live maand blijft staan."""
    for tag in {*basis, *records}:
        path = HISTORIE / (tag.strip("#") + ".json")
        detail = read_json(path, {})
        oud = json.dumps(detail, sort_keys=True)
        detail.update(basis.get(tag, {}))
        if tag in records and month not in detail:
            detail[month] = records[tag]
        if json.dumps(detail, sort_keys=True) != oud or not path.exists():
            write_json(path, dict(sorted(detail.items())))


def average_duration(attacks):
    durations = [a["duur"] for a in attacks if a.get("duur") is not None]
    return round(sum(durations) / len(durations)) if durations else None


def war_summary(war):
    members = war["leden"]
    per_member = war["aanvallenPerLid"]
    size = war.get("grootte") or len(members)
    our_attacks = [a for m in members for a in m["aanvallen"]]
    ours, theirs = war["clan"], war["tegenstander"]
    limits = {}
    if "tegenAanvallen" in war:
        total = size * per_member
        limits = {
            "maxOns": max_stars(ours["stars"] or 0, max(0, total - len(our_attacks)), our_attacks, size),
            "maxZij": max_stars(theirs["stars"] or 0, max(0, total - len(war["tegenAanvallen"])), war["tegenAanvallen"], size),
            "maxPctOns": max_percent(our_attacks, max(0, total - len(our_attacks)), size),
            "maxPctZij": max_percent(war["tegenAanvallen"], max(0, total - len(war["tegenAanvallen"])), size),
        }
    return {
        **limits,
        "id": war["id"],
        "soort": war["soort"],
        "ronde": war.get("ronde"),
        "seizoen": war.get("seizoen"),
        "perLid": per_member,
        "gemTijdOns": average_duration(our_attacks),
        "gemTijdZij": average_duration(war.get("tegenAanvallen", [])),
        "leden": [
            {"tag": m["tag"], "naam": m["naam"], "pos": m.get("pos"), "aanvallen": [{"sterren": a["sterren"], "procent": a["procent"]} for a in m["aanvallen"]]}
            for m in sorted(members, key=lambda m: m.get("pos") or 99)
        ],
        "status": "onvolledig" if war["status"] != "warEnded" and war_is_final(war) else war["status"],
        "start": war.get("start"),
        "einde": war.get("einde"),
        "grootte": war.get("grootte"),
        "clan": war["clan"],
        "tegenstander": war["tegenstander"],
        "gebruikt": sum(len(m["aanvallen"]) for m in members),
        "totaal": len(members) * per_member,
        "open": [
            {"naam": m["naam"], "over": per_member - len(m["aanvallen"])}
            for m in members if len(m["aanvallen"]) < per_member
        ],
    }


def player_profile(tag, raw, wars, stats):
    """Alles voor de profielpagina van een speler."""
    home = lambda items: [x for x in items if x.get("village", "home") == "home"]
    pack = lambda items: [{"naam": x["name"], "lvl": x["level"], "max": x.get("maxLevel")} for x in items]
    cutoff = NOW - timedelta(days=CONFIG["venster_dagen"])
    attacks, missed = [], []
    kinds = {"oorlog": {"oorlogen": 0, "moest": 0, "aanv": 0, "sterren": 0, "drie": 0}, "cwl": {"oorlogen": 0, "moest": 0, "aanv": 0, "sterren": 0, "drie": 0}}
    defence = {"keer": 0, "sterren": 0, "drie": 0}
    for war in reversed(wars):
        start = parse_time(war.get("start"))
        if war["status"] == "preparation" or not start or start < cutoff:
            continue
        member = next((m for m in war["leden"] if m["tag"] == tag), None)
        if not member:
            continue
        opponents = war.get("tegenLeden", {})
        kind = kinds[war["soort"]]
        kind["oorlogen"] += 1
        kind["moest"] += war["aanvallenPerLid"] if war["status"] == "warEnded" else len(member["aanvallen"])
        for attack in member["aanvallen"]:
            target = opponents.get(attack.get("doel"), {})
            kind["aanv"] += 1
            kind["sterren"] += attack["sterren"]
            kind["drie"] += attack["sterren"] == 3
            attacks.append({
                "datum": war["start"][:8], "clan": war["clan"].get("name"), "soort": war["soort"], "ronde": war.get("ronde"),
                "tegen": war["tegenstander"].get("name"), "doel": target.get("naam"), "doelTh": target.get("th"), "eigenTh": member.get("th"),
                "sterren": attack["sterren"], "procent": attack["procent"], "duur": attack.get("duur"),
            })
        if war["status"] == "warEnded" and len(member["aanvallen"]) < war["aanvallenPerLid"]:
            missed.append({"datum": war["start"][:8], "clan": war["clan"].get("name"), "tegen": war["tegenstander"].get("name"), "soort": war["soort"], "aantal": war["aanvallenPerLid"] - len(member["aanvallen"])})
        for attack in war.get("tegenAanvallen", []):
            if attack.get("doel") == tag:
                defence["keer"] += 1
                defence["sterren"] += attack["sterren"]
                defence["drie"] += attack["sterren"] == 3
    return {
        "tag": tag, "naam": raw["name"], "xp": raw.get("expLevel"), "trofeeen": raw.get("trophies"), "besteTrofeeen": raw.get("bestTrophies"),
        "warSterren": raw.get("warStars"), "aanvalWinst": raw.get("attackWins"), "verdedigWinst": raw.get("defenseWins"),
        "donaties": raw.get("donations"), "ontvangen": raw.get("donationsReceived"), "kapitaal": raw.get("clanCapitalContributions"),
        "helden": pack(home(raw.get("heroes", []))),
        "dieren": pack([x for x in home(raw.get("troops", [])) if x["name"] in PETS]),
        "spreuken": pack(home(raw.get("spells", []))),
        "troepen": pack([x for x in home(raw.get("troops", [])) if is_troep(x)]),
        "equipment": pack(sorted(home(raw.get("heroEquipment", [])), key=lambda x: -x["level"])),
        "aanvallen": attacks, "gemist": missed, "perSoort": kinds, "verdediging": defence,
        "beurten": stats.get(tag),
    }


def api_check(clans, raw_sample):
    """Korte controle: welke clans en spelervelden levert de API?"""
    return {
        "getest": NOW.isoformat(timespec="seconds"),
        "bron": BASE,
        "clans": [{"tag": c["tag"], "naam": c["name"], "leden": c.get("members")} for c in clans],
        "spelervelden": sorted(k for k in raw_sample if k in ("league", "leagueTier", "heroes", "heroEquipment", "troops", "role", "clan")),
    }


def main():
    if not os.environ.get("COC_API_KEY"):
        sys.exit("COC_API_KEY ontbreekt")
    history = read_json(DATA / "history.json", {"maanden": [], "spelers": {}})
    basis = basis_inpassen(history)
    for tag in history.setdefault("eerst", {}):  # Iedereen die ooit in een oorlog van ons stond.
        history["spelers"].setdefault(tag, {})

    clans = load_clans()
    if not clans:
        sys.exit("Geen clans gevonden. Controleer de tags in config.json.")

    tags = [m["tag"] for clan in clans for m in clan.get("memberList", [])]
    tags += list(history["spelers"])  # Iedereen die ooit is gezien, blijft gevolgd.
    tags = list(dict.fromkeys(tags))

    previous = {p["tag"]: p for p in read_json(DATA / "latest.json", {}).get("players", [])}
    players, failed, raw_sample, raws = {}, [], {}, {}
    leden = {m["tag"] for clan in clans for m in clan.get("memberList", [])}
    for tag in tags:
        # Oud-leden worden om de beurt een keer per dag opgehaald, leden elke ronde.
        if tag not in leden and tag in previous and zlib.crc32(tag.encode()) % 24 != NOW.hour:
            players[tag] = {**previous[tag], "verouderd": True}
            continue
        try:
            raw = get("/players/" + tag_url(tag))
        except ApiError as error:
            if error.status == 403:
                raise
            raw = None
            failed.append(tag)
        if raw:
            raw_sample = raw_sample or raw
            players[tag] = summarize_player(raw)
            raws[tag] = raw
        elif tag in previous:
            players[tag] = {**previous[tag], "verouderd": True}
        time.sleep(0.05)
    if len(failed) > max(3, len(tags) * 0.2):
        sys.exit(f"Te veel mislukte spelers ({len(failed)}/{len(tags)}). Data niet bijgewerkt.")

    clan_status = []
    for clan in clans:
        status = collect_wars(clan)
        wars_tag = clan["tag"]
        clan_status.append({
            "tag": wars_tag, "nr": clan["nr"], "rol": clan["rol"], "naam": clan["name"], "niveau": clan.get("clanLevel"),
            "leden": clan.get("members"), "warLeague": (clan.get("warLeague") or {}).get("name"),
            "eisen": clan_eisen(clan),
            "logo": (clan.get("badgeUrls") or {}).get("medium"), "winst": clan.get("warWins"), "serie": clan.get("warWinStreak"), **status,
        })

    for tag in players:
        history["spelers"].setdefault(tag, {})
    backfill(clans, history)
    wars = load_wars()
    stats = own_stats(wars)
    log_rows = {}
    for status in clan_status:
        results = history.setdefault("uitslagen", {}).setdefault(status["tag"], {})
        try:
            log = get(f"/clans/{tag_url(status['tag'])}/warlog?limit=200") or {}
        except Private:
            log = {}
        # De officiele log geeft alleen de laatste 60 regels en daarna oude jaren.
        # Daarom vult ClashKing de tussenliggende oorlogen aan.
        regular = [e["endTime"] for e in log.get("items", []) if (e.get("opponent") or {}).get("tag") and e["endTime"] >= f"{NOW.year - 1}"]
        cutoff = min(regular, default="")
        for entry in log.get("items", []):
            if entry["endTime"] < cutoff:
                continue
            # CWL-regels hebben geen tegenstander-tag en tellen niet mee voor de reeksen.
            if entry.get("result") in ("win", "lose", "tie") and (entry.get("opponent") or {}).get("tag"):
                results[entry["endTime"]] = {"win": "w", "lose": "v", "tie": "g"}[entry["result"]]
        # Binnen de log staat alles wat telt; wat er alleen bij ClashKing staat, is een vriendschappelijke oorlog.
        official = set(regular)
        counted = {end: value for end, value in results.items() if end < cutoff or end in official}
        status["ongeslagen"] = unbeaten_streak(counted)
        log_rows[status["tag"]] = log.get("items", [])
        if status["tag"] in ARCHIVE:
            rows = ARCHIVE[status["tag"]]
            for end in [e for e, r in rows.items() if r["soort"] == "oorlog" and e >= cutoff and e not in official and regular]:
                del rows[end]
    month = NOW.strftime("%Y-%m")
    if month not in history["maanden"]:
        history["maanden"].append(month)
        history["maanden"].sort()
    labels = history["maanden"]
    earlier = labels[labels.index(month) - 1] if labels.index(month) else None

    clan_tags = {c["tag"] for c in clans}
    laatst_bijwerken(history, basis, wars, [t for t, p in players.items() if p.get("clanTag") in clan_tags])
    moves = history.setdefault("clans", {})
    output = []
    for tag, player in players.items():
        series = history["spelers"].setdefault(tag, {})
        current = [player["helden"], player["dieren"], player["equip"], player["lgn"] or None, player.get("spreuken"), player.get("troepen")]
        growth, league_wissel = stand_bijwerken(series, month, earlier, current, player.get("verouderd"))
        player = dict(player)
        player["inClan"] = player.get("clanTag") in clan_tags
        player["sinds"] = history.setdefault("eerst", {}).setdefault(tag, NOW.strftime("%Y-%m-%d"))
        player["sindsExact"] = history.get("eerstExact", {}).get(tag, False)
        if tag in CONFIG["bij_ons_sinds"]:
            player["sinds"] = CONFIG["bij_ons_sinds"][tag]
            player["sindsVast"] = True
        move = moves.get(tag)
        if not player.get("verouderd"):
            if not move or move["tag"] != player.get("clanTag"):
                moves[tag] = move = {
                    "tag": player.get("clanTag"), "naam": player.get("clan"),
                    "vorige": move["naam"] if move else None,
                    "sinds": NOW.strftime("%Y-%m-%d") if move else None,
                }
        if move and move.get("vorige"):
            player["vorigeClan"], player["overstap"] = move["vorige"], move["sinds"]
        player["d"] = growth
        player["dl"] = league_wissel
        player["laatst"] = history["laatst"].get(tag)
        player["s"] = [series.get(m) for m in labels]
        player["cw"] = stats.get(tag)
        output.append(player)
    activiteit({p["tag"]: p for p in output}, clan_tags)

    first_war = parse_time(wars[0]["start"]) if wars else None
    window_full = bool(first_war and first_war <= NOW - timedelta(days=CONFIG["venster_dagen"]))
    summaries = [war_summary(w) for w in reversed(wars)][:200]

    write_json(DATA / "history.json", history)
    roof_bijwerken(clans)
    records_bijwerken(clans)
    ranked_bijwerken(players, raws, leden, clan_tags)
    ranked_officieel(players, raws, leden, clan_tags)
    catalogus_bijwerken(raws)
    games_bijwerken(players, clan_tags)
    maand_dagen = CONFIG["maand_venster_dagen"]
    maand_oorlog, maand_verdediging = own_stats(wars, maand_dagen), defence_stats(wars, maand_dagen)
    clan_nummers = {c["tag"]: c["nr"] for c in clan_status}
    historie_bijwerken(basis, month, {
        tag: maand_record(players[tag], raw, clan_nummers.get(players[tag].get("clanTag")), maand_oorlog.get(tag), maand_verdediging.get(tag))
        for tag, raw in raws.items()
    })
    archive = read_json(DATA / "archief.json", {})
    archive.update({tag: sorted(rows.values(), key=lambda r: r["einde"], reverse=True) for tag, rows in ARCHIVE.items()})
    for tag, entries in log_rows.items():
        rows = {r["einde"]: r for r in archive.get(tag, [])}
        for entry in entries:
            opponent = entry.get("opponent") or {}
            if not opponent.get("tag"):
                continue  # CWL-seizoensregel
            per_member = entry.get("attacksPerMember") or 2
            rows[entry["endTime"]] = {
                "soort": "oorlog", "ronde": None, "start": entry["endTime"], "einde": entry["endTime"],
                "grootte": entry.get("teamSize"), "totaal": (entry.get("teamSize") or 0) * per_member,
                "gebruikt": (entry.get("clan") or {}).get("attacks") or 0,
                "tegen": opponent.get("name"), "tegenLogo": (opponent.get("badgeUrls") or {}).get("small"),
                "s": [entry["clan"].get("stars") or 0, opponent.get("stars") or 0],
                "p": [entry["clan"].get("destructionPercentage") or 0, opponent.get("destructionPercentage") or 0],
            }
        ordered = sorted(rows.values(), key=lambda r: r["einde"], reverse=True)
        unique = []
        for row in ordered:
            # Dezelfde oorlog kan in beide bronnen een iets andere eindtijd hebben.
            if unique and unique[-1]["tegen"] == row["tegen"] and unique[-1]["soort"] == row["soort"] and unique[-1]["einde"][:8] == row["einde"][:8]:
                continue
            unique.append(row)
        archive[tag] = unique
    dekking = dekking_bepalen(archive, wars)
    write_json(DATA / "archief.json", archive)
    write_json(DATA / "latest.json", {
        "bijgewerkt": NOW.isoformat(timespec="seconds"),
        "sindsBegin": min(history["eerst"].values()),
        "maanden": labels,
        "vensterDagen": CONFIG["venster_dagen"],
        "vensterVol": window_full,
        "eersteOorlog": wars[0]["start"][:8] if wars else None,
        "grensAanvallen": CONFIG["grens_aanvallen"],
        "gamesDoel": CONFIG["clangames_doel"],
        "cwlLeague": history.get("cwlLeague", {}),
        "rankedLimiet": CONFIG["ranked_limieten"],
        "rankedVrij": CONFIG["ranked_veilige_weken"],
        "dekking": dekking,
        "clans": clan_status,
        "players": output,
        "oorlogen": summaries,
    })
    write_json(DATA / "api_check.json", api_check(clans, raw_sample))
    for tag, raw in raws.items():
        write_json(DATA / "speler" / (tag.strip("#") + ".json"), player_profile(tag, raw, wars, stats))
    print(f"{len(clans)} clans, {len(output)} spelers, {len(wars)} oorlogen bewaard, {len(failed)} mislukt")


if __name__ == "__main__":
    try:
        main()
    except Private:
        sys.exit("API-fout: de sleutel is geweigerd. Controleer de sleutel en het IP-adres.")
    except ApiError as error:
        if error.status >= 500:  # Onderhoud of storing bij Supercell of de proxy: de volgende ronde gaat gewoon door.
            print(f"API tijdelijk niet beschikbaar, ronde overgeslagen: {error}")
            sys.exit(75)  # De workflow draait halve wijzigingen terug en slaat niets op.
        sys.exit(f"API-fout: {error}")
