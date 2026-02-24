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

INTERVAL = 45
PRE_MATCH_WINDOW = 30

logging.basicConfig(format="%(asctime)s %(message)s", level=logging.INFO, stream=sys.stdout)

URL = "https://api.sportmonks.com/v3/football"
alerts = {}
prematch_sent = {}
form_cache = {}
odds_cache = {}

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
    hg, ag = 0, 0
    try:
        # Essaie CURRENT en premier
        for s in fixture.get("scores", []):
            if s.get("description") == "CURRENT":
                sd = s.get("score", {})
                hg = int(sd.get("goals", 0) or 0)
                ag = int(sd.get("participant", 0) or 0)
                return hg, ag
        # Fallback sur autres descriptions live
        for s in fixture.get("scores", []):
            desc = s.get("description", "")
            if desc in ("LIVE", "2ND_HALF", "1ST_HALF", "HT"):
                sd = s.get("score", {})
                hg = int(sd.get("goals", 0) or 0)
                ag = int(sd.get("participant", 0) or 0)
                return hg, ag
    except Exception:
        pass
    return hg, ag


def get_minute(fixture):
    """
    Priorite: timestamp reel (toujours juste) > state.clock > state.name
    state.clock reste bloque a 46 en debut de 2eme MT sur SportMonks
    """
    # Methode 1: timestamp (la plus fiable)
    try:
        starting = fixture.get("starting_at_timestamp", 0)
        if starting and starting > 0:
            elapsed = int((time.time() - starting) / 60)
            if 1 <= elapsed <= 130:
                return elapsed
    except Exception:
        pass

    # Methode 2: state.clock uniquement si > 46
    try:
        state = fixture.get("state", {})
        if isinstance(state, dict):
            clock = state.get("clock", {})
            if isinstance(clock, dict):
                mm = clock.get("mm")
                if mm is not None and int(mm) > 46:
                    return int(mm)
    except Exception:
        pass

    # Methode 3: nom du state
    try:
        state = fixture.get("state", {})
        if isinstance(state, dict):
            sname = str(state.get("name", "")).lower()
            if "2nd" in sname:
                return 55
            if "ht" in sname or "half" in sname:
                return 45
            if "1st" in sname:
                return 25
    except Exception:
        pass

    return 0


def stat_val_for_team(stats, code, tid):
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


def get_season_id(fixture):
    return fixture.get("season_id")


# ─────────────────────────────────────────
# SEUIL INTELLIGENT SELON CONTEXTE
# ─────────────────────────────────────────

def get_smart_threshold(minute, hg, ag, h_eff, a_eff):
    """
    Adapte le seuil selon le contexte du match.
    Plus le contexte est favorable a un but, plus le seuil est bas.

    Logique :
    - Fin de match (75+) : seuil bas car les buts arrivent souvent
    - Equipe qui pousse pour egaliser : seuil bas
    - Equipe inefficace : seuil plus haut pour eviter les fausses alertes
    - En debut de match : seuil plus haut car les stats sont trop faibles
    """
    base = 48  # seuil calibre

    # Ajustement par minute
    if minute >= 80:
        base -= 12   # fin de match = beaucoup de buts
    elif minute >= 70:
        base -= 8
    elif minute >= 55:
        base -= 4
    elif minute < 15:
        base += 8

    # Equipe qui cherche a egaliser ou renverser
    total = hg + ag
    if abs(hg - ag) == 1 and minute >= 60:
        base -= 6    # equipe qui perd cherche egalisation
    elif abs(hg - ag) >= 2:
        base += 5    # match plie, moins d'interet

    # Penalite si equipe dominante est inefficace
    dom_eff = h_eff if hg <= ag else a_eff
    if dom_eff == "inefficace":
        base += 6

    return max(38, min(base, 70))


# ─────────────────────────────────────────
# MOMENTUM RECENT 10 MINUTES
# ─────────────────────────────────────────

def get_recent_momentum(events, hid, aid, current_minute):
    window = 10
    min_start = max(0, current_minute - window)
    h_score, a_score = 0, 0
    h_events, a_events = [], []

    for ev in events:
        try:
            m = int(ev.get("minute", 0) or 0)
            if m < min_start:
                continue
            t = ev.get("type", {})
            code = t.get("code", "") if isinstance(t, dict) else ""
            ev_team = ev.get("participant_id") or ev.get("team_id")

            points = 0
            label = ""
            if "shot-on-target" in code or "shots-on-target" in code:
                points = 3
                label = "tir cadre"
            elif "shot" in code and "off" not in code and "block" not in code:
                points = 1
                label = "tir"
            elif "corner" in code:
                points = 2
                label = "corner"
            elif "dangerous" in code:
                points = 1
                label = "att.dang"
            elif "goal" in code and "own" not in code:
                points = 5
                label = "BUT"

            if points > 0 and label:
                if ev_team == hid:
                    h_score += points
                    h_events.append(str(m) + "' " + label)
                elif ev_team == aid:
                    a_score += points
                    a_events.append(str(m) + "' " + label)
        except Exception:
            continue

    return h_score, a_score, h_events[-4:], a_events[-4:]


# ─────────────────────────────────────────
# IN PLAY ODDS
# ─────────────────────────────────────────

def get_inplay_odds(fixture_id):
    cache_key = str(fixture_id)
    if cache_key in odds_cache:
        cached_time, cached_data = odds_cache[cache_key]
        if time.time() - cached_time < 60:
            return cached_data
    try:
        r = requests.get(
            URL + "/odds/inplay/fixtures/" + str(fixture_id),
            params={"api_token": SPORTMONKS_TOKEN, "per_page": 50},
            timeout=10
        )
        if r.status_code != 200:
            return {}
        d = r.json()
        result = {}
        for odd in d.get("data", []):
            market = str(odd.get("market_description", "") or odd.get("name", "")).lower()
            selections = odd.get("selections", []) or odd.get("odds", [])
            if "next goal" in market:
                for sel in selections:
                    label = str(sel.get("label", "") or sel.get("name", "")).lower()
                    val = sel.get("value") or sel.get("odd")
                    if val:
                        if "no goal" in label:
                            result["no_goal_odd"] = float(val)
                        elif "home" in label:
                            result["home_goal_odd"] = float(val)
                        elif "away" in label:
                            result["away_goal_odd"] = float(val)
            elif "over/under" in market or "total goals" in market:
                for sel in selections:
                    label = str(sel.get("label", "") or sel.get("name", "")).lower()
                    val = sel.get("value") or sel.get("odd")
                    if val and "over" in label:
                        if "0.5" in label:
                            result["over_05_odd"] = float(val)
                        elif "1.5" in label:
                            result["over_15_odd"] = float(val)
        odds_cache[cache_key] = (time.time(), result)
        return result
    except Exception as e:
        print("ERREUR odds: " + str(e), flush=True)
        return {}


def format_odds_insight(odds, hg, ag, dominant_side, h, a):
    if not odds:
        return ""
    insights = []
    total = hg + ag
    no_goal = odds.get("no_goal_odd", 0)
    home_goal = odds.get("home_goal_odd", 0)
    away_goal = odds.get("away_goal_odd", 0)
    over_05 = odds.get("over_05_odd", 0)
    over_15 = odds.get("over_15_odd", 0)

    if no_goal and no_goal < 1.5:
        insights.append("\U0001f4b9 Marche: but peu probable (no goal " + str(round(no_goal, 2)) + ")")
    elif no_goal and no_goal > 2.5:
        insights.append("\U0001f4b9 Marche: but probable (no goal " + str(round(no_goal, 2)) + ")")
    if home_goal and away_goal:
        if home_goal < away_goal and dominant_side == "home":
            insights.append("\U0001f4b9 Cote: " + h + " marque ensuite (" + str(round(home_goal, 2)) + ")")
        elif away_goal < home_goal and dominant_side == "away":
            insights.append("\U0001f4b9 Cote: " + a + " marque ensuite (" + str(round(away_goal, 2)) + ")")
    if over_05 and over_05 < 1.4:
        insights.append("\U0001f4b9 Cote: Over " + str(total) + ".5 tres probable (" + str(round(over_05, 2)) + ")")
    if over_15 and over_15 < 1.6:
        insights.append("\U0001f4b9 Cote: Over " + str(total + 1) + ".5 favori (" + str(round(over_15, 2)) + ")")
    return "\n".join(insights)


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
        matches = r.json().get("data", [])
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
            is_home = False
            for p in match.get("participants", []):
                if p.get("id") == team_id and p.get("meta", {}).get("location") == "home":
                    is_home = True

            home_goals, away_goals = 0, 0
            for s in match.get("scores", []):
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

            g1h, g2h, g_last15 = 0, 0, 0
            for ev in match.get("events", []):
                t = ev.get("type", {})
                code = t.get("code", "") if isinstance(t, dict) else ""
                if "goal" not in code.lower():
                    continue
                if (ev.get("participant_id") or ev.get("team_id")) != team_id:
                    continue
                m = int(ev.get("minute", 0) or 0)
                if m <= 45:
                    g1h += 1
                else:
                    g2h += 1
                if m >= 75:
                    g_last15 += 1

            if g1h > 0:
                form["scored_1st_half"] += 1
            if g2h > 0:
                form["scored_2nd_half"] += 1
            if g_last15 > 0:
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
    if form.get("unbeaten", 0) == n and n >= 4:
        insights.append("\U0001f525 " + tname + " invaincue sur " + str(n) + " matchs")
    elif form.get("wins", 0) >= 4:
        insights.append("\U0001f525 " + tname + " " + str(form["wins"]) + "V/" + str(n))
    if form.get("always_scored", False) and n >= 4:
        insights.append("\u26bd " + tname + " a marque dans ses " + str(n) + " derniers matchs")
    s1h = form.get("scored_1st_half", 0)
    s2h = form.get("scored_2nd_half", 0)
    sl15 = form.get("scored_last_15", 0)
    if minute < 46 and s1h >= int(n * 0.6):
        insights.append("\u23f0 " + tname + " marque souvent en 1MT (" + str(s1h) + "/" + str(n) + ")")
    if minute >= 46 and s2h >= int(n * 0.6):
        insights.append("\u23f0 " + tname + " marque souvent en 2MT (" + str(s2h) + "/" + str(n) + ")")
    if minute >= 70 and sl15 >= int(n * 0.5):
        insights.append("\u23f0 " + tname + " marque souvent en fin de match (" + str(sl15) + "/" + str(n) + ")")
    if current_goals == 0 and form.get("always_scored", False) and n >= 3:
        insights.append("\u26a0\ufe0f " + tname + " n'a pas marque - mais marque toujours!")
    return insights


# ─────────────────────────────────────────
# BUTEURS
# ─────────────────────────────────────────

def get_top_scorers_for_team(team_id, season_id):
    """
    Essaie plusieurs endpoints pour trouver les buteurs.
    1. topscorers/seasons/{season_id} si season_id dispo
    2. players via squads avec stats
    3. events des derniers matchs comme fallback
    """
    scorers = []

    # Methode 1: topscorers par saison
    if season_id:
        try:
            r = requests.get(
                URL + "/topscorers/seasons/" + str(season_id),
                params={
                    "api_token": SPORTMONKS_TOKEN,
                    "include": "player",
                    "per_page": 100
                },
                timeout=15
            )
            if r.status_code == 200:
                for s in r.json().get("data", []):
                    if s.get("participant_id") != team_id:
                        continue
                    player = s.get("player", {})
                    name = str(player.get("name", "") or player.get("display_name", ""))
                    goals = int(s.get("total", 0) or 0)
                    if name and goals >= 2:
                        scorers.append({"name": name, "season_goals": goals})
                if scorers:
                    scorers.sort(key=lambda x: x["season_goals"], reverse=True)
                    print("Buteurs via topscorers: " + str(len(scorers)), flush=True)
                    return scorers[:4]
        except Exception as e:
            print("ERREUR topscorers method1: " + str(e), flush=True)

    # Methode 2: squad avec statistiques joueurs
    try:
        r = requests.get(
            URL + "/teams/" + str(team_id) + "/players",
            params={
                "api_token": SPORTMONKS_TOKEN,
                "include": "statistics.details.type",
                "per_page": 30
            },
            timeout=15
        )
        if r.status_code == 200:
            for p in r.json().get("data", []):
                player = p.get("player", p)
                name = str(player.get("name", "") or player.get("display_name", ""))
                if not name:
                    name = str(p.get("name", "") or p.get("display_name", ""))
                goals = 0
                for stat in p.get("statistics", []):
                    for detail in stat.get("details", []):
                        t = detail.get("type", {})
                        if isinstance(t, dict) and t.get("code") in ("goals", "goals-scored"):
                            val = detail.get("data", {}).get("value", 0)
                            goals += int(val) if val else 0
                if name and goals >= 2:
                    scorers.append({"name": name, "season_goals": goals})
            if scorers:
                scorers.sort(key=lambda x: x["season_goals"], reverse=True)
                print("Buteurs via squad stats: " + str(len(scorers)), flush=True)
                return scorers[:4]
    except Exception as e:
        print("ERREUR topscorers method2: " + str(e), flush=True)

    # Methode 3: events des 10 derniers matchs pour trouver buteurs recurrents
    try:
        r = requests.get(
            URL + "/teams/" + str(team_id) + "/fixtures",
            params={
                "api_token": SPORTMONKS_TOKEN,
                "include": "events.type;events.player",
                "per_page": 10,
                "sort": "-starting_at"
            },
            timeout=15
        )
        if r.status_code == 200:
            goal_count = {}
            for match in r.json().get("data", []):
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
                        pl = ev.get("player", {})
                        if isinstance(pl, dict):
                            pname = str(pl.get("name", "") or pl.get("display_name", "") or "")
                    if pname:
                        goal_count[pname] = goal_count.get(pname, 0) + 1

            for name, goals in goal_count.items():
                if goals >= 1:
                    scorers.append({"name": name, "season_goals": goals})
            if scorers:
                scorers.sort(key=lambda x: x["season_goals"], reverse=True)
                print("Buteurs via events: " + str(len(scorers)), flush=True)
                return scorers[:4]
    except Exception as e:
        print("ERREUR topscorers method3: " + str(e), flush=True)

    print("Aucun buteur trouve pour team_id=" + str(team_id), flush=True)
    return []


def get_player_recent_goals(team_id):
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
        scorer_count = {}
        for match in r.json().get("data", []):
            scorers_this_match = set()
            for ev in match.get("events", []):
                t = ev.get("type", {})
                code = t.get("code", "") if isinstance(t, dict) else ""
                if "goal" not in code.lower() or "own" in code.lower():
                    continue
                if (ev.get("participant_id") or ev.get("team_id")) != team_id:
                    continue
                pname = str(ev.get("player_name", "") or "")
                if not pname:
                    pl = ev.get("player", {})
                    if isinstance(pl, dict):
                        pname = str(pl.get("name", "") or pl.get("display_name", "") or "")
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

def get_fixture_details(fixture_id):
    """Recupere les stats et events d'un match specifique via son ID"""
    try:
        r = requests.get(
            URL + "/fixtures/" + str(fixture_id),
            params={
                "api_token": SPORTMONKS_TOKEN,
                "include": "statistics.type;events.type"
            },
            timeout=15
        )
        if r.status_code == 200:
            data = r.json().get("data", {})
            return (data.get("statistics", []),
                    data.get("events", []))
    except Exception as e:
        print("ERREUR fixture details: " + str(e), flush=True)
    return [], []


def get_fixtures():
    try:
        # Etape 1: recupere les matchs live avec infos de base
        r = requests.get(
            URL + "/livescores/inplay",
            params={
                "api_token": SPORTMONKS_TOKEN,
                "include": "participants;scores;state",
                "per_page": 50
            },
            timeout=15
        )
        print("API status: " + str(r.status_code), flush=True)
        if r.status_code != 200:
            return []
        all_f = r.json().get("data", [])
        filtered = [f for f in all_f if f.get("league_id") in LEAGUES]
        print(str(len(all_f)) + " live, " + str(len(filtered)) + " dans nos ligues", flush=True)

        # Etape 2: pour chaque match dans nos ligues, recupere stats + events separement
        enriched = []
        for f in filtered:
            fid = f.get("id")
            stats, events = get_fixture_details(fid)
            f["statistics"] = stats
            f["events"] = events
            if stats:
                print("Stats OK pour " + str(fid) + ": " + str(len(stats)) + " entrees", flush=True)
            else:
                print("Stats VIDES pour " + str(fid), flush=True)
            enriched.append(f)

        return enriched
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
        upcoming = []
        for f in r.json().get("data", []):
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

def calc_efficiency(son, tot):
    if tot < 3:
        return 0.0, "neutre", 0
    ratio = son / tot
    if ratio >= 0.50:
        return ratio, "efficace", 10
    elif ratio >= 0.30:
        return ratio, "neutre", 0
    else:
        return ratio, "inefficace", -8


def momentum(fixture):
    stats = fixture.get("statistics", [])
    events = fixture.get("events", [])
    hid = get_team_id(fixture, True)
    aid = get_team_id(fixture, False)
    minute = get_minute(fixture)

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
    h_xg = stat_val_for_team(stats, "expected-goals", hid)
    a_xg = stat_val_for_team(stats, "expected-goals", aid)

    son = h_son + a_son
    sib = h_sib + a_sib
    cor = h_cor + a_cor
    dan = h_dan + a_dan
    tot = h_tot + a_tot

    score = 0

    # Tirs cadres (signal le plus fiable)
    if son >= 10:
        score += 22
    elif son >= 6:
        score += 14
    elif son >= 3:
        score += 7

    # Tirs dans la surface (2eme signal le plus fiable)
    if sib >= 12:
        score += 20
    elif sib >= 7:
        score += 13
    elif sib >= 4:
        score += 6

    # Corners (signal modere)
    if cor >= 10:
        score += 10
    elif cor >= 6:
        score += 6
    elif cor >= 3:
        score += 3

    # Attaques dangereuses (signal faible seul)
    if dan >= 40:
        score += 8
    elif dan >= 25:
        score += 5
    elif dan >= 12:
        score += 2

    # xG si disponible (signal tres fiable)
    xg_total = h_xg + a_xg
    xg_available = xg_total > 0
    if xg_available:
        if xg_total >= 3.0:
            score += 22
        elif xg_total >= 2.0:
            score += 16
        elif xg_total >= 1.0:
            score += 10
        elif xg_total >= 0.5:
            score += 5

    # Efficacite
    h_ratio, h_eff, h_bonus = calc_efficiency(h_son, h_tot)
    a_ratio, a_eff, a_bonus = calc_efficiency(a_son, a_tot)
    if h_tot >= a_tot:
        score += h_bonus
    else:
        score += a_bonus

    # Momentum 10 dernieres minutes (signal le plus important - recent)
    h_rec, a_rec, h_rev, a_rev = get_recent_momentum(events, hid, aid, minute)
    recent_total = h_rec + a_rec
    if recent_total >= 12:
        score += 18
    elif recent_total >= 7:
        score += 12
    elif recent_total >= 3:
        score += 6

    final_score = min(max(score, 0), 100)

    return (final_score,
            h_son, a_son, h_sib, a_sib,
            h_cor, a_cor, h_dan, a_dan,
            h_tot, a_tot,
            h_eff, a_eff, h_ratio, a_ratio,
            h_xg, a_xg, xg_available,
            h_rec, a_rec, h_rev, a_rev)


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

def eff_emoji(label):
    if label == "efficace":
        return " \U0001f3af"
    elif label == "inefficace":
        return " \U0001f4a8"
    return ""


def build_live_message(fixture, score,
                       h_son, a_son, h_sib, a_sib,
                       h_cor, a_cor, h_dan, a_dan,
                       h_tot, a_tot, h_eff, a_eff,
                       h_ratio, a_ratio,
                       h_xg, a_xg, xg_available,
                       h_rec, a_rec, h_rev, a_rev,
                       h_form, a_form, odds, threshold):

    minute = get_minute(fixture)
    lid = fixture.get("league_id")
    league = str(LEAGUES.get(lid, "Ligue"))
    h = team_name(fixture, True)
    a = team_name(fixture, False)
    hg, ag = get_goals_by_team(fixture)
    total_goals = hg + ag

    dominant_team, dominant_side = get_dominant(h_dan, a_dan, h_son, a_son, h_cor, a_cor, h, a)

    gauge = "\U0001f7e9" * int(score / 10) + "\u2b1c" * (10 - int(score / 10))

    # Niveau d'alerte avec marge au dessus du seuil
    margin = score - threshold
    if margin >= 20 or score >= 75:
        lvl = "ALERTE MAX"
        emoji = "\U0001f534"
    elif margin >= 10 or score >= 60:
        lvl = "FORTE PRESSION"
        emoji = "\U0001f7e0"
    else:
        lvl = "PRESSION"
        emoji = "\U0001f7e1"

    # Stats par equipe avec precision et xG
    def stat_line(tname, son, sib, cor, dan, tot, xg, eff, ratio):
        parts = []
        if son >= 1:
            parts.append(str(int(son)) + " tirs cadres")
        if sib >= 1:
            parts.append(str(int(sib)) + " tirs surface")
        if cor >= 1:
            parts.append(str(int(cor)) + " corners")
        if dan >= 1:
            parts.append(str(int(dan)) + " att.dang.")
        pct = str(int(ratio * 100)) + "%" if tot >= 3 else "?"
        eff_str = eff_emoji(eff)
        xg_str = " | xG: " + str(round(xg, 2)) if xg > 0 else ""
        if parts:
            return ("  \U0001f539 " + tname + ":\n"
                    + "    " + " | ".join(parts) + "\n"
                    + "    precision: " + pct + eff_str + xg_str)
        return ""

    h_line = stat_line(h, h_son, h_sib, h_cor, h_dan, h_tot, h_xg, h_eff, h_ratio)
    a_line = stat_line(a, a_son, a_sib, a_cor, a_dan, a_tot, a_xg, a_eff, a_ratio)
    stats_text = ""
    if h_line:
        stats_text += h_line + "\n"
    if a_line:
        stats_text += a_line
    if not stats_text:
        stats_text = "  \u2022 Stats en cours"

    # Momentum recent
    recent_text = ""
    if h_rev or a_rev:
        recent_text = SEP + "\n\u26a1 MOMENTUM 10 DERNIERES MIN:\n"
        if h_rev:
            recent_text += "  \U0001f539 " + h + ": " + ", ".join(h_rev) + "\n"
        if a_rev:
            recent_text += "  \U0001f539 " + a + ": " + ", ".join(a_rev) + "\n"
        if h_rec > a_rec * 1.5 and h_rec > 0:
            recent_text += "  \u27a1\ufe0f " + h + " domine ces 10 dernieres min\n"
        elif a_rec > h_rec * 1.5 and a_rec > 0:
            recent_text += "  \u27a1\ufe0f " + a + " domine ces 10 dernieres min\n"
        else:
            recent_text += "  \u27a1\ufe0f Pression equilibree\n"

    # Forme
    form_lines = []
    form_lines.extend(build_form_insights(h_form, h, hg, minute))
    form_lines.extend(build_form_insights(a_form, a, ag, minute))
    form_text = "\n".join(["  " + l for l in form_lines]) if form_lines else ""

    # Cotes
    odds_text = format_odds_insight(odds, hg, ag, dominant_side, h, a)

    # Recommandations buts uniquement
    # Equipe dominante = celle qui domine les 10 dernieres min en priorite
    if h_rev or a_rev:
        rec_dominant = h if h_rec >= a_rec else a
        rec_dominant_side = "home" if h_rec >= a_rec else "away"
        rec_dom_eff = h_eff if rec_dominant_side == "home" else a_eff
    else:
        rec_dominant = dominant_team
        rec_dominant_side = dominant_side
        rec_dom_eff = h_eff if dominant_side == "home" else a_eff

    recs = []

    if h_son + a_son >= 5 or h_sib + a_sib >= 5:
        if rec_dominant_side in ("home", "away"):
            if rec_dom_eff == "inefficace":
                recs.append("  \u2192 \u26bd " + str(rec_dominant) + " domine mais imprecise")
            else:
                recs.append("  \u2192 \u26bd Prochain but: " + str(rec_dominant) + eff_emoji(rec_dom_eff))
        else:
            recs.append("  \u2192 \u26bd Prochain but: Match ouvert")

    if h_son + a_son >= 5 or h_sib + a_sib >= 5:
        if rec_dom_eff != "inefficace":
            recs.append("  \u2192 \U0001f4c8 Over " + str(total_goals) + ".5 buts dans le match")
            if minute < 46:
                recs.append("  \u2192 \U0001f4c8 Over 0.5 buts reste 1ere MT")
            else:
                recs.append("  \u2192 \U0001f4c8 Over 0.5 buts reste 2eme MT")
        else:
            recs.append("  \u2192 \U0001f4c8 Over " + str(total_goals) + ".5 possible mais equipe imprecise")

    if hg == 0 and ag == 0 and minute >= 30:
        recs.append("  \u2192 \U0001f3af BTTS possible - 0-0 sous pression")
    elif total_goals > 0 and (hg == 0 or ag == 0):
        recs.append("  \u2192 \U0001f3af BTTS possible - equipe a 0 sous pression")

    if not recs:
        recs.append("  \u2192 \U0001f440 A surveiller - pression en hausse")

    recs_text = "\n".join(recs)

    form_section = (SEP + "\n\U0001f4ca FORME:\n" + form_text + "\n") if form_text else ""
    odds_section = (SEP + "\n\U0001f4b9 COTES EN DIRECT:\n" + odds_text + "\n") if odds_text else ""

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
        + recent_text
        + form_section
        + odds_section
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

    def form_lines_for(form):
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
    h_fm = form_lines_for(h_form)
    a_fm = form_lines_for(a_form)

    recs = []
    top_all = []
    for s in h_scorers:
        top_all.append((s["name"], s["season_goals"], h_recent.get(s["name"], 0)))
    for s in a_scorers:
        top_all.append((s["name"], s["season_goals"], a_recent.get(s["name"], 0)))
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
    print("BOUCLE DEMARREE interval=" + str(INTERVAL) + "s", flush=True)

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
                    h_tot, a_tot = result[9], result[10]
                    h_eff, a_eff = result[11], result[12]
                    h_ratio, a_ratio = result[13], result[14]
                    h_xg, a_xg, xg_avail = result[15], result[16], result[17]
                    h_rec, a_rec = result[18], result[19]
                    h_rev, a_rev = result[20], result[21]

                    h = team_name(f, True)
                    a = team_name(f, False)
                    hg, ag = get_goals_by_team(f)

                    # Seuil intelligent selon contexte
                    threshold = get_smart_threshold(minute, hg, ag, h_eff, a_eff)

                    xg_str = " xG:" + str(round(h_xg, 1)) + "/" + str(round(a_xg, 1)) if xg_avail else ""
                    print("[" + str(minute) + "'] " + h + " " + str(hg) + "-" + str(ag) + " " + a
                          + " score=" + str(sc) + " seuil=" + str(threshold)
                          + xg_str
                          + " recent=" + str(h_rec) + "/" + str(a_rec), flush=True)

                    key = str(fid) + "_" + str(minute // 15)
                    if sc >= threshold and key not in alerts:
                        alerts[key] = True
                        hid = get_team_id(f, True)
                        aid = get_team_id(f, False)
                        h_form = get_team_form(hid) if hid else {}
                        a_form = get_team_form(aid) if aid else {}
                        odds = get_inplay_odds(fid)
                        try:
                            msg = build_live_message(f, sc,
                                                     h_son, a_son,
                                                     h_sib, a_sib,
                                                     h_cor, a_cor,
                                                     h_dan, a_dan,
                                                     h_tot, a_tot,
                                                     h_eff, a_eff,
                                                     h_ratio, a_ratio,
                                                     h_xg, a_xg, xg_avail,
                                                     h_rec, a_rec, h_rev, a_rev,
                                                     h_form, a_form, odds, threshold)
                            await bot.send_message(chat_id=str(TELEGRAM_CHAT_ID), text=msg)
                            print("ALERTE: " + h + " vs " + a + " [" + str(minute) + "'] score=" + str(sc) + " seuil=" + str(threshold), flush=True)
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
