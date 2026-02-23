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
INTERVAL = 45          # verifie toutes les 45s au lieu de 90s
PRE_MATCH_WINDOW = 30

logging.basicConfig(format="%(asctime)s %(message)s", level=logging.INFO, stream=sys.stdout)

URL = "https://api.sportmonks.com/v3/football"
alerts = {}
prematch_sent = {}
form_cache = {}

SEP = "\u2501" * 22


# ─────────────────────────────────────────
# HELPERS
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


def get_goals_by_team(fixture):
    """Retourne (buts_home, buts_away) depuis les scores"""
    hg, ag = 0, 0
    try:
        for s in fixture.get("scores", []):
            if s.get("description") == "CURRENT":
                sd = s.get("score", {})
                hg = int(sd.get("goals", 0) or 0)
                ag = int(sd.get("participant", 0) or 0)
    except Exception:
        pass
    return hg, ag


def get_minute(fixture):
    """Recupere la minute depuis state.clock — le plus fiable"""
    try:
        state = fixture.get("state", {})
        if isinstance(state, dict):
            clock = state.get("clock", {})
            if isinstance(clock, dict):
                mm = clock.get("mm")
                ss = clock.get("ss", 0)
                if mm is not None and int(mm) > 0:
                    return int(mm)
            # Fallback: state name
            sname = state.get("name", "")
            if "2nd" in sname or "HT" in sname:
                return 46
            if "1st" in sname:
                return 20
    except Exception:
        pass
    # Dernier recours: timestamp
    try:
        starting = fixture.get("starting_at_timestamp", 0)
        if starting:
            elapsed = int((time.time() - starting) / 60)
            if 1 <= elapsed <= 130:
                return elapsed
    except Exception:
        pass
    return 0


def stat_val_for_team(stats, code, tid):
    """Retourne la valeur d'une stat pour une equipe specifique"""
    try:
        for s in stats:
            t = s.get("type", {})
            tcode = t.get("code", "") if isinstance(t, dict) else ""
            if tcode == code and s.get("participant_id") == tid:
                v = s.get("data", {}).get("value", 0)
                return float(v) if v else 0.0
    except Exception:
        pass
    return 0.0


# ─────────────────────────────────────────
# FORME DES EQUIPES
# ─────────────────────────────────────────

def get_team_form(team_id):
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
            "unbeaten": 0, "wins": 0, "draws": 0, "losses": 0,
            "scored_1st_half": 0, "scored_2nd_half": 0,
            "scored_last_15": 0, "always_scored": True,
            "clean_sheets": 0, "total_matches": 0,
        }

        for match in matches:
            form["total_matches"] += 1
            scores = match.get("scores", [])
            events = match.get("events", [])
            is_home = False
            for p in match.get("participants", []):
                if p.get("id") == team_id:
                    if p.get("meta", {}).get("location") == "home":
                        is_home = True

            home_goals, away_goals = 0, 0
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
            if opp_g == 0:
                form["clean_sheets"] += 1

            goals_1h, goals_2h, goals_last15 = 0, 0, 0
            for ev in events:
                t = ev.get("type", {})
                code = t.get("code", "") if isinstance(t, dict) else ""
                if "goal" not in code.lower():
                    continue
                ev_team = ev.get("participant_id") or ev.get("team_id")
                if ev_team != team_id:
                    continue
                m = int(ev.get("minute", 0) or 0)
                if m <= 45:
                    goals_1h += 1
                else:
                    goals_2h += 1
                if m >= 75:
                    goals_last15 += 1

            if goals_1h > 0:
                form["scored_1st_half"] += 1
            if goals_2h > 0:
                form["scored_2nd_half"] += 1
            if goals_last15 > 0:
                form["scored_last_15"] += 1

        form_cache[cache_key] = (time.time(), form)
        return form
    except Exception as e:
        print("ERREUR form: " + str(e), flush=True)
        return {}


def build_form_insights(form, tname, current_goals, minute):
    insights = []
    n = form.get("total_matches", 0)
    if n == 0:
        return insights

    unbeaten = form.get("unbeaten", 0)
    wins = form.get("wins", 0)
    always_scored = form.get("always_scored", False)
    s1h = form.get("scored_1st_half", 0)
    s2h = form.get("scored_2nd_half", 0)
    sl15 = form.get("scored_last_15", 0)

    if unbeaten == n and n >= 4:
        insights.append("\U0001f525 " + tname + " invaincue sur " + str(n) + " matchs")
    elif wins >= 4:
        insights.append("\U0001f525 " + tname + " " + str(wins) + "V/" + str(n) + " matchs")

    if always_scored and n >= 4:
        insights.append("\u26bd " + tname + " a marque dans ses " + str(n) + " derniers matchs")

    if minute < 46 and s1h >= int(n * 0.6):
        insights.append("\u23f0 " + tname + " marque souvent en 1MT (" + str(s1h) + "/" + str(n) + ")")
    if minute >= 46 and s2h >= int(n * 0.6):
        insights.append("\u23f0 " + tname + " marque souvent en 2MT (" + str(s2h) + "/" + str(n) + ")")
    if minute >= 70 and sl15 >= int(n * 0.5):
        insights.append("\u23f0 " + tname + " marque souvent en fin de match (" + str(sl15) + "/" + str(n) + ")")
    if current_goals == 0 and always_scored and n >= 3:
        insights.append("\u26a0\ufe0f " + tname + " n'a pas marque - mais marque toujours!")

    return insights


# ─────────────────────────────────────────
# BUTEURS VIA TOPSCORERS
# ─────────────────────────────────────────

def get_top_scorers_for_team(team_id, season_id):
    """Recupere les top buteurs d'une equipe via l'endpoint topscorers"""
    try:
        r = requests.get(
            URL + "/topscorers/seasons/" + str(season_id),
            params={
                "api_token": SPORTMONKS_TOKEN,
                "include": "player;participant",
                "per_page": 50
            },
            timeout=15
        )
        if r.status_code != 200:
            return []
        d = r.json()
        all_scorers = d.get("data", [])
        team_scorers = []
        for s in all_scorers:
            if s.get("participant_id") != team_id:
                continue
            player = s.get("player", {})
            name = str(player.get("name", "") or player.get("display_name", ""))
            goals = int(s.get("total", 0) or 0)
            if name and goals >= 2:
                team_scorers.append({"name": name, "season_goals": goals})
        team_scorers.sort(key=lambda x: x["season_goals"], reverse=True)
        return team_scorers[:4]
    except Exception as e:
        print("ERREUR topscorers: " + str(e), flush=True)
        return []


def get_season_id(fixture):
    return fixture.get("season_id")


def get_player_recent_goals(team_id):
    """Joueurs ayant marque dans les 3 derniers matchs"""
    try:
        r = requests.get(
            URL + "/teams/" + str(team_id) + "/fixtures",
            params={
                "api_token": SPORTMONKS_TOKEN,
                "include": "events.type;events.player",
                "per_page": 3,
                "sort": "-starting_at"
            },
            timeout=15
        )
        if r.status_code != 200:
            return {}
        d = r.json()
        recent = d.get("data", [])
        scorer_count = {}
        for match in recent:
            scorers_this_match = set()
            for ev in match.get("events", []):
                t = ev.get("type", {})
                code = t.get("code", "") if isinstance(t, dict) else ""
                if "goal" not in code.lower() or "own" in code.lower():
                    continue
                ev_team = ev.get("participant_id") or ev.get("team_id")
                if ev_team != team_id:
                    continue
                pname = str(ev.get("player_name", "") or "")
                if not pname:
                    player = ev.get("player", {})
                    if isinstance(player, dict):
                        pname = str(player.get("name", "") or player.get("display_name", "") or "")
                if pname:
                    scorers_this_match.add(pname)
            for pname in scorers_this_match:
                scorer_count[pname] = scorer_count.get(pname, 0) + 1
        return scorer_count
    except Exception as e:
        print("ERREUR recent goals: " + str(e), flush=True)
        return {}


# ─────────────────────────────────────────
# FIXTURES
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
        print(str(len(all_f)) + " live, " + str(len(filtered)) + " dans nos ligues", flush=True)
        return filtered
    except Exception as e:
        print("ERREUR API: " + str(e), flush=True)
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
        upcoming = []
        for f in d.get("data", []):
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


# ─────────────────────────────────────────
# MOMENTUM
# ─────────────────────────────────────────

def momentum(fixture):
    score = 0
    stats = fixture.get("statistics", [])
    hid = get_team_id(fixture, True)
    aid = get_team_id(fixture, False)

    h_son = stat_val_for_team(stats, "shots-on-target", hid)
    a_son = stat_val_for_team(stats, "shots-on-target", aid)
    h_sib = stat_val_for_team(stats, "shots-insidebox", hid)
    a_sib = stat_val_for_team(stats, "shots-insidebox", aid)
    h_cor = stat_val_for_team(stats, "corners", hid)
    a_cor = stat_val_for_team(stats, "corners", aid)
    h_dan = stat_val_for_team(stats, "dangerous-attacks", hid)
    a_dan = stat_val_for_team(stats, "dangerous-attacks", aid)
    h_tot = stat_val_for_team(stats, "shots-total", hid)
    a_tot = stat_val_for_team(stats, "shots-total", aid)

    son = h_son + a_son
    sib = h_sib + a_sib
    cor = h_cor + a_cor
    dan = h_dan + a_dan
    tot = h_tot + a_tot

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

    if dan >= 40:
        score += 20
    elif dan >= 25:
        score += 13
    elif dan >= 12:
        score += 6

    if tot > 0 and son / tot >= 0.5:
        score += 10

    return (min(score, 100),
            h_son, a_son, h_sib, a_sib,
            h_cor, a_cor, h_dan, a_dan)


def get_dominant(h_dan, a_dan, h_son, a_son, h_cor, a_cor, hname, aname):
    h_total = h_dan + (h_son * 3) + h_cor
    a_total = a_dan + (a_son * 3) + a_cor
    if h_total > a_total * 1.4:
        return hname, "home"
    elif a_total > h_total * 1.4:
        return aname, "away"
    return None, "balanced"


# ─────────────────────────────────────────
# MESSAGES
# ─────────────────────────────────────────

def build_live_message(fixture, score,
                       h_son, a_son, h_sib, a_sib,
                       h_cor, a_cor, h_dan, a_dan,
                       h_form, a_form):
    minute = get_minute(fixture)
    lid = fixture.get("league_id")
    league = str(LEAGUES.get(lid, "Ligue"))
    h = team_name(fixture, True)
    a = team_name(fixture, False)
    hg, ag = get_goals_by_team(fixture)
    total_goals = hg + ag

    dominant_team, dominant_side = get_dominant(h_dan, a_dan, h_son, a_son, h_cor, a_cor, h, a)

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

    # Stats separees par equipe
    def team_stat_line(tname, son, sib, cor, dan):
        lines = []
        if son >= 1:
            lines.append(str(int(son)) + " tirs cadres")
        if sib >= 1:
            lines.append(str(int(sib)) + " tirs surface")
        if cor >= 1:
            lines.append(str(int(cor)) + " corners")
        if dan >= 1:
            lines.append(str(int(dan)) + " att. dang.")
        if lines:
            return "  \U0001f539 " + tname + ": " + " | ".join(lines)
        return ""

    h_stat = team_stat_line(h, h_son, h_sib, h_cor, h_dan)
    a_stat = team_stat_line(a, a_son, a_sib, a_cor, a_dan)
    stats_text = ""
    if h_stat:
        stats_text += h_stat + "\n"
    if a_stat:
        stats_text += a_stat
    if not stats_text:
        stats_text = "  \u2022 Stats en cours"

    # Forme
    form_lines = []
    h_insights = build_form_insights(h_form, h, hg, minute)
    a_insights = build_form_insights(a_form, a, ag, minute)
    form_lines.extend(h_insights)
    form_lines.extend(a_insights)
    form_text = "\n".join(["  " + l for l in form_lines]) if form_lines else ""

    # Recommandations (buts uniquement)
    recs = []

    if score >= 55 and (h_son + a_son >= 6 or h_sib + a_sib >= 6):
        if dominant_side == "home":
            recs.append("  \u2192 \u26bd Prochain but: " + h + " (domine)")
        elif dominant_side == "away":
            recs.append("  \u2192 \u26bd Prochain but: " + a + " (domine)")
        else:
            recs.append("  \u2192 \u26bd Prochain but: Match ouvert")

    if score >= 55 and (h_son + a_son >= 5 or h_sib + a_sib >= 5):
        recs.append("  \u2192 \U0001f4c8 Over " + str(total_goals) + ".5 buts dans le match")
        if minute < 46:
            recs.append("  \u2192 \U0001f4c8 Over 0.5 buts reste 1ere MT")
        else:
            recs.append("  \u2192 \U0001f4c8 Over 0.5 buts reste 2eme MT")

    if hg == 0 and ag == 0 and score >= 55 and minute >= 30:
        recs.append("  \u2192 \U0001f3af BTTS possible - 0-0 sous forte pression")
    elif total_goals > 0 and (hg == 0 or ag == 0) and score >= 55:
        recs.append("  \u2192 \U0001f3af BTTS possible - equipe a 0 sous pression")

    if not recs:
        recs.append("  \u2192 \U0001f440 A surveiller - pression en hausse")

    recs_text = "\n".join(recs)

    form_section = ""
    if form_text:
        form_section = SEP + "\n\U0001f4ca FORME:\n" + form_text + "\n"

    msg = (
        emoji + " " + lvl + " - BUT POTENTIEL\n"
        + SEP + "\n"
        + "\U0001f3c6 " + league + "\n"
        + "\u2694\ufe0f  " + h + " " + str(hg) + " - " + str(ag) + " " + a + "\n"
        + "\u23f1\ufe0f  " + str(minute) + "' | Momentum: " + str(score) + "/100\n"
        + gauge + "\n"
        + SEP + "\n"
        + "\U0001f4ca STATS:\n"
        + stats_text + "\n"
        + form_section
        + SEP + "\n"
        + "\U0001f4a1 QUOI JOUER:\n"
        + recs_text + "\n"
        + SEP + "\n"
        + "\u26a0\ufe0f Parie de facon responsable"
    )
    return msg


def build_prematch_message(fixture, h, a, league, minutes_before,
                           h_scorers, a_scorers,
                           h_recent, a_recent,
                           h_form, a_form):
    def scorer_lines(scorers, recent):
        lines = []
        for s in scorers:
            name = s["name"]
            goals = s["season_goals"]
            line = "  \u2022 " + name + " - " + str(goals) + " buts"
            r = recent.get(name, 0)
            if r >= 2:
                line += "\n    \u2705 En feu! " + str(r) + " buts sur 3 derniers matchs"
            elif r == 1:
                line += "\n    \u2705 A marque au dernier match"
            lines.append(line)
        return "\n".join(lines) if lines else "  \u2022 Aucun buteur notable"

    def form_lines_for(form, tname):
        lines = []
        n = form.get("total_matches", 0)
        if n == 0:
            return "  \u2022 Forme non disponible"
        if form.get("unbeaten", 0) == n:
            lines.append("  \U0001f7e2 Invaincue sur " + str(n) + " matchs")
        if form.get("always_scored", False):
            lines.append("  \u26bd Marque dans ses " + str(n) + " derniers matchs")
        s1h = form.get("scored_1st_half", 0)
        if s1h >= int(n * 0.6):
            lines.append("  \u23f0 Marque souvent en 1MT (" + str(s1h) + "/" + str(n) + ")")
        sl15 = form.get("scored_last_15", 0)
        if sl15 >= int(n * 0.5):
            lines.append("  \u23f0 Marque souvent en fin de match (" + str(sl15) + "/" + str(n) + ")")
        return "\n".join(lines) if lines else "  \u2022 Forme correcte"

    h_sc = scorer_lines(h_scorers, h_recent)
    a_sc = scorer_lines(a_scorers, a_recent)
    h_fm = form_lines_for(h_form, h)
    a_fm = form_lines_for(a_form, a)

    # Recommandations pre-match
    recs = []
    top_all = []
    for s in h_scorers:
        r = h_recent.get(s["name"], 0)
        top_all.append((s["name"], s["season_goals"], r))
    for s in a_scorers:
        r = a_recent.get(s["name"], 0)
        top_all.append((s["name"], s["season_goals"], r))
    top_all.sort(key=lambda x: (x[2] * 10 + x[1]), reverse=True)

    for name, goals, recent in top_all[:3]:
        if recent >= 1 and goals >= 4:
            recs.append("  \u2192 \u26bd Anytime scorer: " + name + " (forme + volume)")
        elif recent >= 2:
            recs.append("  \u2192 \u26bd Anytime scorer: " + name + " (en feu)")
        elif goals >= 6:
            recs.append("  \u2192 \u26bd Anytime scorer: " + name + " (top buteur)")

    if h_form.get("always_scored") and a_form.get("always_scored"):
        recs.append("  \u2192 \U0001f3af BTTS probable - les deux marquent toujours")

    if not recs:
        recs.append("  \u2192 \U0001f440 Surveiller les buteurs en forme")

    msg = (
        "\u26bd PRE-MATCH - BUTEURS A SURVEILLER\n"
        + SEP + "\n"
        + "\U0001f3c6 " + league + "\n"
        + "\u2694\ufe0f " + h + " vs " + a + "\n"
        + "\u23f1\ufe0f Coup d'envoi dans ~" + str(minutes_before) + " min\n"
        + SEP + "\n"
        + "\U0001f534 " + h + ":\n"
        + h_sc + "\n"
        + "\U0001f4ca Forme:\n"
        + h_fm + "\n"
        + SEP + "\n"
        + "\U0001f535 " + a + ":\n"
        + a_sc + "\n"
        + "\U0001f4ca Forme:\n"
        + a_fm + "\n"
        + SEP + "\n"
        + "\U0001f4a1 QUOI JOUER:\n"
        + "\n".join(recs) + "\n"
        + SEP + "\n"
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
        season_id = get_season_id(fixture)

        print("Pre-match: " + h + " vs " + a + " dans " + str(minutes_before) + "min", flush=True)

        h_scorers = get_top_scorers_for_team(hid, season_id) if (hid and season_id) else []
        a_scorers = get_top_scorers_for_team(aid, season_id) if (aid and season_id) else []
        h_recent = get_player_recent_goals(hid) if hid else {}
        a_recent = get_player_recent_goals(aid) if aid else {}
        h_form = get_team_form(hid) if hid else {}
        a_form = get_team_form(aid) if aid else {}

        try:
            msg = build_prematch_message(fixture, h, a, league, minutes_before,
                                         h_scorers, a_scorers,
                                         h_recent, a_recent,
                                         h_form, a_form)
            await bot.send_message(chat_id=str(TELEGRAM_CHAT_ID), text=msg)
            prematch_sent[str(fid)] = True
            print("Pre-match envoye: " + h + " vs " + a, flush=True)
            await asyncio.sleep(2)
        except Exception as e:
            print("ERREUR pre-match: " + str(e), flush=True)
            prematch_sent[str(fid)] = True

        if len(prematch_sent) > 200:
            for k in list(prematch_sent.keys())[:100]:
                del prematch_sent[k]


async def run_forever():
    bot = Bot(token=TELEGRAM_TOKEN)
    print("BOUCLE DEMARREE - seuil=" + str(THRESHOLD) + " interval=" + str(INTERVAL) + "s", flush=True)

    while True:
        print("--- Check ---", flush=True)
        try:
            await send_prematch_alerts(bot)

            fixtures = get_fixtures()
            if not fixtures:
                print("Aucun match live", flush=True)
            else:
                for f in fixtures:
                    fid = f.get("id")
                    minute = get_minute(f)
                    result = momentum(f)
                    sc = result[0]
                    h_son, a_son = result[1], result[2]
                    h_sib, a_sib = result[3], result[4]
                    h_cor, a_cor = result[5], result[6]
                    h_dan, a_dan = result[7], result[8]

                    h = team_name(f, True)
                    a = team_name(f, False)
                    hg, ag = get_goals_by_team(f)

                    print("[" + str(minute) + "'] " + h + " " + str(hg) + "-" + str(ag) + " " + a + " -> " + str(sc), flush=True)

                    key = str(fid) + "_" + str(minute // 15)
                    if sc >= THRESHOLD and key not in alerts:
                        alerts[key] = True
                        hid = get_team_id(f, True)
                        aid = get_team_id(f, False)
                        h_form = get_team_form(hid) if hid else {}
                        a_form = get_team_form(aid) if aid else {}
                        try:
                            msg = build_live_message(f, sc,
                                                     h_son, a_son,
                                                     h_sib, a_sib,
                                                     h_cor, a_cor,
                                                     h_dan, a_dan,
                                                     h_form, a_form)
                            await bot.send_message(chat_id=str(TELEGRAM_CHAT_ID), text=msg)
                            print("Alerte envoyee: " + h + " vs " + a + " [" + str(minute) + "'] score=" + str(sc), flush=True)
                        except Exception as e:
                            print("ERREUR msg: " + str(e), flush=True)
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
