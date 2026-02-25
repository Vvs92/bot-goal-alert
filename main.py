"""
Bot Telegram - Alertes buts football
Version finale - Structure API SportMonks v3 conforme doc officielle
"""
import requests
import os
import asyncio
import sys
import time
import traceback
from telegram import Bot

SPORTMONKS_TOKEN = os.environ.get("SPORTMONKS_TOKEN", "")
TELEGRAM_TOKEN   = os.environ.get("TELEGRAM_TOKEN", "")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "")

print("DEMARRAGE BOT v5-FINAL", flush=True)

LEAGUES = {
    2:   "Champions League",
    5:   "Europa League",
    8:   "Premier League",
    564: "La Liga",
    384: "Serie A",
    82:  "Bundesliga",
    301: "Ligue 1",
    79:  "Bundesliga 2",
    9:   "Championship",
    72:  "Eredivisie",
    600: "Super Lig",
    208: "Belgian Pro League",
    501: "Premiership (Ecosse)",
}

# Codes officiels SportMonks (Football Terms doc)
STAT_SHOTS_ON_TARGET  = "shots-on-target"    # id 86
STAT_SHOTS_TOTAL      = "shots-total"         # id 42
STAT_SHOTS_INSIDEBOX  = "shots-insidebox"     # id 49
STAT_DANGEROUS        = "dangerous-attacks"   # id 44
STAT_CORNERS          = "corners"             # id 34
STAT_XG               = "expected-goals"      # id 5304
STAT_ATTACKS          = "attacks"             # id 43
STAT_BALL_POSSESSION  = "ball-possession"     # id 45

INTERVAL         = 45
PRE_MATCH_WINDOW = 30
URL              = "https://api.sportmonks.com/v3/football"

alerts       = {}
prematch_sent = {}
form_cache   = {}
odds_cache   = {}
SEP = "\u2501" * 22


# ═══════════════════════════════════════════
# HELPERS PARTICIPANTS
# ═══════════════════════════════════════════

def team_name(fixture, home=True):
    try:
        for p in fixture.get("participants", []):
            if p.get("meta", {}).get("location") == ("home" if home else "away"):
                return str(p.get("name", "?"))
    except Exception:
        pass
    return "Home" if home else "Away"


def get_team_id(fixture, home=True):
    try:
        for p in fixture.get("participants", []):
            if p.get("meta", {}).get("location") == ("home" if home else "away"):
                return p.get("id")
    except Exception:
        pass
    return None


# ═══════════════════════════════════════════
# SCORE - structure doc officielle confirmée
# "score": {"goals": int, "participant": "home"|"away"}
# Une entrée par équipe par description
# ═══════════════════════════════════════════

def get_goals_by_team(fixture):
    hg, ag = 0, 0
    try:
        scores = fixture.get("scores", [])
        # On cherche les entrees CURRENT (score en temps reel)
        # Chaque entree = une equipe (home ou away)
        h_found, a_found = False, False
        for s in scores:
            if s.get("description") != "CURRENT":
                continue
            sd = s.get("score", {})
            goals = sd.get("goals", 0)
            try:
                goals = int(goals) if goals is not None else 0
            except (ValueError, TypeError):
                goals = 0
            participant = str(sd.get("participant", "")).lower()
            if participant == "home":
                hg = goals
                h_found = True
            elif participant == "away":
                ag = goals
                a_found = True

        # Fallback: si CURRENT pas trouve, prend 2ND_HALF (cumul 1MT+2MT)
        if not h_found or not a_found:
            for s in scores:
                if s.get("description") != "2ND_HALF":
                    continue
                sd = s.get("score", {})
                goals = sd.get("goals", 0)
                try:
                    goals = int(goals) if goals is not None else 0
                except (ValueError, TypeError):
                    goals = 0
                participant = str(sd.get("participant", "")).lower()
                if participant == "home" and not h_found:
                    hg = goals
                elif participant == "away" and not a_found:
                    ag = goals

    except Exception as e:
        print("ERREUR score: " + str(e), flush=True)
    return hg, ag


# ═══════════════════════════════════════════
# MINUTE - via currentPeriod (doc officielle)
# Period fields: minutes (cumulatif), counts_from, ticking
# ═══════════════════════════════════════════

def get_minute(fixture):
    # Methode 1: currentPeriod si disponible
    # Champs: minutes (cumulatif depuis debut du match), ticking, counts_from
    try:
        cp = fixture.get("currentPeriod") or fixture.get("current_period")
        if cp and isinstance(cp, dict):
            mins = cp.get("minutes")
            ticking = cp.get("ticking", False)
            if mins is not None and ticking:
                return int(mins)
            elif mins is not None and mins > 0:
                return int(mins)
    except Exception:
        pass

    # Methode 2: timestamp depuis le debut
    try:
        starting = fixture.get("starting_at_timestamp", 0)
        if starting and starting > 0:
            elapsed = int((time.time() - starting) / 60)
            if 1 <= elapsed <= 130:
                return elapsed
    except Exception:
        pass

    # Methode 3: state.clock (ancien fallback)
    try:
        state = fixture.get("state", {})
        if isinstance(state, dict):
            clock = state.get("clock", {})
            if isinstance(clock, dict):
                mm = clock.get("mm")
                if mm is not None and int(mm) > 0:
                    return int(mm)
    except Exception:
        pass

    return 0


# ═══════════════════════════════════════════
# STATS - par participant_id + fallback location
# ═══════════════════════════════════════════

def stat_val(stats, code, team_id, location_fallback):
    """Cherche stat par code + team_id, fallback sur location string"""
    try:
        # Priorite: participant_id exact
        for s in stats:
            t = s.get("type", {})
            tcode = t.get("code", "") if isinstance(t, dict) else ""
            if tcode != code:
                continue
            if s.get("participant_id") == team_id:
                v = s.get("data", {}).get("value", 0)
                try:
                    return float(v) if v is not None else 0.0
                except (ValueError, TypeError):
                    return 0.0
        # Fallback: location
        for s in stats:
            t = s.get("type", {})
            tcode = t.get("code", "") if isinstance(t, dict) else ""
            if tcode != code:
                continue
            loc = str(s.get("location", "")).lower()
            if loc == location_fallback.lower():
                v = s.get("data", {}).get("value", 0)
                try:
                    return float(v) if v is not None else 0.0
                except (ValueError, TypeError):
                    return 0.0
    except Exception:
        pass
    return 0.0


def get_season_id(fixture):
    return fixture.get("season_id")


# ═══════════════════════════════════════════
# SEUIL INTELLIGENT
# ═══════════════════════════════════════════

def get_smart_threshold(minute, hg, ag, h_eff, a_eff):
    base = 48
    if minute >= 80:   base -= 12
    elif minute >= 70: base -= 8
    elif minute >= 55: base -= 4
    elif minute < 15:  base += 8
    diff = abs(hg - ag)
    if diff == 1 and minute >= 60: base -= 6
    elif diff >= 2:                base += 4
    dom_eff = h_eff if hg <= ag else a_eff
    if dom_eff == "inefficace":    base += 6
    return max(38, min(base, 70))


# ═══════════════════════════════════════════
# MOMENTUM RECENT 10 MINUTES
# ═══════════════════════════════════════════

def get_recent_momentum(events, hid, aid, current_minute):
    min_start = max(0, current_minute - 10)
    h_score, a_score = 0, 0
    h_evts, a_evts = [], []
    for ev in events:
        try:
            m = int(ev.get("minute", 0) or 0)
            if m < min_start:
                continue
            t     = ev.get("type", {})
            code  = t.get("code", "") if isinstance(t, dict) else ""
            ev_tid = ev.get("participant_id") or ev.get("team_id")
            pts, label = 0, ""
            if "shot-on-target" in code or "shots-on-target" in code:
                pts, label = 3, "tir cadre"
            elif "shot" in code and "off" not in code and "block" not in code:
                pts, label = 1, "tir"
            elif "corner" in code:
                pts, label = 2, "corner"
            elif "dangerous" in code:
                pts, label = 1, "att.dang"
            elif "goal" in code and "own" not in code:
                pts, label = 5, "BUT"
            if pts > 0:
                if ev_tid == hid:
                    h_score += pts
                    h_evts.append(str(m) + "' " + label)
                elif ev_tid == aid:
                    a_score += pts
                    a_evts.append(str(m) + "' " + label)
        except Exception:
            continue
    return h_score, a_score, h_evts[-4:], a_evts[-4:]


# ═══════════════════════════════════════════
# COTES IN-PLAY
# ═══════════════════════════════════════════

def get_inplay_odds(fixture_id):
    key = str(fixture_id)
    if key in odds_cache:
        t, d = odds_cache[key]
        if time.time() - t < 60:
            return d
    try:
        r = requests.get(
            URL + "/odds/inplay/fixtures/" + str(fixture_id),
            params={"api_token": SPORTMONKS_TOKEN, "per_page": 100},
            timeout=10
        )
        if r.status_code != 200:
            odds_cache[key] = (time.time(), {})
            return {}
        result = {}
        for odd in r.json().get("data", []):
            market = str(odd.get("market_description", "") or "").lower()
            label  = str(odd.get("label", "") or "").lower()
            try:
                val = float(odd.get("value") or 0)
            except (ValueError, TypeError):
                continue
            if val <= 0:
                continue
            if "next goal" in market:
                if "no" in label:
                    result["no_goal_odd"] = val
                elif "home" in label or label == "1":
                    result["home_goal_odd"] = val
                elif "away" in label or label == "2":
                    result["away_goal_odd"] = val
            if "over" in label and "0.5" in label:
                result["over_05_odd"] = val
            elif "over" in label and "1.5" in label:
                result["over_15_odd"] = val
        odds_cache[key] = (time.time(), result)
        return result
    except Exception as e:
        print("ERREUR odds: " + str(e), flush=True)
        return {}


def format_odds_insight(odds, hg, ag, dominant_side, h, a):
    if not odds:
        return ""
    lines = []
    total = hg + ag
    ng    = odds.get("no_goal_odd", 0)
    hg_o  = odds.get("home_goal_odd", 0)
    ag_o  = odds.get("away_goal_odd", 0)
    o05   = odds.get("over_05_odd", 0)
    o15   = odds.get("over_15_odd", 0)
    if ng:
        if ng < 1.5:
            lines.append("\U0001f4b9 But peu probable (no goal @" + str(round(ng,2)) + ")")
        elif ng > 2.5:
            lines.append("\U0001f4b9 But probable (no goal @" + str(round(ng,2)) + ")")
    if hg_o and ag_o:
        if hg_o < ag_o and dominant_side == "home":
            lines.append("\U0001f4b9 " + h + " marque ensuite @" + str(round(hg_o,2)))
        elif ag_o < hg_o and dominant_side == "away":
            lines.append("\U0001f4b9 " + a + " marque ensuite @" + str(round(ag_o,2)))
    if o05 and o05 < 1.4:
        lines.append("\U0001f4b9 Over " + str(total) + ".5 tres probable @" + str(round(o05,2)))
    if o15 and o15 < 1.6:
        lines.append("\U0001f4b9 Over " + str(total+1) + ".5 favori @" + str(round(o15,2)))
    return "\n".join(lines)


# ═══════════════════════════════════════════
# FORME DES EQUIPES
# ═══════════════════════════════════════════

def get_team_form(team_id):
    key = str(team_id)
    if key in form_cache:
        t, d = form_cache[key]
        if time.time() - t < 3600:
            return d
    try:
        r = requests.get(
            URL + "/teams/" + str(team_id) + "/fixtures",
            params={
                "api_token": SPORTMONKS_TOKEN,
                "include": "scores;events.type;participants",
                "per_page": 5,
                "sort": "-starting_at"
            },
            timeout=15
        )
        if r.status_code != 200:
            return {}
        matches = r.json().get("data", [])
        form = {
            "unbeaten": 0, "wins": 0, "draws": 0, "losses": 0,
            "scored_1st_half": 0, "scored_2nd_half": 0,
            "scored_last_15": 0, "always_scored": True,
            "clean_sheets": 0, "total_matches": 0,
        }
        for match in matches:
            form["total_matches"] += 1
            is_home = any(
                p.get("id") == team_id and p.get("meta", {}).get("location") == "home"
                for p in match.get("participants", [])
            )
            # Score via CURRENT ou FT
            hg_m, ag_m = 0, 0
            for s in match.get("scores", []):
                if s.get("description") not in ("CURRENT", "FT", "2ND_HALF"):
                    continue
                sd = s.get("score", {})
                try:
                    g = int(sd.get("goals", 0) or 0)
                except (ValueError, TypeError):
                    g = 0
                p = str(sd.get("participant", "")).lower()
                if p == "home":
                    hg_m = g
                elif p == "away":
                    ag_m = g
            team_g = hg_m if is_home else ag_m
            opp_g  = ag_m if is_home else hg_m
            if team_g > opp_g:
                form["wins"] += 1; form["unbeaten"] += 1
            elif team_g == opp_g:
                form["draws"] += 1; form["unbeaten"] += 1
            else:
                form["losses"] += 1
            if team_g == 0:
                form["always_scored"] = False
            if opp_g == 0:
                form["clean_sheets"] += 1
            # Timing des buts
            g1h = g2h = g_last15 = 0
            for ev in match.get("events", []):
                t2 = ev.get("type", {})
                code = t2.get("code", "") if isinstance(t2, dict) else ""
                if "goal" not in code.lower() or "own" in code.lower():
                    continue
                if (ev.get("participant_id") or ev.get("team_id")) != team_id:
                    continue
                m = int(ev.get("minute", 0) or 0)
                if m <= 45: g1h += 1
                else:       g2h += 1
                if m >= 75: g_last15 += 1
            if g1h > 0:    form["scored_1st_half"] += 1
            if g2h > 0:    form["scored_2nd_half"] += 1
            if g_last15>0: form["scored_last_15"] += 1
        form_cache[key] = (time.time(), form)
        return form
    except Exception as e:
        print("ERREUR form: " + str(e), flush=True)
        return {}


def build_form_insights(form, tname, current_goals, minute):
    lines = []
    n = form.get("total_matches", 0)
    if n == 0:
        return lines
    if form.get("unbeaten", 0) == n and n >= 4:
        lines.append("\U0001f525 " + tname + " invaincue sur " + str(n) + " matchs")
    elif form.get("wins", 0) >= 4:
        lines.append("\U0001f525 " + tname + " " + str(form["wins"]) + "V/" + str(n))
    if form.get("always_scored") and n >= 4:
        lines.append("\u26bd " + tname + " marque dans ses " + str(n) + " derniers matchs")
    s1h  = form.get("scored_1st_half", 0)
    s2h  = form.get("scored_2nd_half", 0)
    sl15 = form.get("scored_last_15", 0)
    if minute < 46 and s1h >= max(1, int(n * 0.6)):
        lines.append("\u23f0 " + tname + " marque souvent en 1MT (" + str(s1h) + "/" + str(n) + ")")
    if minute >= 46 and s2h >= max(1, int(n * 0.6)):
        lines.append("\u23f0 " + tname + " marque souvent en 2MT (" + str(s2h) + "/" + str(n) + ")")
    if minute >= 70 and sl15 >= max(1, int(n * 0.5)):
        lines.append("\u23f0 " + tname + " marque souvent en fin de match (" + str(sl15) + "/" + str(n) + ")")
    if current_goals == 0 and form.get("always_scored") and n >= 3:
        lines.append("\u26a0\ufe0f " + tname + " n'a pas encore marque - mais marque toujours!")
    return lines


# ═══════════════════════════════════════════
# BUTEURS - 3 methodes en cascade
# ═══════════════════════════════════════════

def get_top_scorers_for_team(team_id, season_id):
    """Methode 1: topscorers par saison"""
    scorers = []
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
                    name = str(player.get("name") or player.get("display_name") or "")
                    goals = int(s.get("total", 0) or 0)
                    if name and goals >= 2:
                        scorers.append({"name": name, "season_goals": goals})
                if scorers:
                    scorers.sort(key=lambda x: x["season_goals"], reverse=True)
                    print("Buteurs m1 OK pour team " + str(team_id) + ": " + str(len(scorers)), flush=True)
                    return scorers[:4]
        except Exception as e:
            print("ERREUR buteurs m1: " + str(e), flush=True)

    """Methode 2: events des 10 derniers matchs"""
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
                    t2   = ev.get("type", {})
                    code = t2.get("code", "") if isinstance(t2, dict) else ""
                    if "goal" not in code.lower() or "own" in code.lower():
                        continue
                    ev_tid = ev.get("participant_id") or ev.get("team_id")
                    if ev_tid != team_id:
                        continue
                    pname = str(ev.get("player_name") or "")
                    if not pname:
                        pl = ev.get("player", {})
                        if isinstance(pl, dict):
                            pname = str(pl.get("name") or pl.get("display_name") or "")
                    if pname:
                        goal_count[pname] = goal_count.get(pname, 0) + 1
            for name, goals in goal_count.items():
                scorers.append({"name": name, "season_goals": goals})
            if scorers:
                scorers.sort(key=lambda x: x["season_goals"], reverse=True)
                print("Buteurs m2 OK pour team " + str(team_id) + ": " + str(len(scorers)), flush=True)
                return scorers[:4]
    except Exception as e:
        print("ERREUR buteurs m2: " + str(e), flush=True)

    print("Buteurs: aucun trouve pour team " + str(team_id), flush=True)
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
            scorers_match = set()
            for ev in match.get("events", []):
                t2   = ev.get("type", {})
                code = t2.get("code", "") if isinstance(t2, dict) else ""
                if "goal" not in code.lower() or "own" in code.lower():
                    continue
                if (ev.get("participant_id") or ev.get("team_id")) != team_id:
                    continue
                pname = str(ev.get("player_name") or "")
                if not pname:
                    pl = ev.get("player", {})
                    if isinstance(pl, dict):
                        pname = str(pl.get("name") or pl.get("display_name") or "")
                if pname:
                    scorers_match.add(pname)
            for pname in scorers_match:
                scorer_count[pname] = scorer_count.get(pname, 0) + 1
        return scorer_count
    except Exception as e:
        print("ERREUR recent goals: " + str(e), flush=True)
        return {}


# ═══════════════════════════════════════════
# FIXTURES LIVE
# Inclure currentPeriod pour minute exacte
# ═══════════════════════════════════════════

def get_fixture_details(fixture_id):
    try:
        r = requests.get(
            URL + "/fixtures/" + str(fixture_id),
            params={
                "api_token": SPORTMONKS_TOKEN,
                "include": "statistics.type;events.type;currentPeriod"
            },
            timeout=15
        )
        if r.status_code == 200:
            d = r.json().get("data", {})
            return (
                d.get("statistics", []),
                d.get("events", []),
                d.get("currentPeriod") or d.get("current_period")
            )
    except Exception as e:
        print("ERREUR fixture details: " + str(e), flush=True)
    return [], [], None


def get_fixtures():
    try:
        r = requests.get(
            URL + "/livescores/inplay",
            params={
                "api_token": SPORTMONKS_TOKEN,
                "include": "participants;scores;state",
                "per_page": 50
            },
            timeout=15
        )
        print("API livescores: " + str(r.status_code), flush=True)
        if r.status_code != 200:
            return []
        all_f    = r.json().get("data", [])
        filtered = [f for f in all_f if f.get("league_id") in LEAGUES]
        print(str(len(all_f)) + " live, " + str(len(filtered)) + " nos ligues", flush=True)

        enriched = []
        for f in filtered:
            fid               = f.get("id")
            stats, events, cp = get_fixture_details(fid)
            f["statistics"]   = stats
            f["events"]       = events
            if cp:
                f["currentPeriod"] = cp
            print("Fixture " + str(fid) + ": stats=" + str(len(stats))
                  + " events=" + str(len(events))
                  + " period_min=" + str(cp.get("minutes") if cp else "N/A"),
                  flush=True)
            enriched.append(f)
        return enriched
    except Exception as e:
        print("ERREUR get_fixtures: " + str(e), flush=True)
        return []


def get_upcoming_fixtures():
    try:
        from datetime import datetime, timezone
        now      = int(time.time())
        soon     = now + (PRE_MATCH_WINDOW * 60)
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
        print(str(len(upcoming)) + " match(s) dans 30min", flush=True)
        return upcoming
    except Exception as e:
        print("ERREUR upcoming: " + str(e), flush=True)
        return []


# ═══════════════════════════════════════════
# CALCUL MOMENTUM
# ═══════════════════════════════════════════

def calc_efficiency(son, tot):
    if tot < 3:
        return 0.0, "neutre", 0
    ratio = son / tot
    if ratio >= 0.50:   return ratio, "efficace", 10
    elif ratio >= 0.30: return ratio, "neutre", 0
    else:               return ratio, "inefficace", -8


def momentum(fixture):
    stats  = fixture.get("statistics", [])
    events = fixture.get("events", [])
    hid    = get_team_id(fixture, True)
    aid    = get_team_id(fixture, False)
    minute = get_minute(fixture)

    def sv(code, tid, loc):
        return stat_val(stats, code, tid, loc)

    h_son = sv(STAT_SHOTS_ON_TARGET, hid, "home")
    a_son = sv(STAT_SHOTS_ON_TARGET, aid, "away")
    h_sib = sv(STAT_SHOTS_INSIDEBOX, hid, "home")
    a_sib = sv(STAT_SHOTS_INSIDEBOX, aid, "away")
    h_cor = sv(STAT_CORNERS,          hid, "home")
    a_cor = sv(STAT_CORNERS,          aid, "away")
    h_dan = sv(STAT_DANGEROUS,        hid, "home")
    a_dan = sv(STAT_DANGEROUS,        aid, "away")
    h_tot = sv(STAT_SHOTS_TOTAL,      hid, "home")
    a_tot = sv(STAT_SHOTS_TOTAL,      aid, "away")
    h_xg  = sv(STAT_XG,               hid, "home")
    a_xg  = sv(STAT_XG,               aid, "away")

    score = 0
    son = h_son + a_son
    sib = h_sib + a_sib
    cor = h_cor + a_cor
    dan = h_dan + a_dan

    # Tirs cadres
    if son >= 10:   score += 22
    elif son >= 6:  score += 14
    elif son >= 3:  score += 7

    # Tirs surface
    if sib >= 12:   score += 20
    elif sib >= 7:  score += 13
    elif sib >= 4:  score += 6

    # Corners
    if cor >= 10:   score += 10
    elif cor >= 6:  score += 6
    elif cor >= 3:  score += 3

    # Attaques dangereuses
    if dan >= 40:   score += 8
    elif dan >= 25: score += 5
    elif dan >= 12: score += 2

    # xG
    xg_total     = h_xg + a_xg
    xg_available = xg_total > 0
    if xg_available:
        if xg_total >= 3.0:   score += 22
        elif xg_total >= 2.0: score += 16
        elif xg_total >= 1.0: score += 10
        elif xg_total >= 0.5: score += 5

    # Efficacite
    h_ratio, h_eff, h_bonus = calc_efficiency(h_son, h_tot)
    a_ratio, a_eff, a_bonus = calc_efficiency(a_son, a_tot)
    score += h_bonus if h_tot >= a_tot else a_bonus

    # Momentum recent
    h_rec, a_rec, h_rev, a_rev = get_recent_momentum(events, hid, aid, minute)
    rec_total = h_rec + a_rec
    if rec_total >= 12:   score += 18
    elif rec_total >= 7:  score += 12
    elif rec_total >= 3:  score += 6

    return (min(max(score, 0), 100),
            h_son, a_son, h_sib, a_sib, h_cor, a_cor, h_dan, a_dan,
            h_tot, a_tot, h_eff, a_eff, h_ratio, a_ratio,
            h_xg, a_xg, xg_available,
            h_rec, a_rec, h_rev, a_rev)


def get_dominant(h_dan, a_dan, h_son, a_son, h_cor, a_cor, hname, aname):
    h = h_dan + h_son * 3 + h_cor
    a = a_dan + a_son * 3 + a_cor
    if h > a * 1.4:   return hname, "home"
    elif a > h * 1.4: return aname, "away"
    return None, "balanced"


# ═══════════════════════════════════════════
# CONSTRUCTION DES MESSAGES
# ═══════════════════════════════════════════

def eff_emoji(e):
    if e == "efficace":   return " \U0001f3af"
    if e == "inefficace": return " \U0001f4a8"
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
    league = str(LEAGUES.get(fixture.get("league_id"), "Ligue"))
    h      = team_name(fixture, True)
    a      = team_name(fixture, False)
    hg, ag = get_goals_by_team(fixture)
    total  = hg + ag

    dom_team, dom_side = get_dominant(h_dan, a_dan, h_son, a_son, h_cor, a_cor, h, a)
    gauge = "\U0001f7e9" * int(score/10) + "\u2b1c" * (10 - int(score/10))

    margin = score - threshold
    if margin >= 20 or score >= 75:
        lvl, emj = "ALERTE MAX",      "\U0001f534"
    elif margin >= 10 or score >= 60:
        lvl, emj = "FORTE PRESSION",  "\U0001f7e0"
    else:
        lvl, emj = "PRESSION",        "\U0001f7e1"

    def stat_line(tname, son, sib, cor, dan, tot, xg, eff, ratio):
        parts = []
        if son >= 1: parts.append(str(int(son)) + " tirs cadres")
        if sib >= 1: parts.append(str(int(sib)) + " tirs surface")
        if cor >= 1: parts.append(str(int(cor)) + " corners")
        if dan >= 1: parts.append(str(int(dan)) + " att.dang.")
        if not parts:
            return ""
        pct    = str(int(ratio*100)) + "%" if tot >= 3 else "?"
        xg_str = " | xG: " + str(round(xg, 2)) if xg > 0 else ""
        return ("  \U0001f539 " + tname + ":\n"
                + "    " + " | ".join(parts) + "\n"
                + "    precision: " + pct + eff_emoji(eff) + xg_str)

    h_line    = stat_line(h, h_son, h_sib, h_cor, h_dan, h_tot, h_xg, h_eff, h_ratio)
    a_line    = stat_line(a, a_son, a_sib, a_cor, a_dan, a_tot, a_xg, a_eff, a_ratio)
    stats_txt = ((h_line + "\n") if h_line else "") + (a_line or "")
    if not stats_txt.strip():
        stats_txt = "  \u2022 Stats en cours de chargement"

    rec_txt = ""
    if h_rev or a_rev:
        rec_txt = SEP + "\n\u26a1 MOMENTUM 10 DERNIERES MIN:\n"
        if h_rev: rec_txt += "  \U0001f539 " + h + ": " + ", ".join(h_rev) + "\n"
        if a_rev: rec_txt += "  \U0001f539 " + a + ": " + ", ".join(a_rev) + "\n"
        if h_rec > a_rec * 1.5:
            rec_txt += "  \u27a1\ufe0f " + h + " domine ces 10 dernieres min\n"
        elif a_rec > h_rec * 1.5:
            rec_txt += "  \u27a1\ufe0f " + a + " domine ces 10 dernieres min\n"
        else:
            rec_txt += "  \u27a1\ufe0f Pression equilibree\n"

    form_lines = []
    form_lines.extend(build_form_insights(h_form, h, hg, minute))
    form_lines.extend(build_form_insights(a_form, a, ag, minute))
    form_txt  = "\n".join(["  " + l for l in form_lines])
    odds_txt  = format_odds_insight(odds, hg, ag, dom_side, h, a)

    # Dominant pour recommandations
    if h_rev or a_rev:
        rec_dom_side = "home" if h_rec >= a_rec else "away"
        rec_dom      = h if rec_dom_side == "home" else a
        rec_dom_eff  = h_eff if rec_dom_side == "home" else a_eff
    else:
        rec_dom_side = dom_side
        rec_dom      = dom_team
        rec_dom_eff  = h_eff if dom_side == "home" else a_eff

    recs = []
    if h_son + a_son >= 5 or h_sib + a_sib >= 5:
        if rec_dom_side in ("home", "away"):
            if rec_dom_eff == "inefficace":
                recs.append("  \u2192 \u26bd " + str(rec_dom) + " domine mais imprecise")
            else:
                recs.append("  \u2192 \u26bd Prochain but: " + str(rec_dom) + eff_emoji(rec_dom_eff))
        else:
            recs.append("  \u2192 \u26bd Prochain but: Match ouvert")
        if rec_dom_eff != "inefficace":
            recs.append("  \u2192 \U0001f4c8 Over " + str(total) + ".5 buts")
            recs.append("  \u2192 \U0001f4c8 Over 0.5 buts " + ("1ere MT" if minute < 46 else "2eme MT"))
        else:
            recs.append("  \u2192 \U0001f4c8 Over " + str(total) + ".5 possible mais equipe imprecise")

    if hg == 0 and ag == 0 and minute >= 30:
        recs.append("  \u2192 \U0001f3af BTTS - 0-0 sous pression")
    elif total > 0 and (hg == 0 or ag == 0):
        recs.append("  \u2192 \U0001f3af BTTS - equipe a 0 sous pression")
    if not recs:
        recs.append("  \u2192 \U0001f440 A surveiller - pression en hausse")

    form_section = (SEP + "\n\U0001f4ca FORME:\n" + form_txt + "\n") if form_txt.strip() else ""
    odds_section = (SEP + "\n\U0001f4b9 COTES LIVE:\n" + odds_txt + "\n") if odds_txt else ""

    return (emj + " " + lvl + " - BUT POTENTIEL\n"
            + SEP + "\n"
            + "\U0001f3c6 " + league + "\n"
            + "\u2694\ufe0f  " + h + " " + str(hg) + " - " + str(ag) + " " + a + "\n"
            + "\u23f1\ufe0f  " + str(minute) + "' | Momentum: " + str(score) + "/100\n"
            + gauge + "\n"
            + SEP + "\n"
            + "\U0001f4ca STATS:\n" + stats_txt + "\n"
            + rec_txt
            + form_section
            + odds_section
            + SEP + "\n"
            + "\U0001f4a1 QUOI JOUER:\n" + "\n".join(recs) + "\n"
            + SEP + "\n"
            + "\u26a0\ufe0f Parie de facon responsable")


def build_prematch_message(fixture, h, a, league, mins_before,
                           h_scorers, a_scorers, h_recent, a_recent,
                           h_form, a_form):

    def scorer_lines(scorers, recent):
        if not scorers:
            return "  \u2022 Aucun buteur notable"
        lines = []
        for s in scorers:
            line = "  \u2022 " + s["name"] + " - " + str(s["season_goals"]) + " buts"
            r = recent.get(s["name"], 0)
            if r >= 2:   line += "\n    \u2705 En feu! " + str(r) + " buts sur 3 derniers matchs"
            elif r == 1: line += "\n    \u2705 A marque au dernier match"
            lines.append(line)
        return "\n".join(lines)

    def form_summary(form):
        n = form.get("total_matches", 0)
        if n == 0:
            return "  \u2022 Forme non disponible"
        lines = []
        if form.get("unbeaten", 0) == n:
            lines.append("  \U0001f7e2 Invaincue sur " + str(n) + " matchs")
        elif form.get("wins", 0) >= 3:
            lines.append("  \U0001f7e2 " + str(form["wins"]) + "V " + str(form["draws"]) + "N " + str(form["losses"]) + "D sur " + str(n))
        else:
            lines.append("  \u2022 " + str(form["wins"]) + "V " + str(form["draws"]) + "N " + str(form["losses"]) + "D sur " + str(n))
        if form.get("always_scored"):
            lines.append("  \u26bd Marque dans ses " + str(n) + " derniers matchs")
        s1h = form.get("scored_1st_half", 0)
        if s1h >= max(1, int(n * 0.6)):
            lines.append("  \u23f0 Marque souvent en 1MT (" + str(s1h) + "/" + str(n) + ")")
        sl15 = form.get("scored_last_15", 0)
        if sl15 >= max(1, int(n * 0.5)):
            lines.append("  \u23f0 Marque souvent en fin de match (" + str(sl15) + "/" + str(n) + ")")
        return "\n".join(lines) if lines else "  \u2022 Forme correcte"

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

    return ("\u26bd PRE-MATCH - BUTEURS A SURVEILLER\n"
            + SEP + "\n"
            + "\U0001f3c6 " + league + "\n"
            + "\u2694\ufe0f " + h + " vs " + a + "\n"
            + "\u23f1\ufe0f Coup d'envoi dans ~" + str(mins_before) + " min\n"
            + SEP + "\n"
            + "\U0001f534 " + h + ":\n" + scorer_lines(h_scorers, h_recent) + "\n"
            + "\U0001f4ca Forme:\n" + form_summary(h_form) + "\n"
            + SEP + "\n"
            + "\U0001f535 " + a + ":\n" + scorer_lines(a_scorers, a_recent) + "\n"
            + "\U0001f4ca Forme:\n" + form_summary(a_form) + "\n"
            + SEP + "\n"
            + "\U0001f4a1 QUOI JOUER:\n" + "\n".join(recs) + "\n"
            + SEP + "\n"
            + "\u26a0\ufe0f Parie de facon responsable")


# ═══════════════════════════════════════════
# BOUCLE PRINCIPALE
# ═══════════════════════════════════════════

async def send_prematch_alerts(bot):
    upcoming = get_upcoming_fixtures()
    for fixture in upcoming:
        fid = fixture.get("id")
        if str(fid) in prematch_sent:
            continue
        start        = fixture.get("starting_at_timestamp", 0)
        mins_before  = int((start - time.time()) / 60)
        if mins_before < 1:
            continue
        league = str(LEAGUES.get(fixture.get("league_id"), "Ligue"))
        h      = team_name(fixture, True)
        a      = team_name(fixture, False)
        hid    = get_team_id(fixture, True)
        aid    = get_team_id(fixture, False)
        sid    = get_season_id(fixture)
        print("Pre-match: " + h + " vs " + a + " dans " + str(mins_before) + "min", flush=True)
        h_scorers = get_top_scorers_for_team(hid, sid) if hid else []
        a_scorers = get_top_scorers_for_team(aid, sid) if aid else []
        h_recent  = get_player_recent_goals(hid) if hid else {}
        a_recent  = get_player_recent_goals(aid) if aid else {}
        h_form    = get_team_form(hid) if hid else {}
        a_form    = get_team_form(aid) if aid else {}
        try:
            msg = build_prematch_message(fixture, h, a, league, mins_before,
                                         h_scorers, a_scorers, h_recent, a_recent,
                                         h_form, a_form)
            await bot.send_message(chat_id=str(TELEGRAM_CHAT_ID), text=msg)
            prematch_sent[str(fid)] = True
            print("Pre-match envoye: " + h + " vs " + a, flush=True)
            await asyncio.sleep(2)
        except Exception as e:
            print("ERREUR pre-match: " + str(e), flush=True)
            prematch_sent[str(fid)] = True
    # Nettoyage cache
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
                print("Aucun match live dans nos ligues", flush=True)
            else:
                for f in fixtures:
                    try:
                        fid    = f.get("id")
                        minute = get_minute(f)
                        res    = momentum(f)
                        sc                           = res[0]
                        h_son, a_son                 = res[1], res[2]
                        h_sib, a_sib                 = res[3], res[4]
                        h_cor, a_cor                 = res[5], res[6]
                        h_dan, a_dan                 = res[7], res[8]
                        h_tot, a_tot                 = res[9], res[10]
                        h_eff, a_eff                 = res[11], res[12]
                        h_ratio, a_ratio             = res[13], res[14]
                        h_xg, a_xg, xg_avail         = res[15], res[16], res[17]
                        h_rec, a_rec, h_rev, a_rev   = res[18], res[19], res[20], res[21]

                        h      = team_name(f, True)
                        a      = team_name(f, False)
                        hg, ag = get_goals_by_team(f)
                        thresh = get_smart_threshold(minute, hg, ag, h_eff, a_eff)
                        xg_str = " xG:" + str(round(h_xg,1)) + "/" + str(round(a_xg,1)) if xg_avail else ""

                        print("[" + str(minute) + "'] " + h + " " + str(hg) + "-" + str(ag) + " " + a
                              + " score=" + str(sc) + " seuil=" + str(thresh)
                              + xg_str + " recent=" + str(h_rec) + "/" + str(a_rec),
                              flush=True)

                        key = str(fid) + "_" + str(minute // 15)
                        if sc >= thresh and key not in alerts:
                            alerts[key] = True
                            hid    = get_team_id(f, True)
                            aid    = get_team_id(f, False)
                            h_form = get_team_form(hid) if hid else {}
                            a_form = get_team_form(aid) if aid else {}
                            odds   = get_inplay_odds(fid)
                            msg    = build_live_message(
                                f, sc,
                                h_son, a_son, h_sib, a_sib,
                                h_cor, a_cor, h_dan, a_dan,
                                h_tot, a_tot, h_eff, a_eff,
                                h_ratio, a_ratio,
                                h_xg, a_xg, xg_avail,
                                h_rec, a_rec, h_rev, a_rev,
                                h_form, a_form, odds, thresh
                            )
                            await bot.send_message(chat_id=str(TELEGRAM_CHAT_ID), text=msg)
                            print("ALERTE ENVOYEE: " + h + " vs " + a
                                  + " [" + str(minute) + "'] score=" + str(sc), flush=True)
                            await asyncio.sleep(2)
                    except Exception as e:
                        print("ERREUR match: " + str(e), flush=True)
                        traceback.print_exc()

                # Nettoyage cache alertes
                if len(alerts) > 500:
                    for k in list(alerts.keys())[:250]:
                        del alerts[k]

        except Exception as e:
            print("ERREUR BOUCLE: " + str(e), flush=True)
            traceback.print_exc()

        print("Prochaine verif dans " + str(INTERVAL) + "s", flush=True)
        await asyncio.sleep(INTERVAL)


if __name__ == "__main__":
    asyncio.run(run_forever())
