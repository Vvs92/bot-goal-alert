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


def build_message(match, m, pred, threshold):
    league  = str(match.get("league", {}).get("name", "?"))
    h_name  = str(match.get("home_team", "Dom"))
    a_name  = str(match.get("away_team", "Ext"))
    hg, ag  = get_score(match)
    minute  = get_minute(match)
    score   = m["score"]
    total_g = hg + ag

    margin = score - threshold
    if margin >= 20 or score >= 80:
        emj = "🔴🔴"
    elif margin >= 10 or score >= 62:
        emj = "🟠"
    else:
        emj = "🟡"

    bets = []
    if m["rec_over25"] or m["p_over25"] >= 65:
        bets.append("Over " + str(total_g) + ".5 (" + str(int(m["p_over25"])) + "%)")
    if m["rec_btts"] or m["p_btts"] >= 60:
        bets.append("BTTS (" + str(int(m["p_btts"])) + "%)")
    if m["favorite"] in ("H", "A") and m["rec_fav"]:
        fn = h_name if m["favorite"] == "H" else a_name
        bets.append(fn + " gagne (" + str(int(m["fav_prob"])) + "%)")
    if minute < 44:
        bets.append("Over 0.5 MT1")
    elif minute < 85:
        bets.append("Over 0.5 MT2")
    bets_str = " | ".join(bets) if bets else "Aucun signal clair"

    stats = (str(int(m["h_son"])) + "/" + str(int(m["a_son"])) + " tirs cadres"
             + " | " + str(int(m["h_cor"])) + "/" + str(int(m["a_cor"])) + " corners")

    # Indicateur adresse equipe dominante
    dom_eff = ""
    if m["h_son"] + m["h_tot"] > m["a_son"] + m["a_tot"]:
        if m["h_eff"] == "efficace":
            dom_eff = " | " + h_name[:10] + " adroit 🎯"
        elif m["h_eff"] == "inefficace":
            dom_eff = " | " + h_name[:10] + " imprecis 💨"
    elif m["a_son"] + m["a_tot"] > m["h_son"] + m["h_tot"]:
        if m["a_eff"] == "efficace":
            dom_eff = " | " + a_name[:10] + " adroit 🎯"
        elif m["a_eff"] == "inefficace":
            dom_eff = " | " + a_name[:10] + " imprecis 💨"

    lines = [
        emj + " " + league + " | " + str(minute) + "min | Momentum " + str(score) + "/100",
        h_name + " " + str(hg) + " - " + str(ag) + " " + a_name,
        "📊 " + stats + dom_eff,
        "💡 " + bets_str,
        "⚠️ Parie de facon responsable",
    ]
    return "\n".join(lines)


# ================================================================
# MOMENTUM CHIRURGICAL - remplace compute_momentum
# ================================================================
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

    hg, ag = get_score(match)
    diff   = abs(hg - ag)
    raw    = 0

    # === SIGNAL 1 : Tirs cadres (signal qualite le plus fort) ===
    # On regarde surtout l'equipe qui domine
    dom_son = max(h_son, a_son)
    if dom_son >= 8:    raw += 30
    elif dom_son >= 6:  raw += 22
    elif dom_son >= 4:  raw += 14
    elif dom_son >= 2:  raw += 7
    elif dom_son >= 1:  raw += 3

    # Equilibre ou domination nette
    son_gap = abs(h_son - a_son)
    if son_gap >= 4:    raw += 8   # domination nette = signal plus fort
    elif son_gap <= 1:  raw += 4   # match ouvert = aussi intéressant

    # === SIGNAL 2 : Volume de tirs ===
    dom_tot = max(h_tot, a_tot)
    if dom_tot >= 15:   raw += 12
    elif dom_tot >= 10: raw += 8
    elif dom_tot >= 6:  raw += 4

    # === SIGNAL 3 : Corners (pression territoriale) ===
    dom_cor = max(h_cor, a_cor)
    if dom_cor >= 8:    raw += 10
    elif dom_cor >= 5:  raw += 6
    elif dom_cor >= 3:  raw += 3

    # === SIGNAL 4 : Adresse (tirs cadres / tirs totaux) ===
    h_ratio, h_eff, _ = calc_efficiency(h_son, h_tot)
    a_ratio, a_eff, _ = calc_efficiency(a_son, a_tot)
    dom_eff_val = h_eff if h_son >= a_son else a_eff
    dom_ratio   = h_ratio if h_son >= a_son else a_ratio
    if dom_eff_val == "efficace":
        raw += 12   # equipe adroite = signal fort de but imminent
    elif dom_eff_val == "inefficace":
        raw -= 5    # beaucoup de tirs mais rate = moins fiable

    # === SIGNAL 5 : Fenetre temporelle (quand alerter) ===
    # Les meilleurs moments pour parier : 25-40' et 60-78'
    if 25 <= minute <= 40:
        raw += 10   # prime 1ere periode active
    elif 60 <= minute <= 78:
        raw += 12   # prime 2eme periode active = meilleure fenetre
    elif minute > 80:
        raw -= 20   # penalite forte apres 80' (trop tard)
    elif minute < 15:
        raw -= 10   # trop tot, pas assez d'info

    # === SIGNAL 6 : Contexte scoreline ===
    if diff == 0:
        raw += 8    # match nul = les deux equipes poussent
    elif diff == 1:
        raw += 5    # equipe qui perd pousse
    elif diff >= 3:
        raw -= 15   # match plie = peu d'interet

    # === SIGNAL 7 : ML predictions ===
    if xg_ok:
        xg_t = h_xg + a_xg
        if xg_t >= 3.0:   raw += 15
        elif xg_t >= 2.0: raw += 10
        elif xg_t >= 1.2: raw += 5
    if p_over25 >= 75:    raw += 8
    elif p_over25 >= 60:  raw += 4
    if rec_over25:        raw += 5
    if rec_btts:          raw += 3

    # === SIGNAL 8 : Activite recente (buts dans les 10 dernieres min) ===
    h_rec, a_rec, h_rev, a_rev = get_recent_activity(incidents, minute)
    rec_t = h_rec + a_rec
    if rec_t >= 10:  raw += 10
    elif rec_t >= 5: raw += 5

    return {
        "score":    min(max(raw, 0), 100),
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
# SEUIL ADAPTATIF CHIRURGICAL
# ================================================================
def get_threshold(minute, hg, ag, m):
    # Seuil de base plus strict pour reduire les fausses alertes
    base = 45

    # Fenetres optimales = seuil reduit
    if 60 <= minute <= 78:    base -= 10
    elif 25 <= minute <= 40:  base -= 6
    elif minute > 80:         base += 25   # bloquer apres 80' (cutoff 85 en aval)
    elif minute < 15:         base += 15

    diff = abs(hg - ag)
    if diff == 0:             base -= 5
    elif diff == 1:           base -= 3
    elif diff >= 3:           base += 20   # match plie, on ne joue pas

    # ML boost
    if m["p_over25"] >= 75:   base -= 5
    if m["rec_over25"]:       base -= 4
    if m["conf_pct"] >= 70:   base -= 3

    # Adresse
    if m["h_eff"] == "efficace" or m["a_eff"] == "efficace":
        base -= 4

    return max(30, min(base, 75))


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

                        if minute > 85:
                            continue
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
