"""
╔══════════════════════════════════════════════════════╗
║   BOT TELEGRAM - ALERTES BUTS FOOTBALL               ║
║   API : api-football v3 (api-sports.io) - GRATUIT    ║
║   Structure JSON confirmée doc officielle             ║
╚══════════════════════════════════════════════════════╝

Structure API-Football v3 (confirmee) :
  GET /fixtures?live=all
  response[i] = {
    fixture: { id, status: { elapsed, short }, timestamp },
    league:  { id, name },
    teams:   { home: { id, name }, away: { id, name } },
    goals:   { home: int|null, away: int|null },
  }

  GET /fixtures/statistics?fixture=ID
  response[i] = {
    team: { id, name },
    statistics: [ { type: "Shots on Goal", value: int|null }, ... ]
  }
  Types dispo: "Shots on Goal", "Shots off Goal", "Shots insidebox",
               "Total Shots", "Blocked Shots", "Corner Kicks",
               "Ball Possession", "Goalkeeper Saves", "expected_goals"

  GET /fixtures/events?fixture=ID
  response[i] = {
    time:   { elapsed, extra },
    team:   { id, name },
    player: { id, name },
    type:   "Goal"|"Card"|"subst"|"Var",
    detail: "Normal Goal"|"Penalty"|"Own Goal"|...
  }

  GET /teams/statistics?league=X&season=Y&team=Z
  response = {
    form: "WWDLW...",
    goals: { for: { total: {total}, minute: {"0-15":{total,percentage},...} } }
    fixtures: { played:{total}, wins:{total}, draws:{total}, loses:{total} }
  }

COMPTAGE REQUETES (plan gratuit = 100/jour) :
  - 1 appel /fixtures?live=all toutes les 45s
  - /statistics + /events par match live (cache 30s)
  - /teams/statistics par equipe (cache 1h)
  Total soiree chargee : ~60-80 requetes OK
"""

import requests
import os
import asyncio
import time
import traceback
from telegram import Bot

# ═══════════════════════════════════════════════════════
# CONFIG
# ═══════════════════════════════════════════════════════

API_KEY          = os.environ.get("APIFOOTBALL_KEY", "")
TELEGRAM_TOKEN   = os.environ.get("TELEGRAM_TOKEN", "")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "")

BASE_URL = "https://v3.football.api-sports.io"
HEADERS  = {
    "x-rapidapi-host": "v3.football.api-sports.io",
    "x-rapidapi-key":  API_KEY
}

INTERVAL       = 45
CURRENT_SEASON = 2024
SEP            = "\u2501" * 22

print("=" * 45, flush=True)
print("  BOT ALERTES FOOTBALL v6 - API-Football", flush=True)
print("=" * 45, flush=True)

# Ligues suivies (IDs API-Football)
LEAGUES = {
    2:   "Champions League",
    3:   "Europa League",
    848: "Conference League",
    39:  "Premier League",
    140: "La Liga",
    135: "Serie A",
    78:  "Bundesliga",
    61:  "Ligue 1",
    79:  "Bundesliga 2",
    40:  "Championship",
    88:  "Eredivisie",
    203: "Super Lig",
    144: "Belgian Pro League",
    179: "Premiership (Ecosse)",
}

# Caches
alerts_sent = {}
form_cache  = {}
stats_cache = {}
req_count   = {"today": 0, "date": ""}


# ═══════════════════════════════════════════════════════
# COMPTEUR + APPEL API
# ═══════════════════════════════════════════════════════

def count_req():
    from datetime import date
    today = str(date.today())
    if req_count["date"] != today:
        req_count["date"]  = today
        req_count["today"] = 0
    req_count["today"] += 1
    print("  [API] req today: " + str(req_count["today"]) + "/100", flush=True)


def api_get(endpoint, params=None):
    try:
        r = requests.get(
            BASE_URL + endpoint,
            headers=HEADERS,
            params=params or {},
            timeout=12
        )
        count_req()
        if r.status_code == 429:
            print("  [RATE LIMIT] Pause 60s", flush=True)
            time.sleep(60)
            return None
        if r.status_code != 200:
            print("  [API] " + str(r.status_code) + " sur " + endpoint, flush=True)
            return None
        data   = r.json()
        errors = data.get("errors", {})
        if errors and errors != [] and errors != {}:
            print("  [API] Erreur: " + str(errors), flush=True)
            return None
        return data.get("response", [])
    except Exception as e:
        print("  [API] Exception: " + str(e), flush=True)
        return None


# ═══════════════════════════════════════════════════════
# DONNEES LIVE
# ═══════════════════════════════════════════════════════

def get_live_fixtures():
    """
    GET /fixtures?live=all
    Structure reponse confirmee :
      fixture.id, fixture.status.{elapsed, short}, fixture.timestamp
      league.{id, name}
      teams.{home, away}.{id, name}
      goals.{home, away} -> int ou null
    """
    data = api_get("/fixtures", {"live": "all"})
    if not data:
        return []
    filtered = [f for f in data if f.get("league", {}).get("id") in LEAGUES]
    print("  [LIVE] " + str(len(data)) + " total, " + str(len(filtered)) + " nos ligues", flush=True)
    return filtered


def get_fixture_stats_and_events(fixture_id):
    """
    Stats: GET /fixtures/statistics?fixture=ID
    Events: GET /fixtures/events?fixture=ID
    Cache 30s pour eviter appels inutiles.
    """
    key = str(fixture_id)
    if key in stats_cache:
        ts, stats, events = stats_cache[key]
        if time.time() - ts < 30:
            return stats, events

    stats  = api_get("/fixtures/statistics", {"fixture": fixture_id}) or []
    events = api_get("/fixtures/events",     {"fixture": fixture_id}) or []
    stats_cache[key] = (time.time(), stats, events)
    return stats, events


def extract_stat(stats_resp, team_id, stat_type):
    """
    Extrait valeur d'une stat pour une equipe.
    stats_resp = [{team:{id}, statistics:[{type, value}]}, ...]
    """
    for block in stats_resp:
        if block.get("team", {}).get("id") != team_id:
            continue
        for s in block.get("statistics", []):
            if s.get("type") != stat_type:
                continue
            val = s.get("value")
            if val is None:
                return 0.0
            if isinstance(val, str):
                val = val.replace("%", "").strip()
            try:
                return float(val)
            except (ValueError, TypeError):
                return 0.0
    return 0.0


def get_minute(fixture):
    """
    fixture.status.elapsed = minute cumulative du match (int).
    C'est le champ officiel API-Football pour la minute.
    """
    try:
        elapsed = fixture.get("fixture", {}).get("status", {}).get("elapsed")
        if elapsed is not None and int(elapsed) > 0:
            return int(elapsed)
    except Exception:
        pass
    try:
        ts = fixture.get("fixture", {}).get("timestamp", 0)
        if ts and ts > 0:
            e = int((time.time() - ts) / 60)
            if 1 <= e <= 130:
                return e
    except Exception:
        pass
    return 0


def get_score(fixture):
    """
    goals.home / goals.away -> int ou null -> 0 si null.
    Structure la plus simple et fiable de l'API.
    """
    try:
        g  = fixture.get("goals", {})
        hg = g.get("home")
        ag = g.get("away")
        return (int(hg) if hg is not None else 0,
                int(ag) if ag is not None else 0)
    except Exception:
        return 0, 0


# ═══════════════════════════════════════════════════════
# FORME DES EQUIPES - cache 1h
# ═══════════════════════════════════════════════════════

def get_team_form(team_id, league_id):
    """
    GET /teams/statistics?league=X&season=Y&team=Z
    Retourne directement un objet (pas une liste).
    Champs utiles:
      form: "WWDLW" (derniers matchs)
      goals.for.minute: {"0-15":{total,percentage}, "16-30":..., "76-90":...}
      goals.for.total.total: int
      goals.against.total.total: int
      fixtures.played.total: int
    """
    key = str(team_id)
    if key in form_cache:
        ts, data = form_cache[key]
        if time.time() - ts < 3600:
            return data

    result = api_get("/teams/statistics", {
        "league": league_id,
        "season": CURRENT_SEASON,
        "team":   team_id
    })

    # /teams/statistics retourne un objet unique, pas une liste
    stat = {}
    if isinstance(result, dict):
        stat = result
    elif isinstance(result, list) and result:
        stat = result[0]

    form_cache[key] = (time.time(), stat)
    return stat


def parse_form_insights(form_data, tname, minute, current_goals):
    if not form_data:
        return []
    insights = []

    # Forme "WWDLW"
    form_str = str(form_data.get("form") or "")
    last5    = form_str[-5:]
    wins     = last5.count("W")
    draws    = last5.count("D")
    losses   = last5.count("L")

    if wins >= 4:
        insights.append("\U0001f525 " + tname + " en grande forme (" + str(wins) + "V/" + str(len(last5)) + ")")
    elif wins <= 1 and losses >= 3:
        insights.append("\U0001f4c9 " + tname + " en difficulte (" + str(losses) + "D sur 5)")

    # Buts par tranche horaire
    goals_min = form_data.get("goals", {}).get("for", {}).get("minute", {})
    if goals_min:
        late_pct = 0.0
        for slot in ["76-90", "91-105"]:
            try:
                p = goals_min.get(slot, {}).get("percentage") or "0"
                late_pct += float(str(p).replace("%", ""))
            except Exception:
                pass
        early_pct = 0.0
        for slot in ["0-15", "16-30"]:
            try:
                p = goals_min.get(slot, {}).get("percentage") or "0"
                early_pct += float(str(p).replace("%", ""))
            except Exception:
                pass
        if minute >= 70 and late_pct >= 25:
            insights.append("\u23f0 " + tname + " marque souvent en fin de match (" + str(int(late_pct)) + "% de ses buts)")
        if minute < 35 and early_pct >= 30:
            insights.append("\u23f0 " + tname + " marque souvent en debut de match (" + str(int(early_pct)) + "%)")

    # Buts encaisses
    played = (form_data.get("fixtures", {}).get("played", {}) or {}).get("total") or 0
    ga_tot = (form_data.get("goals", {}).get("against", {}).get("total", {}) or {}).get("total") or 0
    if played >= 5 and ga_tot / played < 0.8:
        insights.append("\U0001f6e1\ufe0f " + tname + " defense solide (" + str(ga_tot) + " buts encaisses/" + str(played) + " matchs)")

    # Equipe n'a pas encore marque dans ce match
    gf_tot = (form_data.get("goals", {}).get("for", {}).get("total", {}) or {}).get("total") or 0
    avg_gf = gf_tot / max(played, 1)
    if current_goals == 0 and minute >= 35 and avg_gf >= 1.4:
        insights.append("\u26a0\ufe0f " + tname + " n'a pas encore marque mais " + str(round(avg_gf, 1)) + " buts/match en moy.")

    return insights


# ═══════════════════════════════════════════════════════
# MOMENTUM RECENT (events des 10 dernieres min)
# ═══════════════════════════════════════════════════════

def get_recent_momentum(events, home_id, away_id, minute):
    """
    Events API-Football:
      time.elapsed -> int (minute)
      team.id
      type: "Goal", "Card", "subst", "Var"
      detail: "Normal Goal", "Penalty", "Own Goal", "Yellow Card"...
    On pondère les buts récents.
    """
    min_start = max(0, minute - 10)
    h_score, a_score = 0, 0
    h_evts, a_evts   = [], []

    for ev in events:
        try:
            m       = int(ev.get("time", {}).get("elapsed") or 0)
            if m < min_start:
                continue
            tid     = ev.get("team", {}).get("id")
            etype   = str(ev.get("type", ""))
            detail  = str(ev.get("detail", ""))
            pts, lbl = 0, ""
            if etype == "Goal" and "Own" not in detail:
                pts, lbl = 5, "BUT \u26bd"
            elif etype == "Goal" and "Own" in detail:
                pts, lbl = 3, "CSC"
            if pts > 0:
                if tid == home_id:
                    h_score += pts
                    h_evts.append(str(m) + "' " + lbl)
                elif tid == away_id:
                    a_score += pts
                    a_evts.append(str(m) + "' " + lbl)
        except Exception:
            continue

    return h_score, a_score, h_evts[-4:], a_evts[-4:]


# ═══════════════════════════════════════════════════════
# CALCUL MOMENTUM
# ═══════════════════════════════════════════════════════

def calc_efficiency(son, tot):
    if tot < 3:
        return 0.0, "neutre", 0
    r = son / tot
    if r >= 0.50:   return r, "efficace",   +10
    elif r >= 0.30: return r, "neutre",       0
    else:           return r, "inefficace",  -8


def compute_momentum(fixture, stats_resp, events):
    home_id = fixture["teams"]["home"]["id"]
    away_id = fixture["teams"]["away"]["id"]
    minute  = get_minute(fixture)

    def sv(stype, tid):
        return extract_stat(stats_resp, tid, stype)

    h_son = sv("Shots on Goal",     home_id)
    a_son = sv("Shots on Goal",     away_id)
    h_sib = sv("Shots insidebox",   home_id)
    a_sib = sv("Shots insidebox",   away_id)
    h_tot = sv("Total Shots",       home_id)
    a_tot = sv("Total Shots",       away_id)
    h_cor = sv("Corner Kicks",      home_id)
    a_cor = sv("Corner Kicks",      away_id)
    h_sav = sv("Goalkeeper Saves",  home_id)
    a_sav = sv("Goalkeeper Saves",  away_id)
    h_xg  = sv("expected_goals",    home_id)
    a_xg  = sv("expected_goals",    away_id)
    h_pos = sv("Ball Possession",   home_id)
    a_pos = sv("Ball Possession",   away_id)

    score = 0

    # Tirs cadres (principal indicateur)
    son_t = h_son + a_son
    if son_t >= 12:   score += 25
    elif son_t >= 8:  score += 18
    elif son_t >= 5:  score += 12
    elif son_t >= 3:  score += 6

    # Tirs dans la surface
    sib_t = h_sib + a_sib
    if sib_t >= 15:   score += 18
    elif sib_t >= 10: score += 12
    elif sib_t >= 6:  score += 7
    elif sib_t >= 3:  score += 3

    # Corners
    cor_t = h_cor + a_cor
    if cor_t >= 12:   score += 10
    elif cor_t >= 8:  score += 7
    elif cor_t >= 5:  score += 4
    elif cor_t >= 2:  score += 2

    # Arrets gardien (pression offensive)
    sav_t = h_sav + a_sav
    if sav_t >= 8:    score += 8
    elif sav_t >= 5:  score += 5
    elif sav_t >= 3:  score += 2

    # xG (tres fiable quand disponible)
    xg_t  = h_xg + a_xg
    xg_ok = xg_t > 0
    if xg_ok:
        if xg_t >= 3.5:   score += 25
        elif xg_t >= 2.5: score += 20
        elif xg_t >= 1.5: score += 14
        elif xg_t >= 0.8: score += 8
        elif xg_t >= 0.3: score += 4

    # Efficacite
    h_ratio, h_eff, h_bonus = calc_efficiency(h_son, h_tot)
    a_ratio, a_eff, a_bonus = calc_efficiency(a_son, a_tot)
    score += h_bonus if h_tot >= a_tot else a_bonus

    # Momentum recent (buts des 10 dernieres min)
    h_rec, a_rec, h_rev, a_rev = get_recent_momentum(events, home_id, away_id, minute)
    rec_t = h_rec + a_rec
    if rec_t >= 10:   score += 10
    elif rec_t >= 5:  score += 6
    elif rec_t >= 2:  score += 3

    return {
        "score":        min(max(score, 0), 100),
        "h_son": h_son, "a_son": a_son,
        "h_sib": h_sib, "a_sib": a_sib,
        "h_tot": h_tot, "a_tot": a_tot,
        "h_cor": h_cor, "a_cor": a_cor,
        "h_sav": h_sav, "a_sav": a_sav,
        "h_xg":  h_xg,  "a_xg":  a_xg,
        "h_pos": h_pos, "a_pos": a_pos,
        "h_eff": h_eff, "a_eff": a_eff,
        "h_ratio": h_ratio, "a_ratio": a_ratio,
        "xg_ok":  xg_ok,
        "h_rec":  h_rec,  "a_rec":  a_rec,
        "h_rev":  h_rev,  "a_rev":  a_rev,
    }


# ═══════════════════════════════════════════════════════
# SEUIL INTELLIGENT
# ═══════════════════════════════════════════════════════

def get_threshold(minute, hg, ag, h_eff, a_eff):
    base = 45
    if minute >= 82:    base -= 14
    elif minute >= 75:  base -= 10
    elif minute >= 65:  base -= 6
    elif minute >= 55:  base -= 3
    elif minute < 15:   base += 10
    elif minute < 25:   base += 5
    diff = abs(hg - ag)
    if diff == 1 and minute >= 65:   base -= 7
    elif diff == 0 and minute >= 60: base -= 4
    elif diff >= 3:                  base += 8
    dom_eff = h_eff if hg <= ag else a_eff
    if dom_eff == "inefficace":      base += 5
    elif dom_eff == "efficace":      base -= 3
    return max(35, min(base, 72))


def get_dominant(m, h_name, a_name):
    h_pwr = m["h_son"]*4 + m["h_sib"]*2 + m["h_cor"]*1.5 + m["h_xg"]*8 + m["h_sav"]*1
    a_pwr = m["a_son"]*4 + m["a_sib"]*2 + m["a_cor"]*1.5 + m["a_xg"]*8 + m["a_sav"]*1
    if h_pwr > a_pwr * 1.35:   return "home", h_name, m["h_eff"]
    elif a_pwr > h_pwr * 1.35: return "away", a_name, m["a_eff"]
    return "balanced", None, "neutre"


def eff_emoji(e):
    if e == "efficace":   return " \U0001f3af"
    if e == "inefficace": return " \U0001f4a8"
    return ""


# ═══════════════════════════════════════════════════════
# MESSAGE TELEGRAM
# ═══════════════════════════════════════════════════════

def build_message(fixture, m, threshold, h_insights, a_insights):
    league  = fixture["league"]["name"]
    h_name  = fixture["teams"]["home"]["name"]
    a_name  = fixture["teams"]["away"]["name"]
    hg, ag  = get_score(fixture)
    minute  = get_minute(fixture)
    score   = m["score"]
    total_g = hg + ag

    dom_side, dom_name, dom_eff = get_dominant(m, h_name, a_name)

    margin = score - threshold
    if margin >= 22 or score >= 78:
        lvl = "\U0001f534 ALERTE MAX"
        emj = "\U0001f534"
    elif margin >= 12 or score >= 62:
        lvl = "\U0001f7e0 FORTE PRESSION"
        emj = "\U0001f7e0"
    else:
        lvl = "\U0001f7e1 PRESSION"
        emj = "\U0001f7e1"

    filled = int(score / 10)
    gauge  = "\U0001f7e9" * filled + "\u2b1c" * (10 - filled)

    # Stats
    def stat_line(tname, son, sib, cor, tot, xg, pos, sav, eff, ratio):
        parts = []
        if son >= 1: parts.append(str(int(son)) + " tirs cadres")
        if sib >= 1: parts.append(str(int(sib)) + " tirs surface")
        if cor >= 1: parts.append(str(int(cor)) + " corners")
        if sav >= 1: parts.append(str(int(sav)) + " arrets adv.")
        if not parts:
            return ""
        pct    = str(int(ratio*100)) + "%" if tot >= 3 else "?"
        xg_str = " | xG: " + str(round(xg, 2)) if xg > 0 else ""
        pos_str = " | poss: " + str(int(pos)) + "%" if pos > 0 else ""
        return ("  \U0001f539 " + tname + ":\n"
                + "    " + " | ".join(parts) + "\n"
                + "    precision: " + pct + eff_emoji(eff) + xg_str + pos_str)

    h_line = stat_line(h_name, m["h_son"], m["h_sib"], m["h_cor"],
                       m["h_tot"], m["h_xg"], m["h_pos"], m["h_sav"],
                       m["h_eff"], m["h_ratio"])
    a_line = stat_line(a_name, m["a_son"], m["a_sib"], m["a_cor"],
                       m["a_tot"], m["a_xg"], m["a_pos"], m["a_sav"],
                       m["a_eff"], m["a_ratio"])
    stats_block = ""
    if h_line: stats_block += h_line + "\n"
    if a_line: stats_block += a_line
    if not stats_block.strip():
        stats_block = "  \u2022 Stats en cours de chargement..."

    # Momentum recent
    rec_block = ""
    if m["h_rev"] or m["a_rev"]:
        rec_block = SEP + "\n\u26a1 MOMENTUM 10 DERNIERES MIN:\n"
        if m["h_rev"]: rec_block += "  \U0001f539 " + h_name + ": " + ", ".join(m["h_rev"]) + "\n"
        if m["a_rev"]: rec_block += "  \U0001f539 " + a_name + ": " + ", ".join(m["a_rev"]) + "\n"
        if m["h_rec"] > m["a_rec"] * 1.4:
            rec_block += "  \u27a1\ufe0f " + h_name + " domine ces 10 dernieres min\n"
        elif m["a_rec"] > m["h_rec"] * 1.4:
            rec_block += "  \u27a1\ufe0f " + a_name + " domine ces 10 dernieres min\n"
        else:
            rec_block += "  \u27a1\ufe0f Pression equilibree\n"

    # Forme
    all_insights = h_insights + a_insights
    form_block   = ""
    if all_insights:
        form_block = SEP + "\n\U0001f4ca FORME & TENDANCES:\n"
        for line in all_insights:
            form_block += "  " + line + "\n"

    # Recommandations
    recs = []
    if dom_name:
        if dom_eff == "inefficace":
            recs.append("  \u2192 \u26bd " + dom_name + " domine mais imprecise")
        elif dom_eff == "efficace":
            recs.append("  \u2192 \u26bd Prochain but : " + dom_name + " (dominante + efficace) \U0001f3af")
        else:
            recs.append("  \u2192 \u26bd Prochain but : " + dom_name + " (dominante)")
    else:
        recs.append("  \u2192 \u26bd Prochain but : match ouvert - les deux peuvent marquer")

    if dom_eff != "inefficace":
        recs.append("  \u2192 \U0001f4c8 Over " + str(total_g) + ".5 buts dans le match")
        if minute < 45:
            recs.append("  \u2192 \U0001f4c8 Over 0.5 buts 1ere MT")
        elif minute < 90:
            recs.append("  \u2192 \U0001f4c8 Over 0.5 buts 2eme MT")
    else:
        recs.append("  \u2192 \U0001f4c8 Over " + str(total_g) + ".5 possible mais equipe imprecise")

    if hg == 0 and ag == 0 and minute >= 30:
        recs.append("  \u2192 \U0001f3af BTTS - 0-0 mais les deux equipes poussent")
    elif total_g >= 1 and (hg == 0 or ag == 0) and minute >= 55:
        label_0 = "Domicile" if hg == 0 else "Visiteur"
        recs.append("  \u2192 \U0001f3af BTTS - " + label_0 + " a 0 sous pression")

    if m["xg_ok"]:
        xg_t = m["h_xg"] + m["a_xg"]
        if xg_t >= 2.5:
            recs.append("  \u2192 \U0001f4d0 xG total eleve (" + str(round(xg_t, 2)) + ") - match tres offensif")
        elif xg_t < 0.5:
            recs.append("  \u2192 \U0001f4d0 xG faible (" + str(round(xg_t, 2)) + ") - prudence buts")

    rec_section = SEP + "\n\U0001f4a1 QUOI JOUER:\n" + "\n".join(recs) + "\n"

    return (emj + " " + lvl + " - BUT POTENTIEL\n"
            + SEP + "\n"
            + "\U0001f3c6 " + league + "\n"
            + "\u2694\ufe0f  " + h_name + " " + str(hg) + " - " + str(ag) + " " + a_name + "\n"
            + "\u23f1\ufe0f  " + str(minute) + "' | Momentum: " + str(score) + "/100\n"
            + gauge + "\n"
            + SEP + "\n"
            + "\U0001f4ca STATS:\n" + stats_block + "\n"
            + rec_block
            + form_block
            + rec_section
            + SEP + "\n"
            + "\u26a0\ufe0f Parie de facon responsable")


# ═══════════════════════════════════════════════════════
# BOUCLE PRINCIPALE
# ═══════════════════════════════════════════════════════

async def run_forever():
    bot        = Bot(token=TELEGRAM_TOKEN)
    loop_count = 0
    print("  BOT DEMARRE - intervalle " + str(INTERVAL) + "s", flush=True)

    while True:
        loop_count += 1
        print("\n" + "=" * 35, flush=True)
        print("  Check #" + str(loop_count), flush=True)

        try:
            fixtures = get_live_fixtures()

            if not fixtures:
                print("  Aucun match live dans nos ligues", flush=True)
            else:
                for f in fixtures:
                    try:
                        fid    = f["fixture"]["id"]
                        minute = get_minute(f)
                        hg, ag = get_score(f)
                        h_name = f["teams"]["home"]["name"]
                        a_name = f["teams"]["away"]["name"]
                        lid    = f["league"]["id"]
                        h_id   = f["teams"]["home"]["id"]
                        a_id   = f["teams"]["away"]["id"]

                        # Ignorer statuts non-live
                        s_short = f.get("fixture", {}).get("status", {}).get("short", "")
                        if s_short in ("HT", "NS", "FT", "AET", "PEN", "PST", "CANC", "ABD", "SUSP", "INT"):
                            continue

                        # Stats + Events (cache 30s - 2 req par match)
                        stats_resp, events = get_fixture_stats_and_events(fid)

                        # Calcul momentum
                        m         = compute_momentum(f, stats_resp, events)
                        score     = m["score"]
                        threshold = get_threshold(minute, hg, ag, m["h_eff"], m["a_eff"])

                        xg_str = (" xG:" + str(round(m["h_xg"],1)) + "/"
                                  + str(round(m["a_xg"],1))) if m["xg_ok"] else ""

                        print("  [" + str(minute) + "'] " + h_name + " " + str(hg) + "-"
                              + str(ag) + " " + a_name
                              + " | score=" + str(score) + " seuil=" + str(threshold)
                              + " | son=" + str(int(m["h_son"])) + "/" + str(int(m["a_son"]))
                              + " cor=" + str(int(m["h_cor"])) + "/" + str(int(m["a_cor"]))
                              + xg_str, flush=True)

                        # Alerte par fenetre de 15 minutes
                        alert_key = str(fid) + "_" + str(minute // 15)
                        if score >= threshold and alert_key not in alerts_sent:
                            alerts_sent[alert_key] = True

                            # Forme equipes (cache 1h - 2 req max)
                            h_form_raw = get_team_form(h_id, lid)
                            a_form_raw = get_team_form(a_id, lid)
                            h_insights = parse_form_insights(h_form_raw, h_name, minute, hg)
                            a_insights = parse_form_insights(a_form_raw, a_name, minute, ag)

                            msg = build_message(f, m, threshold, h_insights, a_insights)
                            await bot.send_message(chat_id=str(TELEGRAM_CHAT_ID), text=msg)
                            print("  ALERTE: " + h_name + " vs " + a_name
                                  + " [" + str(minute) + "'] score=" + str(score), flush=True)
                            await asyncio.sleep(1.5)

                    except Exception as e:
                        print("  ERREUR match: " + str(e), flush=True)
                        traceback.print_exc()

            # Nettoyage memoire
            if len(alerts_sent) > 600:
                for k in list(alerts_sent.keys())[:300]:
                    del alerts_sent[k]
            if len(stats_cache) > 80:
                for k in list(stats_cache.keys())[:40]:
                    del stats_cache[k]

        except Exception as e:
            print("  ERREUR BOUCLE: " + str(e), flush=True)
            traceback.print_exc()

        print("  Prochaine verif dans " + str(INTERVAL) + "s", flush=True)
        await asyncio.sleep(INTERVAL)


# ═══════════════════════════════════════════════════════
# ENTREE
# ═══════════════════════════════════════════════════════

if __name__ == "__main__":
    missing = []
    if not API_KEY:          missing.append("APIFOOTBALL_KEY")
    if not TELEGRAM_TOKEN:   missing.append("TELEGRAM_TOKEN")
    if not TELEGRAM_CHAT_ID: missing.append("TELEGRAM_CHAT_ID")
    if missing:
        print("VARIABLES MANQUANTES: " + ", ".join(missing), flush=True)
        print("Configurez-les dans Railway > Variables", flush=True)
        exit(1)

    print("  Config OK - " + str(len(LEAGUES)) + " ligues suivies", flush=True)
    print("  Saison: " + str(CURRENT_SEASON), flush=True)
    asyncio.run(run_forever())
