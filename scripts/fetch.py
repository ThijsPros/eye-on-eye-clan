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
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "docs" / "data"
WARS = DATA / "wars"
CWL = DATA / "cwl"
CONFIG = json.loads((ROOT / "config.json").read_text(encoding="utf8"))
BASE = os.environ.get("COC_API_BASE", "https://cocproxy.royaleapi.dev/v1").rstrip("/")
NOW = datetime.now(timezone.utc)
CLASHKING = "https://api.clashk.ing"

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
            if error.code not in (429, 500, 502, 503, 504) or attempt == 3:
                raise ApiError(error.code, reason) from None
        except (urllib.error.URLError, TimeoutError) as error:
            if attempt == 3:
                raise ApiError(0, type(error).__name__) from None
        time.sleep(2 ** attempt)


def tag_url(tag):
    return urllib.parse.quote(tag, safe="")


def read_json(path, default):
    return json.loads(path.read_text(encoding="utf8")) if path.exists() else default


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
    if "max" in other and own["sterren"] > other["max"]:
        return "w"
    if "max" in own and other["sterren"] > own["max"]:
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
        if league:
            month = war["startTime"][:6]
            months[month] = number = months.get(month, 0) + 1
        per_member = 1 if league else war.get("attacksPerMember") or 2
        rows[war["endTime"]] = {
            "soort": "cwl" if league else "oorlog", "ronde": number, "start": war["startTime"], "einde": war["endTime"],
            "grootte": war.get("teamSize"), "totaal": (war.get("teamSize") or 0) * per_member, "gebruikt": ours.get("attacks") or 0,
            "tegen": theirs.get("name"), "tegenLogo": (theirs.get("badgeUrls") or {}).get("small"),
            "s": [ours.get("stars") or 0, theirs.get("stars") or 0],
            "p": [ours.get("destructionPercentage") or 0, theirs.get("destructionPercentage") or 0],
        }
    ARCHIVE[tag] = rows


def backfill(clans, history):
    """Vult oorlogen en eerste datums aan. Draait een keer per dag."""
    today = NOW.strftime("%Y-%m-%d")
    if history.get("ckDatum") == today:
        return
    first = history.setdefault("eerst", {})
    cutoff = NOW - timedelta(days=CONFIG["venster_dagen"] + 35)
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

    ours = {clan["tag"] for clan in clans}
    done = history.setdefault("inUitGehad", [])
    exact = history.setdefault("eerstExact", {})
    for tag in history["spelers"]:
        if tag in done:
            continue
        events = clashking(f"/player/{tag_url(tag)}/join-leave?limit=1000")
        if events is None:
            complete = False
            continue
        own_events = sorted((e for e in events if e.get("clan") in ours and e.get("time")), key=lambda e: e["time"])
        if own_events:
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


def own_stats(wars):
    """Eigen telling per speler over het ingestelde venster."""
    cutoff = NOW - timedelta(days=CONFIG["venster_dagen"])
    stats = {}
    for war in wars:
        start = parse_time(war.get("start"))
        if war["status"] == "preparation" or not start or start < cutoff:
            continue
        for member in war["leden"]:
            s = stats.setdefault(member["tag"], {"aanv": 0, "moest": 0, "sterren": 0, "drie": 0, "gemis": 0, "oorlogen": 0, "cwlOorlogen": 0, "laatste": None})
            s["oorlogen"] += 1
            s["cwlOorlogen"] += war["soort"] == "cwl"
            s["aanv"] += len(member["aanvallen"])
            s["moest"] += war["aanvallenPerLid"] if war["status"] == "warEnded" else len(member["aanvallen"])
            s["sterren"] += sum(a["sterren"] for a in member["aanvallen"])
            s["drie"] += sum(a["sterren"] == 3 for a in member["aanvallen"])
            s["laatste"] = max(s["laatste"] or "", war["start"][:8])
            # Gemist telt alleen als de oorlog zeker is afgelopen en volledig is gezien.
            if war["status"] == "warEnded":
                s["gemis"] += max(0, war["aanvallenPerLid"] - len(member["aanvallen"]))
    for s in stats.values():
        s["gem"] = round(s["sterren"] / s["aanv"], 2) if s["aanv"] else None
        s["drieProcent"] = round(100 * s["drie"] / s["aanv"], 1) if s["aanv"] else None
        s["laatste"] = f"{s['laatste'][:4]}-{s['laatste'][4:6]}-{s['laatste'][6:]}" if s["laatste"] else None
    return stats


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

    clans = load_clans()
    if not clans:
        sys.exit("Geen clans gevonden. Controleer de tags in config.json.")

    tags = [m["tag"] for clan in clans for m in clan.get("memberList", [])]
    tags += list(history["spelers"])  # Iedereen die ooit is gezien, blijft gevolgd.
    tags = list(dict.fromkeys(tags))

    previous = {p["tag"]: p for p in read_json(DATA / "latest.json", {}).get("players", [])}
    players, failed, raw_sample, raws = {}, [], {}, {}
    for tag in tags:
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
    moves = history.setdefault("clans", {})
    output = []
    for tag, player in players.items():
        series = history["spelers"].setdefault(tag, {})
        current = [player["helden"], player["dieren"], player["equip"], player["lgn"]]
        if not player.get("verouderd"):
            series[month] = current
        before = series.get(earlier)
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
        player["d"] = (
            {"h": current[0] - before[0], "p": current[1] - before[1], "e": current[2] - before[2],
             "l": current[3] - before[3] if len(before) > 3 and current[3] and before[3] else None}
            if before else None
        )
        player["s"] = [series.get(m, [None] * 3)[:3] if m in series else None for m in labels]
        player["cw"] = stats.get(tag)
        output.append(player)

    first_war = parse_time(wars[0]["start"]) if wars else None
    window_full = bool(first_war and first_war <= NOW - timedelta(days=CONFIG["venster_dagen"]))
    summaries = [war_summary(w) for w in reversed(wars)][:200]

    write_json(DATA / "history.json", history)
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
    write_json(DATA / "archief.json", archive)
    write_json(DATA / "latest.json", {
        "bijgewerkt": NOW.isoformat(timespec="seconds"),
        "sindsBegin": min(history["eerst"].values()),
        "maanden": labels,
        "vensterDagen": CONFIG["venster_dagen"],
        "vensterVol": window_full,
        "eersteOorlog": wars[0]["start"][:8] if wars else None,
        "grensAanvallen": CONFIG["grens_aanvallen"],
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
        sys.exit(f"API-fout: {error}")
