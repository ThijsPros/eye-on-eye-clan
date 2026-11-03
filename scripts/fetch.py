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
    for number, round_ in enumerate(group.get("rounds", []), 1):
        for war_tag in round_.get("warTags", []):
            if war_tag == "#0":
                continue
            file_id = f"cwl-{safe_tag}-{war_tag.strip('#')}"
            stored = read_json(WARS / (file_id + ".json"), None)
            if stored and stored["status"] == "warEnded":
                break
            war = get("/clanwarleagues/wars/" + tag_url(war_tag))
            if war and tag in (war["clan"].get("tag"), war["opponent"].get("tag")):
                normalized = normalize_war(war, file_id, "cwl", tag, group.get("season"))
                normalized["ronde"] = number
                save_war(normalized)
                break
    return status


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
        wars = [w for w in wars if w.get("state") == "warEnded" and w.get("startTime")]
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
            if stored and stored["status"] == "warEnded":
                continue
            season = f"{war['startTime'][:4]}-{war['startTime'][4:6]}-01" if war.get("tag") else None
            normalized = normalize_war(war, normalized_id, "cwl" if war.get("tag") else "oorlog", tag, season)
            if war.get("tag"):
                normalized["ronde"] = war["_ronde"]
            save_war(normalized)

    ours = {clan["tag"] for clan in clans}
    done = history.setdefault("inUitGehad", [])
    for tag in history["spelers"]:
        if tag in done:
            continue
        events = clashking(f"/player/{tag_url(tag)}/join-leave?limit=1000")
        if events is None:
            complete = False
            continue
        for event in events:
            if event.get("clan") in ours and event.get("time"):
                first[tag] = min(first.get(tag, event["time"][:10]), event["time"][:10])
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
            {"naam": m["naam"], "pos": m.get("pos"), "aanvallen": [{"sterren": a["sterren"], "procent": a["procent"]} for a in m["aanvallen"]]}
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
    players, failed, raw_sample = {}, [], {}
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
    summaries = [war_summary(w) for w in reversed(wars)][:60]

    write_json(DATA / "history.json", history)
    write_json(DATA / "latest.json", {
        "bijgewerkt": NOW.isoformat(timespec="seconds"),
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
    print(f"{len(clans)} clans, {len(output)} spelers, {len(wars)} oorlogen bewaard, {len(failed)} mislukt")


if __name__ == "__main__":
    try:
        main()
    except Private:
        sys.exit("API-fout: de sleutel is geweigerd. Controleer de sleutel en het IP-adres.")
    except ApiError as error:
        sys.exit(f"API-fout: {error}")
