"""
BOT TELEGRAM - ALERTES FOOTBALL AUTOMATIQUES
API : Bzzoiro Sports Data (sports.bzzoiro.com)
Structure confirmee doc officielle Bzzoiro.

CHAMPS EXACTS DOC BZZOIRO :
  Match:
    id, home_team, away_team
    home_score, away_score      (integer|null)
    current_minute              (integer|null)
    period                      (1T, HT, 2T, FT)
    status                      (notstarted|inprogress|1st_half|halftime|2nd_half|finished|postponed|cancelled)
    league: { id, name, country }
    live_stats: {
      home: { ball_possession, total_shots, shots_on_target,
              corner_kicks, fouls, yellow_cards, red_cards, offsides }
      away: { ... }
    }
    incidents: [
      { type: goal|card|substitution, minute, player_name, is_home (bool) }
    ]

  Prediction:
    prob_home_win, prob_draw, prob_away_win   (float 0-100)
    predicted_result                          (H|D|A)
    expected_home_goals, expected_away_goals  (float)
    prob_over_15, prob_over_25, prob_over_35  (float 0-100)
    prob_btts_yes                             (float 0-100)
    confidence                                (float 0-1)
    most_likely_score                         (string ex: 2-1)
    favorite                                  (H|A|null)
    favorite_prob                             (float|null)
    favorite_recommend, over_25_recommend     (boolean)
    btts_recommend, winner_recommend          (boolean)

VARIABLES RAILWAY :
  BZZOIRO_KEY      -> cle API Bzzoiro
  TELEGRAM_TOKEN   -> token bot Telegram
  TELEGRAM_CHAT_ID -> ton chat ID
"""

import os
import time
import asyncio
import traceback
import requests
import json
from datetime import datetime
from telegram import Bot

# ================================================================
# CONFIG
# ================================================================

BZZOIRO_KEY      = os.environ.get("BZZOIRO_KEY", "")
TELEGRAM_TOKEN   = os.environ.get("TELEGRAM_TOKEN", "")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "")

BASE_URL = "https://sports.bzzoiro.com"
HEADERS  = {"Authorization": "Token " + BZZOIRO_KEY}

INTERVAL = 45
SEP      = "━" * 22

# Statuts live exacts (doc officielle Bzzoiro)
LIVE_STATUSES = {"inprogress", "1st_half", "2nd_half", "halftime", "ht", "live", "progress", "playing", "in_play"}

# Toutes les ligues Bzzoiro (21 confirmees)
WATCHED_LEAGUES = {
    1:  "Premier League",
    2:  "Liga Portugal Betclic",
    3:  "La Liga",
    4:  "Serie A",
    5:  "Bundesliga",
    6:  "Ligue 1",
    7:  "Champions League",
    8:  "Europa League",
    9:  "Brasileirao Serie A",
    10: "Eredivisie",
    11: "Trendyol Super Lig",
    12: "Championship",
    13: "Scottish Premiership",
    14: "Belgian Pro League",
    15: "Super League Suisse",
    17: "Saudi Pro League",
    18: "MLS",
    19: "Liga MX Apertura",
    20: "Liga MX Clausura",
    22: "Parva Liga Bulgarie",
    23: "Superliga Roumanie",
    24: "Stoiximan Super League Grece",
}

# Caches
alerts_sent = {}
pred_cache  = {}
live_cache  = {}

print("=" * 55, flush=True)
print("  BOT FOOTBALL ALERTES - Bzzoiro API", flush=True)
print("  " + datetime.now().strftime("%Y-%m-%d %H:%M:%S"), flush=True)
print("=" * 55, flush=True)


# ================================================================
# APPEL API (version debug : retourne aussi le raw)
# ================================================================

def api_get(endpoint, params=None, return_raw=False):
    try:
        r = requests.get(
            BASE_URL + endpoint,
            headers=HEADERS,
            params=params or {},
            timeout=15
        )
        if r.status_code == 401:
            print("  [API] 401 - BZZOIRO_KEY invalide", flush=True)
            if return_raw:
                return None, None
            return None
        if r.status_code == 429:
            print("  [API] Rate limit - pause 30s", flush=True)
            time.sleep(30)
            if return_raw:
                return None, None
            return None
        if r.status_code != 200:
            print("  [API] HTTP " + str(r.status_code) + " " + endpoint, flush=True)
            if return_raw:
                return None, None
            return None
        data = r.json()
        if return_raw:
            return data, r.text
        return data
    except Exception as e:
        print("  [API] Exception: " + str(e), flush=True)
        if return_raw:
            return None, None
        return None


def api_get_all(endpoint, params=None, max_pages=5):
    """Recupere toutes les pages de resultats (pagination)."""
    all_results = []
    p = dict(params) if params else {}
    page = 1
    while page <= max_pages:
        p["page"] = page
        data = api_get(endpoint, p)
        if not data:
            break
        results = _extract_results(data)
        if not results:
            break
        all_results.extend(results)
        # Si pas de page suivante, on arrete
        if isinstance(data, dict) and not data.get("next"):
            break
        page += 1
    return all_results


# ================================================================
# DIAGNOSTIC AU DEMARRAGE (version renforcee avec JSON brut)
# ================================================================

def run_diagnostic():
    """Diagnostic rapide au demarrage."""
    lines = []
    lines.append("⚙️ API OK | " + datetime.now().strftime("%H:%M:%S"))

    # Matchs live
    live = api_get_all("/api/live/")
    lines.append("🔴 Live: " + str(len(live)) + " matchs")
    for m in live[:5]:
        lines.append("  " + _match_summary(m))

    # Total events aujourd'hui
    d = api_get("/api/events/")
    if d:
        total = d.get("count", len(_extract_results(d)))
        statuts = {}
        for m in _extract_results(d):
            st = str(m.get("status", "?"))
            statuts[st] = statuts.get(st, 0) + 1
        lines.append("📅 Events today: " + str(total))
        lines.append("   Statuts: " + str(statuts))
    else:
        lines.append("❌ /api/events/ ECHEC")

    lines.append("🏆 " + str(len(WATCHED_LEAGUES)) + " ligues surveillees")
    return "\n".join(lines)


def _extract_results(data):
    """Extrait la liste de matchs peu importe la structure JSON."""
    if isinstance(data, list):
        return data
    if isinstance(data, dict):
        for key in ("results", "data", "matches", "events"):
            v = data.get(key)
            if isinstance(v, list):
                return v
    return []


def _match_summary(m):
    lg  = m.get("league", {})
    lid = lg.get("id", "?") if isinstance(lg, dict) else str(lg)
    lnm = lg.get("name", "?") if isinstance(lg, dict) else "?"
    return ("id=" + str(lid)
            + " [" + str(lnm)[:12] + "] "
            + str(m.get("home_team","?"))[:10]
            + " vs " + str(m.get("away_team","?"))[:10]
            + " st=" + str(m.get("status","?"))
            + " min=" + str(m.get("current_minute","?")))


# ================================================================
# RECUPERATION MATCHS LIVE (robuste : detecte la bonne cle JSON)
# ================================================================

def get_live_matches():
    key = "live"
    if key in live_cache:
        ts, cached = live_cache[key]
        if time.time() - ts < 30:
            return cached

    results = []

    # Tentative 1 : /api/live/ (toutes pages)
    r = api_get_all("/api/live/")
    print("  [LIVE] /api/live/ -> " + str(len(r)) + " matchs", flush=True)
    results.extend(r)

    # Tentative 2 : /api/events/ avec chaque statut (toutes pages)
    if not results:
        print("  [LIVE] Fallback /api/events/...", flush=True)
        for sv in ["inprogress", "1st_half", "2nd_half"]:
            r2 = api_get_all("/api/events/", {"status": sv})
            if r2:
                print("  [LIVE] events/?status=" + sv
                      + " -> " + str(len(r2)) + " matchs", flush=True)
            results.extend(r2)

    # Tentative 3 : /api/events/ sans filtre toutes pages, filtre manuel
    if not results:
        print("  [LIVE] Fallback /api/events/ sans filtre...", flush=True)
        r3 = api_get_all("/api/events/", max_pages=10)
        print("  [LIVE] /api/events/ total -> " + str(len(r3)) + " events", flush=True)
        results.extend(r3)

    total = len(results)

    # Log tous les matchs recus
    if total > 0:
        print("  [LIVE] " + str(total) + " matchs recus au total:", flush=True)
        for m in results[:10]:  # limite log a 10
            lg  = m.get("league", {})
            lid = lg.get("id", "?") if isinstance(lg, dict) else "?"
            lnm = lg.get("name", "?") if isinstance(lg, dict) else "?"
            print("    lid=" + str(lid) + " [" + str(lnm)[:12] + "] "
                  + str(m.get("home_team","?"))[:10]
                  + " vs " + str(m.get("away_team","?"))[:10]
                  + " | st='" + str(m.get("status","")) + "'"
                  + " min=" + str(m.get("current_minute",""))
                  + " per=" + str(m.get("period","")),
                  flush=True)
    else:
        print("  [LIVE] 0 matchs recus", flush=True)

    # Filtre sur ligues + statuts live
    filtered = []
    for m in results:
        lg      = m.get("league", {})
        lid     = lg.get("id") if isinstance(lg, dict) else None
        status  = str(m.get("status", "")).lower().strip()
        minute  = m.get("current_minute")
        period  = str(m.get("period", "")).upper()

        is_live = (
            status in LIVE_STATUSES
            or (minute is not None and str(minute).isdigit() and int(minute) > 0)
            or period in ("1T", "2T", "HT")
        )

        if lid in WATCHED_LEAGUES and is_live:
            filtered.append(m)

    live_cache[key] = (time.time(), filtered)
    print("  [LIVE] " + str(len(filtered)) + "/" + str(total)
          + " retenus", flush=True)
    return filtered


# ================================================================
# PREDICTIONS ML
# ================================================================

def get_prediction(event_id):
    key = str(event_id)
    if key in pred_cache:
        ts, pred = pred_cache[key]
        if time.time() - ts < 600:
            return pred
    d = api_get("/api/predictions/", {"event": event_id})
    if not d:
        pred_cache[key] = (time.time(), {})
        return {}
    results = _extract_results(d)
    pred    = results[0] if results else {}
    pred_cache[key] = (time.time(), pred)
    return pred


# ================================================================
# EXTRACTION DONNEES
# ================================================================

def sf(val, default=0.0):
    if val is None:
        return default
    if isinstance(val, bool):
        return 1.0 if val else 0.0
    if isinstance(val, str):
        val = val.replace("%", "").strip()
        if not val:
            return default
    try:
        return float(val)
    except (ValueError, TypeError):
        return default


def get_score(match):
    hg = match.get("home_score")
    ag = match.get("away_score")
    return (int(hg) if hg is not None else 0,
            int(ag) if ag is not None else 0)


def get_minute(match):
    m = match.get("current_minute")
    if m is not None:
        try:
            v = int(m)
            if 1 <= v <= 130:
                return v
        except (ValueError, TypeError):
            pass
    period = str(match.get("period", "")).upper()
    if period == "HT":  return 45
    if period == "FT":  return 90
    return 0


def get_live_stat(match, side, key):
    ls = match.get("live_stats")
    if not isinstance(ls, dict):
        return 0.0
    sd = ls.get(side)
    if not isinstance(sd, dict):
        return 0.0
    return sf(sd.get(key))


def get_incidents(match):
    inc = match.get("incidents")
    return inc if isinstance(inc, list) else []


# ================================================================
# ANALYSE INCIDENTS
# is_home (bool) : True = domicile, False = visiteur
# ================================================================

def get_goal_scorers(incidents):
    home_sc, away_sc = [], []
    for inc in incidents:
        try:
            if str(inc.get("type", "")).lower() != "goal":
                continue
            minute  = int(inc.get("minute") or 0)
            player  = str(inc.get("player_name") or "?")
            is_home = inc.get("is_home")
            parts   = player.strip().split()
            short   = (parts[0][0] + ". " + " ".join(parts[1:])) if len(parts) > 1 else player
            entry   = short + " " + str(minute) + "'"
            if is_home:
                home_sc.append(entry)
            else:
                away_sc.append(entry)
        except Exception:
            continue
    return home_sc, away_sc


def get_recent_activity(incidents, minute):
    min_start = max(0, minute - 12)
    h_pts, a_pts   = 0, 0
    h_evts, a_evts = [], []
    for inc in incidents:
        try:
            m       = int(inc.get("minute") or 0)
            if m < min_start:
                continue
            itype   = str(inc.get("type", "")).lower()
            is_home = inc.get("is_home")
            if itype == "goal":
                entry = str(m) + "' ⚽ BUT"
                if is_home:
                    h_pts += 5
                    h_evts.append(entry)
                else:
                    a_pts += 5
                    a_evts.append(entry)
        except Exception:
            continue
    return h_pts, a_pts, h_evts[-3:], a_evts[-3:]


# ================================================================
# CALCUL MOMENTUM (0-100)
# ================================================================

def calc_efficiency(shots_on, shots_total):
    if shots_total < 3:
        return 0.0, "neutre", 0
    r = shots_on / shots_total
    if r >= 0.50:   return r, "efficace",   +10
    elif r >= 0.30: return r, "neutre",       0
    else:           return r, "inefficace",  -8


def compute_momentum(match, pred):
    minute    = get_minute(match)
    incidents = get_incidents(match)

    h_son = get_live_stat(match, "home", "shots_on_target")
    a_son = get_live_stat(match, "away", "shots_on_target")
    h_tot = get_live_stat(match, "home", "total_shots")
    a_tot = get_live_stat(match, "away", "total_shots")
    h_cor = get_live_stat(match, "home", "corner_kicks")
    a_cor = get_live_stat(match, "away", "corner_kicks")
    h_pos = get_live_stat(match, "home", "ball_possession")
    a_pos = get_live_stat(match, "away", "ball_possession")

    h_xg  = sf(pred.get("expected_home_goals"))
    a_xg  = sf(pred.get("expected_away_goals"))
    xg_ok = (h_xg + a_xg) > 0.05

    p_over15 = sf(pred.get("prob_over_15"))
    p_over25 = sf(pred.get("prob_over_25"))
    p_over35 = sf(pred.get("prob_over_35"))
    p_btts   = sf(pred.get("prob_btts_yes"))
    p_home   = sf(pred.get("prob_home_win"))
    p_draw   = sf(pred.get("prob_draw"))
    p_away   = sf(pred.get("prob_away_win"))

    conf_raw = sf(pred.get("confidence"))
    conf_pct = conf_raw * 100 if conf_raw <= 1.0 else conf_raw

    rec_over25 = bool(pred.get("over_25_recommend"))
    rec_btts   = bool(pred.get("btts_recommend"))
    rec_fav    = bool(pred.get("favorite_recommend"))
    favorite   = str(pred.get("favorite") or "")
    fav_prob   = sf(pred.get("favorite_prob"))
    mls        = str(pred.get("most_likely_score") or "")

    raw = 0

    # Tirs cadres
    son_t = h_son + a_son
    if son_t >= 14:   raw += 22
    elif son_t >= 10: raw += 17
    elif son_t >= 7:  raw += 12
    elif son_t >= 4:  raw += 7
    elif son_t >= 2:  raw += 3

    # Tirs totaux
    tot_t = h_tot + a_tot
    if tot_t >= 25:   raw += 12
    elif tot_t >= 18: raw += 9
    elif tot_t >= 12: raw += 6
    elif tot_t >= 7:  raw += 3

    # Corners
    cor_t = h_cor + a_cor
    if cor_t >= 14:   raw += 9
    elif cor_t >= 9:  raw += 7
    elif cor_t >= 6:  raw += 5
    elif cor_t >= 3:  raw += 2

    # xG ML
    xg_t = h_xg + a_xg
    if xg_ok:
        if xg_t >= 4.0:   raw += 22
        elif xg_t >= 3.0: raw += 18
        elif xg_t >= 2.0: raw += 14
        elif xg_t >= 1.2: raw += 9
        elif xg_t >= 0.6: raw += 5
        elif xg_t >= 0.2: raw += 2

    # Over 2.5 ML
    if p_over25 >= 85:   raw += 10
    elif p_over25 >= 70: raw += 7
    elif p_over25 >= 55: raw += 4
    elif p_over25 >= 40: raw += 1

    # Recommandations ML
    if rec_over25: raw += 6
    if rec_btts:   raw += 4

    # Efficacite
    h_ratio, h_eff, h_bonus = calc_efficiency(h_son, h_tot)
    a_ratio, a_eff, a_bonus = calc_efficiency(a_son, a_tot)
    raw += h_bonus if h_tot >= a_tot else a_bonus

    # Activite recente
    h_rec, a_rec, h_rev, a_rev = get_recent_activity(incidents, minute)
    rec_t = h_rec + a_rec
    if rec_t >= 10:   raw += 8
    elif rec_t >= 5:  raw += 5
    elif rec_t >= 2:  raw += 2

    return {
        "score":    min(raw, 100),
        "h_son": h_son,  "a_son": a_son,
        "h_tot": h_tot,  "a_tot": a_tot,
        "h_cor": h_cor,  "a_cor": a_cor,
        "h_pos": h_pos,  "a_pos": a_pos,
        "h_xg":  h_xg,   "a_xg":  a_xg,
        "xg_ok": xg_ok,
        "p_over15": p_over15, "p_over25": p_over25,
        "p_over35": p_over35, "p_btts":   p_btts,
        "p_home":   p_home,   "p_draw":   p_draw,
        "p_away":   p_away,   "conf_pct": conf_pct,
        "mls":        mls,
        "favorite":   favorite,
        "fav_prob":   fav_prob,
        "rec_over25": rec_over25,
        "rec_btts":   rec_btts,
        "rec_fav":    rec_fav,
        "h_eff": h_eff,  "a_eff": a_eff,
        "h_ratio": h_ratio, "a_ratio": a_ratio,
        "h_rec": h_rec,  "a_rec": a_rec,
        "h_rev": h_rev,  "a_rev": a_rev,
    }


# ================================================================
# SEUIL ADAPTATIF
# ================================================================

def get_threshold(minute, hg, ag, m):
    base = 38

    if minute >= 83:    base -= 16
    elif minute >= 76:  base -= 12
    elif minute >= 68:  base -= 8
    elif minute >= 58:  base -= 5
    elif minute >= 47:  base -= 2
    elif minute < 12:   base += 12
    elif minute < 22:   base += 7
    elif minute < 32:   base += 3

    diff = abs(hg - ag)
    if diff == 0:
        if minute >= 70:   base -= 8
        elif minute >= 55: base -= 4
    elif diff == 1:
        if minute >= 70:   base -= 8
        elif minute >= 55: base -= 5
    elif diff == 2:        base += 4
    elif diff >= 3:        base += 12

    if m["p_over25"] >= 75:   base -= 6
    elif m["p_over25"] >= 60: base -= 3
    if m["rec_over25"]:       base -= 5
    if m["conf_pct"] >= 70:   base -= 4
    elif m["conf_pct"] >= 50: base -= 2

    dom_eff = m["h_eff"] if hg <= ag else m["a_eff"]
    if dom_eff == "efficace":     base -= 4
    elif dom_eff == "inefficace": base += 5

    return max(28, min(base, 68))


# ================================================================
# DOMINANT
# ================================================================

def get_dominant(m, h_name, a_name):
    h_pwr = m["h_son"]*5 + m["h_tot"]*2 + m["h_cor"]*2 + m["h_xg"]*12
    a_pwr = m["a_son"]*5 + m["a_tot"]*2 + m["a_cor"]*2 + m["a_xg"]*12
    if h_pwr > a_pwr * 1.3:   return "home", h_name, m["h_eff"]
    elif a_pwr > h_pwr * 1.3: return "away", a_name, m["a_eff"]
    return "balanced", None, "neutre"


def eff_str(e):
    if e == "efficace":   return " 🎯"
    if e == "inefficace": return " 💨"
    return ""


# ================================================================
# MESSAGE TELEGRAM
# ================================================================

def build_message(match, m, pred, threshold):
    league  = str(match.get("league", {}).get("name", "?"))
    h_name  = str(match.get("home_team", "Domicile"))
    a_name  = str(match.get("away_team", "Visiteur"))
    hg, ag  = get_score(match)
    minute  = get_minute(match)
    score   = m["score"]
    total_g = hg + ag
    incidents = get_incidents(match)

    dom_side, dom_name, dom_eff = get_dominant(m, h_name, a_name)

    margin = score - threshold
    if margin >= 20 or score >= 80:
        lvl, emj = "🔴 ALERTE MAX", "🔴"
    elif margin >= 10 or score >= 62:
        lvl, emj = "🟠 FORTE PRESSION", "🟠"
    else:
        lvl, emj = "🟡 PRESSION", "🟡"

    filled = int(score / 10)
    gauge  = "🟩" * filled + "⬜" * (10 - filled)

    # Buteurs
    h_sc, a_sc = get_goal_scorers(incidents)
    scorers_block = ""
    if h_sc or a_sc:
        scorers_block = (SEP + "\n⚽ BUTEURS:\n"
                         + "  🔹 " + h_name + ": "
                         + (", ".join(h_sc) if h_sc else "—") + "\n"
                         + "  🔹 " + a_name + ": "
                         + (", ".join(a_sc) if a_sc else "—") + "\n")

    # Stats
    def stat_line(tname, son, tot, cor, pos, eff, ratio):
        parts = []
        if son >= 1: parts.append(str(int(son)) + " tirs cadres")
        if tot >= 1: parts.append(str(int(tot)) + " tirs tot.")
        if cor >= 1: parts.append(str(int(cor)) + " corners")
        line = "  🔹 " + tname + ": " + (" | ".join(parts) if parts else "N/A")
        extras = []
        if tot >= 3:
            extras.append("precision " + str(int(ratio*100)) + "%" + eff_str(eff))
        if pos > 0:
            extras.append("poss. " + str(int(pos)) + "%")
        if extras:
            line += "\n    (" + " | ".join(extras) + ")"
        return line

    h_stat = stat_line(h_name, m["h_son"], m["h_tot"], m["h_cor"],
                       m["h_pos"], m["h_eff"], m["h_ratio"])
    a_stat = stat_line(a_name, m["a_son"], m["a_tot"], m["a_cor"],
                       m["a_pos"], m["a_eff"], m["a_ratio"])

    # xG
    xg_block = ""
    if m["xg_ok"]:
        xg_t = m["h_xg"] + m["a_xg"]
        bh   = "🟢" if m["h_xg"] >= m["a_xg"] else "🔴"
        ba   = "🟢" if m["a_xg"] > m["h_xg"] else "🔴"
        xg_block = (SEP + "\n📐 xG ATTENDUS (ML):\n"
                    + "  " + bh + " " + h_name + ": " + str(round(m["h_xg"],2)) + "\n"
                    + "  " + ba + " " + a_name + ": " + str(round(m["a_xg"],2)) + "\n"
                    + "  Total: " + str(round(xg_t,2)) + "\n")

    # ML
    ml_block = ""
    if pred:
        ml_lines = []
        ph, pd, pa = m["p_home"], m["p_draw"], m["p_away"]
        if ph > 0 or pd > 0 or pa > 0:
            def bar(p):
                n = int(round(p / 100 * 8))
                return "█" * n + "░" * (8 - n)
            ml_lines.append("  " + h_name[:13] + " " + str(round(ph,1)) + "% " + bar(ph))
            ml_lines.append("  Nul          " + str(round(pd,1)) + "% " + bar(pd))
            ml_lines.append("  " + a_name[:13] + " " + str(round(pa,1)) + "% " + bar(pa))

        if m["p_over15"] > 0:
            ic = "🟢" if m["p_over15"] >= 75 else "🟡"
            ml_lines.append("  " + ic + " Over 1.5: " + str(round(m["p_over15"],1)) + "%"
                            + (" ✓" if bool(pred.get("over_15_recommend")) else ""))
        if m["p_over25"] > 0:
            ic = "🟢" if m["p_over25"] >= 65 else ("🟡" if m["p_over25"] >= 45 else "🔴")
            ml_lines.append("  " + ic + " Over 2.5: " + str(round(m["p_over25"],1)) + "%"
                            + (" ✓ ML" if m["rec_over25"] else ""))
        if m["p_over35"] > 0:
            ic = "🟢" if m["p_over35"] >= 55 else "🟡"
            ml_lines.append("  " + ic + " Over 3.5: " + str(round(m["p_over35"],1)) + "%"
                            + (" ✓" if bool(pred.get("over_35_recommend")) else ""))
        if m["p_btts"] > 0:
            ic = "🟢" if m["p_btts"] >= 60 else ("🟡" if m["p_btts"] >= 45 else "🔴")
            ml_lines.append("  " + ic + " BTTS: " + str(round(m["p_btts"],1)) + "%"
                            + (" ✓ ML" if m["rec_btts"] else ""))
        if m["favorite"] in ("H", "A") and m["fav_prob"] > 0:
            fn = h_name if m["favorite"] == "H" else a_name
            ml_lines.append("  ⭐ Favori: " + fn
                            + " (" + str(round(m["fav_prob"],1)) + "%)"
                            + (" ✓ Rec." if m["rec_fav"] else ""))
        if m["mls"] and m["mls"] not in ("?", "None", ""):
            ml_lines.append("  🎯 Score probable: " + m["mls"])
        if m["conf_pct"] > 0:
            ic = "🟢" if m["conf_pct"] >= 65 else "🟡"
            ml_lines.append("  " + ic + " Confiance: " + str(round(m["conf_pct"],1)) + "%")

        if ml_lines:
            ml_block = (SEP + "\n🤖 PREDICTIONS ML (CatBoost):\n"
                        + "\n".join(ml_lines) + "\n")

    # Activite recente
    rec_block = ""
    if m["h_rev"] or m["a_rev"]:
        rec_block = SEP + "\n⚡ MOMENTUM 12 DERNIERES MIN:\n"
        if m["h_rev"]:
            rec_block += "  🔹 " + h_name + ": " + " | ".join(m["h_rev"]) + "\n"
        if m["a_rev"]:
            rec_block += "  🔹 " + a_name + ": " + " | ".join(m["a_rev"]) + "\n"
        if m["h_rec"] > m["a_rec"] * 1.4:
            rec_block += "  ➡ " + h_name + " en montee\n"
        elif m["a_rec"] > m["h_rec"] * 1.4:
            rec_block += "  ➡ " + a_name + " en montee\n"
        else:
            rec_block += "  ➡ Pression des deux cotes\n"

    # Recommandations
    recs = []
    if dom_name:
        if dom_eff == "efficace":
            recs.append("  → ⚽ Prochain but: " + dom_name + " (dom. + efficace) 🎯")
        elif dom_eff == "inefficace":
            recs.append("  → ⚽ " + dom_name + " domine mais imprecise 💨")
        else:
            recs.append("  → ⚽ Prochain but probable: " + dom_name)
    else:
        recs.append("  → ⚽ Match ouvert - buts des deux cotes")

    if m["rec_over25"]:
        recs.append("  → 📈 Over " + str(total_g) + ".5 buts (🤖 ML rec. "
                    + str(round(m["p_over25"],0)) + "%)")
    elif m["p_over25"] >= 65:
        recs.append("  → 📈 Over " + str(total_g) + ".5 buts ("
                    + str(round(m["p_over25"],0)) + "% ML)")
    else:
        recs.append("  → 📈 Over " + str(total_g) + ".5 buts dans le match")

    if minute < 44:
        recs.append("  → 📈 Over 0.5 buts 1ere MT")
    elif minute < 90:
        recs.append("  → 📈 Over 0.5 buts 2eme MT")

    if m["rec_btts"]:
        recs.append("  → 🎯 BTTS (🤖 ML rec. " + str(round(m["p_btts"],0)) + "%)")
    elif m["p_btts"] >= 60:
        recs.append("  → 🎯 BTTS (" + str(round(m["p_btts"],0)) + "% ML)")

    if m["mls"] and m["mls"] not in ("?", "None", ""):
        recs.append("  → 🎯 Score exact: " + m["mls"] + " (Poisson ML)")

    if m["xg_ok"] and (m["h_xg"] + m["a_xg"]) >= 3.5:
        recs.append("  → 📐 xG total: " + str(round(m["h_xg"]+m["a_xg"],2))
                    + " - match tres offensif")

    rec_section = SEP + "\n💡 QUOI JOUER:\n" + "\n".join(recs) + "\n"

    return (emj + " " + lvl + " - BUT POTENTIEL\n"
            + SEP + "\n"
            + "🏆 " + league + "\n"
            + "⚔  " + h_name + " 🟥 " + str(hg)
            + "  —  " + str(ag) + " 🟦 " + a_name + "\n"
            + "⏱  " + str(minute) + "' | Momentum: " + str(score) + "/100\n"
            + gauge + "\n"
            + scorers_block
            + SEP + "\n"
            + "📊 STATS LIVE:\n" + h_stat + "\n" + a_stat + "\n"
            + xg_block + ml_block + rec_block + rec_section
            + SEP + "\n"
            + "⚠ Parie de facon responsable")


# ================================================================
# BOUCLE PRINCIPALE
# ================================================================

async def run_forever():
    bot        = Bot(token=TELEGRAM_TOKEN)
    loop_count = 0

    # Diagnostic complet au demarrage
    print("  Diagnostic API en cours...", flush=True)
    diag = run_diagnostic()
    print(diag, flush=True)

    # Envoi en plusieurs messages si trop long (limite Telegram : 4096 chars)
    diag_header = "🟢 BOT DEMARRE\n" + SEP + "\n"
    full_msg    = diag_header + diag + "\n" + SEP

    chunks = []
    while len(full_msg) > 4000:
        split_at = full_msg.rfind("\n", 0, 4000)
        if split_at == -1:
            split_at = 4000
        chunks.append(full_msg[:split_at])
        full_msg = full_msg[split_at:]
    chunks.append(full_msg)

    for chunk in chunks:
        try:
            await bot.send_message(chat_id=str(TELEGRAM_CHAT_ID), text=chunk)
            await asyncio.sleep(0.5)
        except Exception as e:
            print("  Erreur msg demarrage: " + str(e), flush=True)

    while True:
        loop_count += 1
        print("\n" + "=" * 45, flush=True)
        print("  Cycle #" + str(loop_count) + " "
              + datetime.now().strftime("%H:%M:%S"), flush=True)

        try:
            matches = get_live_matches()

            if not matches:
                print("  Aucun match live dans nos ligues", flush=True)
            else:
                for match in matches:
                    try:
                        event_id = match.get("id")
                        minute   = get_minute(match)
                        hg, ag   = get_score(match)
                        h_name   = str(match.get("home_team", "?"))
                        a_name   = str(match.get("away_team", "?"))

                        pred      = get_prediction(event_id)
                        m         = compute_momentum(match, pred)
                        score     = m["score"]
                        threshold = get_threshold(minute, hg, ag, m)

                        xg_s = (" xG:" + str(round(m["h_xg"],1))
                                + "/" + str(round(m["a_xg"],1))) if m["xg_ok"] else ""
                        print("  [" + str(minute) + "'] "
                              + h_name[:11] + " " + str(hg) + "-"
                              + str(ag) + " " + a_name[:11]
                              + " | " + str(score) + "/" + str(threshold)
                              + " | son=" + str(int(m["h_son"])) + "/"
                              + str(int(m["a_son"]))
                              + " cor=" + str(int(m["h_cor"])) + "/"
                              + str(int(m["a_cor"]))
                              + xg_s, flush=True)

                        alert_key = str(event_id) + "_" + str(minute // 15)
                        if score >= threshold and alert_key not in alerts_sent:
                            alerts_sent[alert_key] = True
                            msg = build_message(match, m, pred, threshold)
                            await bot.send_message(
                                chat_id=str(TELEGRAM_CHAT_ID),
                                text=msg
                            )
                            print("  ALERTE: " + h_name + " vs " + a_name
                                  + " [" + str(minute) + "'] score="
                                  + str(score), flush=True)
                            await asyncio.sleep(1.5)

                    except Exception as e:
                        print("  ERREUR match: " + str(e), flush=True)
                        traceback.print_exc()

            # Nettoyage memoire
            if len(alerts_sent) > 800:
                for k in list(alerts_sent.keys())[:400]:
                    del alerts_sent[k]
            if len(pred_cache) > 300:
                for k in list(pred_cache.keys())[:150]:
                    del pred_cache[k]
            live_cache.clear()

        except Exception as e:
            print("  ERREUR BOUCLE: " + str(e), flush=True)
            traceback.print_exc()
            await asyncio.sleep(10)

        await asyncio.sleep(INTERVAL)


# ================================================================
# ENTREE
# ================================================================

if __name__ == "__main__":
    missing = []
    if not BZZOIRO_KEY:      missing.append("BZZOIRO_KEY")
    if not TELEGRAM_TOKEN:   missing.append("TELEGRAM_TOKEN")
    if not TELEGRAM_CHAT_ID: missing.append("TELEGRAM_CHAT_ID")
    if missing:
        print("VARIABLES MANQUANTES: " + ", ".join(missing), flush=True)
        exit(1)
    print("  Config OK - " + str(len(WATCHED_LEAGUES))
          + " ligues surveillees", flush=True)
    asyncio.run(run_forever())
