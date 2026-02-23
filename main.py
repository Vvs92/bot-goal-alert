import requests
import logging
import os
import asyncio
import sys
import time
from telegram import Bot

SPORTMONKS_TOKEN = os.environ.get("SPORTMONKS_TOKEN", "")
TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN", "")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "")

print("DEMARRAGE BOT", flush=True)

LEAGUES = {
    2: "Champions League",
    5: "Europa League",
    8: "Premier League",
    564: "La Liga",
    384: "Serie A",
    82: "Bundesliga",
    301: "Ligue 1",
    79: "Bundesliga 2",
    9: "Championship",
    72: "Eredivisie",
    600: "Super Lig",
    208: "Belgian Pro League",
    501: "Premiership (Ecosse)",
}

THRESHOLD = 50
INTERVAL = 90
PRE_MATCH_WINDOW = 30

logging.basicConfig(format="%(asctime)s %(message)s", level=logging.INFO, stream=sys.stdout)

URL = "https://api.sportmonks.com/v3/football"
alerts = {}
prematch_sent = {}
form_cache = {}


# ─────────────────────────────────────────
# HELPERS GENERAUX
# ─────────────────────────────────────────

def team_name(fixture, home=True):
    try:
        for p in fixture.get("participants", []):
            loc = p.get("meta", {}).get("location", "")
            if home and loc == "home":
                return str(p.get("name", "Home"))
            if not home and loc == "away":
                return str(p.get("name", "Away"))
    except Exception:
        pass
    return "Home" if home else "Away"


def get_team_id(fixture, home=True):
    try:
        for p in fixture.get("participants", []):
            loc = p.get("meta", {}).get("location", "")
            if home and loc == "home":
                return p.get("id")
            if not home and loc == "away":
                return p.get("id")
    except Exception:
        pass
    return None


def get_goals(fixture, home=True):
    try:
        for s in fixture.get("scores", []):
            if s.get("description") == "CURRENT":
                sd = s.get("score", {})
                if home:
                    return int(sd.get("goals", 0) or 0)
                else:
                    return int(sd.get("participant", 0) or 0)
    except Exception:
        pass
    return 0


def get_minute(fixture):
    try:
        starting = fixture.get("starting_at_timestamp", 0)
        if starting:
            elapsed = int((time.time() - starting) / 60)
            if 0 < elapsed < 130:
                return elapsed
    except Exception:
        pass
    return 0


def stat_val(stats, code, tid=None):
    try:
        for s in stats:
            t = s.get("type", {})
            tcode = t.get("code", "") if isinstance(t, dict) else ""
            if tcode == code:
                if tid is None or s.get("participant_id") == tid:
                    v = s.get("data", {}).get("value", 0)
                    return float(v) if v else 0.0
    except Exception:
        pass
    return 0.0


# ─────────────────────────────────────────
# FORME DES EQUIPES
# ─────────────────────────────────────────

def get_team_form(team_id):
    """Analyse la forme recente d'une equipe sur ses 5 derniers matchs"""
    cache_key = str(team_id)
    if cache_key in form_cache:
        cached_time, cached_data = form_cache[cache_key]
        if time.time() - cached_time < 3600:
            return cached_data

    try:
        r = requests.get(
            URL + "/teams/" + str(team_id) + "/fixtures",
            params={
                "api_token": SPORTMONKS_TOKEN,
                "include": "scores;events.type",
                "per_page": 5,
                "sort": "-starting_at"
            },
            timeout=15
        )
        if r.status_code != 200:
            return {}

        d = r.json()
        matches = d.get("data", [])

        if not matches:
            return {}

        form = {
            "unbeaten": 0,
            "wins": 0,
            "draws": 0,
            "losses": 0,
            "scored_1st_half": 0,
            "scored_2nd_half": 0,
            "scored_last_15": 0,
            "always_scored": True,
            "clean_sheets": 0,
            "total_matches": 0,
        }

        for match in matches:
            form["total_matches"] += 1
            fid = match.get("id")
            scores = match.get("scores", [])
            events = match.get("events", [])

            home_goals = 0
            away_goals = 0
            team_goals_1h = 0
            team_goals_2h = 0
            team_goals_last15 = 0
            team_scored = False

            # Determine si l'equipe joue a domicile ou exterieur
            is_home = False
            participants = match.get("participants", [])
            for p in participants:
                if p.get("id") == team_id:
                    if p.get("meta", {}).get("location") == "home":
                        is_home = True

            # Score final
            for s in scores:
                if s.get("description") == "FT":
                    sd = s.get("score", {})
                    home_goals = int(sd.get("goals", 0) or 0)
                    away_goals = int(sd.get("participant", 0) or 0)

            team_g = home_goals if is_home else away_goals
            opp_g = away_goals if is_home else home_goals

            if team_g > opp_g:
                form["wins"] += 1
                form["unbeaten"] += 1
            elif team_g == opp_g:
                form["draws"] += 1
                form["unbeaten"] += 1
            else:
                form["losses"] += 1

            if team_g == 0:
                form["always_scored"] = False
            else:
                team_scored = True

            if opp_g == 0:
                form["clean_sheets"] += 1

            # Analyse des buts par periode via events
            for ev in events:
                t = ev.get("type", {})
                code = t.get("code", "") if isinstance(t, dict) else ""
                if "goal" not in code.lower():
                    continue
                ev_team = ev.get("participant_id") or ev.get("team_id")
                if ev_team != team_id:
                    continue
                minute_ev = int(ev.get("minute", 0) or 0)
                if minute_ev <= 45:
                    team_goals_1h += 1
                else:
                    team_goals_2h += 1
                if minute_ev >= 75:
                    team_goals_last15 += 1

            if team_goals_1h > 0:
                form["scored_1st_half"] += 1
            if team_goals_2h > 0:
                form["scored_2nd_half"] += 1
            if team_goals_last15 > 0:
                form["scored_last_15"] += 1

        form_cache[cache_key] = (time.time(), form)
        return form

    except Exception as e:
        print("ERREUR form: " + str(e), flush=True)
        return {}


def build_form_insights(form, team_name_str, current_goals, minute):
    """Genere les insights de forme pertinents pour le contexte du match"""
    insights = []
    n = form.get("total_matches", 0)
    if n == 0:
        return insights

    unbeaten = form.get("unbeaten", 0)
    wins = form.get("wins", 0)
    always_scored = form.get("always_scored", False)
    scored_1h = form.get("scored_1st_half", 0)
    scored_2h = form.get("scored_2nd_half", 0)
    scored_last15 = form.get("scored_last_15", 0)
    clean_sheets = form.get("clean_sheets", 0)

    # Invincibilite
    if unbeaten == n and n >= 4:
        insights.append("\U0001f525 " + team_name_str + " est invaincue sur ses " + str(n) + " derniers matchs")
    elif wins >= 4:
        insights.append("\U0001f525 " + team_name_str + " a gagne " + str(wins) + " de ses " + str(n) + " derniers matchs")

    # Marque toujours au moins 1 but
    if always_scored and n >= 4:
        insights.append("\u26bd " + team_name_str + " a marque dans chacun de ses " + str(n) + " derniers matchs")

    # Habitudes de buts par periode
    if minute < 46 and scored_1h >= int(n * 0.6):
        insights.append("\u23f0 " + team_name_str + " marque souvent en 1ere MT (" + str(scored_1h) + "/" + str(n) + " matchs)")

    if minute >= 46 and scored_2h >= int(n * 0.6):
        insights.append("\u23f0 " + team_name_str + " marque souvent en 2eme MT (" + str(scored_2h) + "/" + str(n) + " matchs)")

    if minute >= 70 and scored_last15 >= int(n * 0.5):
        insights.append("\u23f0 " + team_name_str + " marque souvent en fin de match (" + str(scored_last15) + "/" + str(n) + " matchs)")

    # Equipe qui n'a pas encore marque ce match
    if current_goals == 0 and always_scored and n >= 3:
        insights.append("\u26a0\ufe0f " + team_name_str + " n'a pas encore marque - marque dans tous ses derniers matchs!")

    return insights


# ─────────────────────────────────────────
# API FIXTURES
# ─────────────────────────────────────────

def get_fixtures():
    try:
        r = requests.get(
            URL + "/livescores/inplay",
            params={
                "api_token": SPORTMONKS_TOKEN,
                "include": "participants;scores;state;statistics.type",
                "per_page": 50
            },
            timeout=15
        )
        print("API status: " + str(r.status_code), flush=True)
        if r.status_code != 200:
            return []
        d = r.json()
        all_f = d.get("data", [])
        filtered = [f for f in all_f if f.get("league_id") in LEAGUES]
        print(str(len(all_f)) + " matchs live, " + str(len(filtered)) + " dans nos ligues", flush=True)
        return filtered
    except Exception as e:
        print("ERREUR API live: " + str(e), flush=True)
        return []


def get_upcoming_fixtures():
    try:
        now = int(time.time())
        soon = now + (PRE_MATCH_WINDOW * 60)
        from datetime import datetime, timezone
        date_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        r = requests.get(
            URL + "/fixtures/date/" + date_str,
            params={
                "api_token": SPORTMONKS_TOKEN,
                "include": "participants",
                "per_page": 100
            },
            timeout=15
        )
        if r.status_code != 200:
            return []
        d = r.json()
        all_f = d.get("data", [])
        upcoming = []
        for f in all_f:
            if f.get("league_id") not in LEAGUES:
                continue
            start = f.get("starting_at_timestamp", 0)
            if start and now < start <= soon:
                upcoming.append(f)
        print(str(len(upcoming)) + " match(s) a venir dans 30min", flush=True)
        return upcoming
    except Exception as e:
        print("ERREUR upcoming: " + str(e), flush=True)
        return []


def get_top_scorers(fixture_id, team_id):
    try:
        r = requests.get(
            URL + "/fixtures/" + str(fixture_id) + "/lineups",
            params={
                "api_token": SPORTMONKS_TOKEN,
                "include": "player.statistics.details.type",
            },
            timeout=15
        )
        if r.status_code != 200:
            return []
        d = r.json()
        players = d.get("data", [])
        scorers = []
        for p in players:
            if p.get("team_id") != team_id:
                continue
            player = p.get("player", {})
            name = str(player.get("name", ""))
            if not name:
                continue
            season_goals = 0
            stats = player.get("statistics", [])
            for stat in stats:
                details = stat.get("details", [])
                for detail in details:
                    t = detail.get("type", {})
                    if isinstance(t, dict) and t.get("code") == "goals":
                        val = detail.get("data", {}).get("value", 0)
                        season_goals += int(val) if val else 0
            if season_goals >= 3:
                scorers.append({"name": name, "season_goals": season_goals})
        scorers.sort(key=lambda x: x["season_goals"], reverse=True)
        return scorers[:4]
    except Exception as e:
        print("ERREUR scorers: " + str(e), flush=True)
        return []


def get_player_recent_form(fixture_id, team_id):
    try:
        r = requests.get(
            URL + "/teams/" + str(team_id) + "/fixtures",
            params={
                "api_token": SPORTMONKS_TOKEN,
                "include": "events.type",
                "per_page": 5
            },
            timeout=15
        )
        if r.status_code != 200:
            return {}
        d = r.json()
        recent_matches = d.get("data", [])
        scorer_recent = {}
        for match in recent_matches[:3]:
            events = match.get("events", [])
            for ev in events:
                t = ev.get("type", {})
                if isinstance(t, dict) and "goal" in t.get("code", "").lower():
                    player_name = str(ev.get("player_name", "") or "")
                    if player_name:
                        scorer_recent[player_name] = scorer_recent.get(player_name, 0) + 1
        return scorer_recent
    except Exception as e:
        print("ERREUR recent form: " + str(e), flush=True)
        return {}


# ─────────────────────────────────────────
# MOMENTUM
# ─────────────────────────────────────────

def get_dominant_team(fixture, stats):
    hid = None
    aid = None
    hname = "Home"
    aname = "Away"
    for p in fixture.get("participants", []):
        loc = p.get("meta", {}).get("location", "")
        if loc == "home":
            hid = p.get("id")
            hname = str(p.get("name", "Home"))
        else:
            aid = p.get("id")
            aname = str(p.get("name", "Away"))

    h_dan = stat_val(stats, "dangerous-attacks", hid)
    a_dan = stat_val(stats, "dangerous-attacks", aid)
    h_son = stat_val(stats, "shots-on-target", hid)
    a_son = stat_val(stats, "shots-on-target", aid)
    h_cor = stat_val(stats, "corners", hid)
    a_cor = stat_val(stats, "corners", aid)

    h_total = h_dan + (h_son * 3) + h_cor
    a_total = a_dan + (a_son * 3) + a_cor

    if h_total > a_total * 1.4:
        return hname, "home"
    elif a_total > h_total * 1.4:
        return aname, "away"
    else:
        return None, "balanced"


def momentum(fixture):
    score = 0
    stats = fixture.get("statistics", [])
    hid = None
    aid = None
    for p in fixture.get("participants", []):
        loc = p.get("meta", {}).get("location", "")
        if loc == "home":
            hid = p.get("id")
        else:
            aid = p.get("id")

    son = stat_val(stats, "shots-on-target", hid) + stat_val(stats, "shots-on-target", aid)
    sib = stat_val(stats, "shots-insidebox", hid) + stat_val(stats, "shots-insidebox", aid)
    cor = stat_val(stats, "corners", hid) + stat_val(stats, "corners", aid)
    dan = stat_val(stats, "dangerous-attacks", hid) + stat_val(stats, "dangerous-attacks", aid)
    tot = stat_val(stats, "shots-total", hid) + stat_val(stats, "shots-total", aid)

    if son >= 10:
        score += 25
    elif son >= 6:
        score += 16
    elif son >= 3:
        score += 8

    if sib >= 12:
        score += 25
    elif sib >= 7:
        score += 16
    elif sib >= 4:
        score += 8

    if cor >= 10:
        score += 15
    elif cor >= 6:
        score += 9
    elif cor >= 3:
        score += 4

    if dan >= 80:
        score += 20
    elif dan >= 50:
        score += 13
    elif dan >= 25:
        score += 6

    if tot > 0 and son / tot >= 0.5:
        score += 10

    return min(score, 100), son, sib, cor, dan, tot


# ─────────────────────────────────────────
# MESSAGES
# ─────────────────────────────────────────

def build_prematch_message(fixture, h, a, league, minutes_before, h_scorers, a_scorers, h_recent, a_recent, h_form, a_form):
    sep = "\u2501" * 20
    hid = get_team_id(fixture, True)
    aid = get_team_id(fixture, False)

    h_lines = []
    for s in h_scorers:
        name = s["name"]
        goals = s["season_goals"]
        line = "  \u2022 " + name + " - " + str(goals) + " buts cette saison"
        recent = h_recent.get(name, 0)
        if recent >= 2:
            line += "\n    \u2705 En feu! A marque lors de ses " + str(recent) + " derniers matchs"
        elif recent == 1:
            line += "\n    \u2705 A marque lors de son dernier match"
        h_lines.append(line)

    a_lines = []
    for s in a_scorers:
        name = s["name"]
        goals = s["season_goals"]
        line = "  \u2022 " + name + " - " + str(goals) + " buts cette saison"
        recent = a_recent.get(name, 0)
        if recent >= 2:
            line += "\n    \u2705 En feu! A marque lors de ses " + str(recent) + " derniers matchs"
        elif recent == 1:
            line += "\n    \u2705 A marque lors de son dernier match"
        a_lines.append(line)

    h_text = "\n".join(h_lines) if h_lines else "  \u2022 Donnees non disponibles"
    a_text = "\n".join(a_lines) if a_lines else "  \u2022 Donnees non disponibles"

    # Forme equipes
    h_form_lines = []
    a_form_lines = []

    if h_form:
        n = h_form.get("total_matches", 0)
        if n > 0:
            if h_form.get("unbeaten", 0) == n:
                h_form_lines.append("  \U0001f7e2 Invaincue sur " + str(n) + " matchs")
            if h_form.get("always_scored", False):
                h_form_lines.append("  \u26bd Marque dans chacun de ses " + str(n) + " derniers matchs")
            s1h = h_form.get("scored_1st_half", 0)
            if s1h >= int(n * 0.6):
                h_form_lines.append("  \u23f0 Marque souvent en 1ere MT (" + str(s1h) + "/" + str(n) + ")")
            sl15 = h_form.get("scored_last_15", 0)
            if sl15 >= int(n * 0.5):
                h_form_lines.append("  \u23f0 Marque souvent en fin de match (" + str(sl15) + "/" + str(n) + ")")

    if a_form:
        n = a_form.get("total_matches", 0)
        if n > 0:
            if a_form.get("unbeaten", 0) == n:
                a_form_lines.append("  \U0001f7e2 Invaincue sur " + str(n) + " matchs")
            if a_form.get("always_scored", False):
                a_form_lines.append("  \u26bd Marque dans chacun de ses " + str(n) + " derniers matchs")
            s1h = a_form.get("scored_1st_half", 0)
            if s1h >= int(n * 0.6):
                a_form_lines.append("  \u23f0 Marque souvent en 1ere MT (" + str(s1h) + "/" + str(n) + ")")
            sl15 = a_form.get("scored_last_15", 0)
            if sl15 >= int(n * 0.5):
                a_form_lines.append("  \u23f0 Marque souvent en fin de match (" + str(sl15) + "/" + str(n) + ")")

    h_form_text = "\n".join(h_form_lines) if h_form_lines else "  \u2022 Forme non disponible"
    a_form_text = "\n".join(a_form_lines) if a_form_lines else "  \u2022 Forme non disponible"

    # Recommandations
    recs = []
    top_all = []
    for s in h_scorers:
        recent = h_recent.get(s["name"], 0)
        top_all.append((s["name"], s["season_goals"], recent))
    for s in a_scorers:
        recent = a_recent.get(s["name"], 0)
        top_all.append((s["name"], s["season_goals"], recent))
    top_all.sort(key=lambda x: (x[2] * 10 + x[1]), reverse=True)

    for name, goals, recent in top_all[:3]:
        if recent >= 1 and goals >= 4:
            recs.append("  \u2192 \u26bd Anytime scorer: " + name + " (forme + volume)")
        elif recent >= 2:
            recs.append("  \u2192 \u26bd Anytime scorer: " + name + " (en feu)")
        elif goals >= 6:
            recs.append("  \u2192 \u26bd Anytime scorer: " + name + " (top buteur saison)")

    if h_form.get("always_scored") and a_form.get("always_scored"):
        recs.append("  \u2192 \U0001f3af BTTS probable - les deux equipes marquent toujours")

    if not recs:
        recs.append("  \u2192 \U0001f440 Surveiller les buteurs reguliers")

    recs_text = "\n".join(recs)

    msg = (
        "\u26bd PRE-MATCH - BUTEURS A SURVEILLER\n"
        + sep + "\n"
        + "\U0001f3c6 " + league + "\n"
        + "\u2694\ufe0f " + h + " vs " + a + "\n"
        + "\u23f1\ufe0f Coup d'envoi dans ~" + str(minutes_before) + " min\n"
        + sep + "\n"
        + "\U0001f534 " + h + " - Buteurs:\n"
        + h_text + "\n"
        + "\U0001f4ca Forme " + h + ":\n"
        + h_form_text + "\n"
        + sep + "\n"
        + "\U0001f535 " + a + " - Buteurs:\n"
        + a_text + "\n"
        + "\U0001f4ca Forme " + a + ":\n"
        + a_form_text + "\n"
        + sep + "\n"
        + "\U0001f4a1 QUOI JOUER SUR BETIFY:\n"
        + recs_text + "\n"
        + sep + "\n"
        + "\u26a0\ufe0f Parie de facon responsable"
    )

    return msg


def build_live_message(fixture, score, son, sib, cor, dan, tot, h_form, a_form):
    minute = get_minute(fixture)
    lid = fixture.get("league_id")
    league = str(LEAGUES.get(lid, "Ligue"))
    h = team_name(fixture, True)
    a = team_name(fixture, False)
    hg = get_goals(fixture, True)
    ag = get_goals(fixture, False)
    stats = fixture.get("statistics", [])
    dominant_team, dominant_side = get_dominant_team(fixture, stats)
    total_goals = hg + ag

    gauge = "\U0001f7e9" * int(score / 10) + "\u2b1c" * (10 - int(score / 10))

    if score >= 70:
        lvl = "ALERTE MAX"
        emoji = "\U0001f534"
    elif score >= 55:
        lvl = "FORTE PRESSION"
        emoji = "\U0001f7e0"
    else:
        lvl = "PRESSION"
        emoji = "\U0001f7e1"

    stats_lines = []
    if son >= 3:
        stats_lines.append("  \u2022 " + str(int(son)) + " tirs cadres")
    if sib >= 3:
        stats_lines.append("  \u2022 " + str(int(sib)) + " tirs dans la surface")
    if cor >= 3:
        stats_lines.append("  \u2022 " + str(int(cor)) + " corners")
    if dan >= 20:
        stats_lines.append("  \u2022 " + str(int(dan)) + " attaques dangereuses")

    # Forme insights
    form_lines = []
    h_insights = build_form_insights(h_form, h, hg, minute)
    a_insights = build_form_insights(a_form, a, ag, minute)
    form_lines.extend(h_insights)
    form_lines.extend(a_insights)

    recs = []

    if score >= 55 and (son >= 6 or sib >= 6):
        if dominant_side == "home":
            recs.append("  \u2192 \u26bd Prochain but: " + h + " (domine)")
        elif dominant_side == "away":
            recs.append("  \u2192 \u26bd Prochain but: " + a + " (domine)")
        else:
            recs.append("  \u2192 \u26bd Prochain but: Match ouvert")

    if score >= 55 and (son >= 5 or sib >= 5):
        recs.append("  \u2192 \U0001f4c8 Over " + str(total_goals) + ".5 buts dans le match")
        if minute < 46:
            recs.append("  \u2192 \U0001f4c8 Over 0.5 buts reste 1ere MT")
        else:
            recs.append("  \u2192 \U0001f4c8 Over 0.5 buts reste 2eme MT")

    if cor >= 5:
        next_cor = int(cor) + 2
        recs.append("  \u2192 \U0001f6a9 Plus de corners / Over " + str(next_cor) + ".5 corners")

    if dominant_team and score >= 55:
        recs.append("  \u2192 \U0001f621 " + dominant_team + " etouffe l'adversaire")

    if hg == 0 and ag == 0 and score >= 55 and minute >= 30:
        recs.append("  \u2192 \U0001f3af BTTS possible - 0-0 sous forte pression")
    elif total_goals > 0 and (hg == 0 or ag == 0) and score >= 55:
        recs.append("  \u2192 \U0001f3af BTTS possible - equipe a 0 sous pression")

    if dan >= 70:
        recs.append("  \u2192 \U0001f7e8 Cartons probables (intensite elevee)")

    if not recs:
        recs.append("  \u2192 \U0001f440 A surveiller - pression en hausse")

    stats_text = "\n".join(stats_lines) if stats_lines else "  \u2022 Stats en cours"
    recs_text = "\n".join(recs)
    form_text = "\n".join(form_lines) if form_lines else ""
    sep = "\u2501" * 20

    form_section = ""
    if form_text:
        form_section = sep + "\n\U0001f4ca FORME DES EQUIPES:\n" + form_text + "\n"

    msg = (
        emoji + " " + lvl + " - BUT POTENTIEL\n"
        + sep + "\n"
        + "\U0001f3c6 " + league + "\n"
        + "\u2694\ufe0f  " + h + " " + str(hg) + " - " + str(ag) + " " + a + "\n"
        + "\u23f1\ufe0f  " + str(minute) + "' | Score momentum: " + str(score) + "/100\n"
        + gauge + "\n"
        + sep + "\n"
        + "\U0001f4ca STATS:\n"
        + stats_text + "\n"
        + form_section
        + sep + "\n"
        + "\U0001f4a1 QUOI JOUER SUR BETIFY:\n"
        + recs_text + "\n"
        + sep + "\n"
        + "\u26a0\ufe0f Parie de facon responsable"
    )

    return msg


# ─────────────────────────────────────────
# BOUCLE PRINCIPALE
# ─────────────────────────────────────────

async def send_prematch_alerts(bot):
    upcoming = get_upcoming_fixtures()
    for fixture in upcoming:
        fid = fixture.get("id")
        if str(fid) in prematch_sent:
            continue

        start = fixture.get("starting_at_timestamp", 0)
        minutes_before = int((start - time.time()) / 60)
        if minutes_before < 1:
            continue

        lid = fixture.get("league_id")
        league = str(LEAGUES.get(lid, "Ligue"))
        h = team_name(fixture, True)
        a = team_name(fixture, False)
        hid = get_team_id(fixture, True)
        aid = get_team_id(fixture, False)

        print("Pre-match: " + h + " vs " + a + " dans " + str(minutes_before) + "min", flush=True)

        h_scorers = get_top_scorers(fid, hid) if hid else []
        a_scorers = get_top_scorers(fid, aid) if aid else []
        h_recent = get_player_recent_form(fid, hid) if hid else {}
        a_recent = get_player_recent_form(fid, aid) if aid else {}
        h_form = get_team_form(hid) if hid else {}
        a_form = get_team_form(aid) if aid else {}

        try:
            msg = build_prematch_message(fixture, h, a, league, minutes_before, h_scorers, a_scorers, h_recent, a_recent, h_form, a_form)
            await bot.send_message(chat_id=str(TELEGRAM_CHAT_ID), text=msg)
            prematch_sent[str(fid)] = True
            print("Pre-match envoye: " + h + " vs " + a, flush=True)
            await asyncio.sleep(2)
        except Exception as e:
            print("ERREUR pre-match: " + str(e), flush=True)
            prematch_sent[str(fid)] = True

        if len(prematch_sent) > 200:
            keys = list(prematch_sent.keys())
            for k in keys[:100]:
                del prematch_sent[k]


async def run_forever():
    bot = Bot(token=TELEGRAM_TOKEN)
    print("BOUCLE DEMARREE - seuil=" + str(THRESHOLD), flush=True)

    while True:
        print("--- Check ---", flush=True)
        try:
            await send_prematch_alerts(bot)

            fixtures = get_fixtures()
            if not fixtures:
                print("Aucun match live dans nos ligues", flush=True)
            else:
                for f in fixtures:
                    fid = f.get("id")
                    minute = get_minute(f)
                    sc, son, sib, cor, dan, tot = momentum(f)
                    h = team_name(f, True)
                    a = team_name(f, False)
                    hg = get_goals(f, True)
                    ag = get_goals(f, False)
                    print("[" + str(minute) + "'] " + h + " " + str(hg) + "-" + str(ag) + " " + a + " -> " + str(sc), flush=True)
                    key = str(fid) + "_" + str(minute // 15)
                    if sc >= THRESHOLD and key not in alerts:
                        alerts[key] = True
                        hid = get_team_id(f, True)
                        aid = get_team_id(f, False)
                        h_form = get_team_form(hid) if hid else {}
                        a_form = get_team_form(aid) if aid else {}
                        try:
                            msg = build_live_message(f, sc, son, sib, cor, dan, tot, h_form, a_form)
                            await bot.send_message(chat_id=str(TELEGRAM_CHAT_ID), text=msg)
                            print("Alerte live: " + h + " vs " + a, flush=True)
                        except Exception as e:
                            print("ERREUR live msg: " + str(e), flush=True)
                        await asyncio.sleep(2)

                if len(alerts) > 500:
                    for k in list(alerts.keys())[:250]:
                        del alerts[k]

        except Exception as e:
            print("ERREUR BOUCLE: " + str(e), flush=True)

        print("Prochaine verif dans " + str(INTERVAL) + "s", flush=True)
        await asyncio.sleep(INTERVAL)


if __name__ == "__main__":
    asyncio.run(run_forever())
