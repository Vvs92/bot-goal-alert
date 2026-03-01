"""
╔══════════════════════════════════════════════════════════════╗
║   BOT TELEGRAM - ALERTES FOOTBALL PREMIUM                    ║
║   API : Bzzoiro Sports Data (sports.bzzoiro.com)             ║
║   100% gratuit · Illimite · ML CatBoost · xG · Cotes live   ║
╚══════════════════════════════════════════════════════════════╝

VARIABLES RAILWAY :
  BZZOIRO_KEY      → ta cle API Bzzoiro (register sur sports.bzzoiro.com)
  TELEGRAM_TOKEN   → token bot Telegram (@BotFather)
  TELEGRAM_CHAT_ID → ton chat ID (@userinfobot)

STRUCTURE API BZZOIRO CONFIRMEE (doc officielle) :

  GET /api/live/
  Authorization: Token KEY
  → { count: int, results: [ LiveMatch, ... ] }

  LiveMatch : {
    id           : int,
    home_team    : str,
    away_team    : str,
    home_score   : int|null,
    away_score   : int|null,
    minute       : int|null,
    status       : "live"|"finished"|"notstarted",
    league       : { id: int, name: str, country: str },
    statistics   : {
        home: { shots_on_goal, shots_insidebox, shots_total,
                corners, ball_possession, saves,
                dangerous_attacks, fouls, offsides },
        away: { ... }
    },
    incidents    : [
        { type: "goal"|"card"|"substitution",
          team: "home"|"away",
          minute: int,
          player: str,
          detail: str }
    ]
  }

  GET /api/predictions/?event=ID
  → { count, results: [ {
      prob_home_win, prob_draw, prob_away_win,
      prob_over_15, prob_over_25, prob_over_35,
      prob_btts_yes, prob_btts_no,
      predicted_result: "H"|"D"|"A",
      confidence: float (0-100),
      expected_goals_home: float,
      expected_goals_away: float,
      most_likely_score: str   (ex: "2-1")
  } ] }

  GET /api/events/?status=live
  → meme structure mais sans stats live (utiliser /api/live/)

  Cotes sur /api/events/ dans chaque evenement :
  odds: { home: float, draw: float, away: float,
          over_15: float, under_15: float,
          over_25: float, under_25: float,
          over_35: float, under_35: float,
          btts_yes: float, btts_no: float }
"""

import os
import time
import json
import asyncio
import traceback
import requests
from datetime import datetime
from telegram import Bot

# ═══════════════════════════════════════════════════════════════
# CONFIGURATION
# ═══════════════════════════════════════════════════════════════

BZZOIRO_KEY      = os.environ.get("BZZOIRO_KEY", "")
TELEGRAM_TOKEN   = os.environ.get("TELEGRAM_TOKEN", "")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "")

BASE_URL  = "https://sports.bzzoiro.com"
HEADERS   = {"Authorization": "Token " + BZZOIRO_KEY}

INTERVAL  = 45    # secondes entre chaque cycle
SEP       = "\u2501" * 22

print("=" * 55, flush=True)
print("  BOT FOOTBALL PREMIUM v7 - Bzzoiro API", flush=True)
print("  " + datetime.now().strftime("%Y-%m-%d %H:%M:%S"), flush=True)
print("=" * 55, flush=True)

# ── Ligues Bzzoiro (IDs confirmes sur sports.bzzoiro.com/leagues/) ──
WATCHED_LEAGUES = {
    1:  "Premier League",
    3:  "La Liga",
    4:  "Serie A",
    5:  "Bundesliga",
    6:  "Ligue 1",
    7:  "Champions League",
    8:  "Europa League",
    10: "Eredivisie",
    11: "Trendyol Super Lig",
    12: "Championship",
    14: "Belgian Pro League",
    17: "Saudi Pro League",
    18: "MLS",
    # Ligues bonus disponibles sur Bzzoiro
    2:  "Liga Portugal",
    9:  "Brasileirao Serie A",
    15: "Super League (Suisse)",
    19: "Liga MX",
}

# ── Caches ─────────────────────────────────────────────────────
alerts_sent   = {}   # "event_id_window" → True
pred_cache    = {}   # event_id → (ts, pred_dict)
live_cache    = {}   # "all" → (ts, list_matches)
discovery_done = False   # log structure JSON une seule fois


# ═══════════════════════════════════════════════════════════════
# APPELS API - robuste
# ═══════════════════════════════════════════════════════════════

def api_get(endpoint, params=None, timeout=15):
    """
    Appel API Bzzoiro.
    Retourne le JSON complet (pas seulement 'results')
    pour pouvoir acceder a tous les champs.
    """
    try:
        r = requests.get(
            BASE_URL + endpoint,
            headers=HEADERS,
            params=params or {},
            timeout=timeout
        )
        if r.status_code == 401:
            print("  [API] 401 - Cle BZZOIRO_KEY invalide", flush=True)
            return None
        if r.status_code == 429:
            print("  [API] Rate limit - pause 30s", flush=True)
            time.sleep(30)
            return None
        if r.status_code != 200:
            print("  [API] HTTP " + str(r.status_code) + " " + endpoint, flush=True)
            return None
        return r.json()
    except requests.exceptions.Timeout:
        print("  [API] Timeout " + endpoint, flush=True)
        return None
    except requests.exceptions.ConnectionError:
        print("  [API] Connexion impossible", flush=True)
        return None
    except Exception as e:
        print("  [API] Exception: " + str(e), flush=True)
        return None


def log_structure(data, label="STRUCTURE"):
    """
    Log la structure JSON recue au 1er appel.
    Permet de verifier en production que les champs sont corrects.
    """
    global discovery_done
    if discovery_done:
        return
    try:
        results = data.get("results", [])
        if results:
            sample = results[0]
            print("\n  [DISCOVERY] " + label + ":", flush=True)
            print("  Champs top-level: " + str(list(sample.keys())), flush=True)
            if "statistics" in sample:
                stats = sample["statistics"]
                if isinstance(stats, dict):
                    home_keys = list(stats.get("home", {}).keys())
                    print("  statistics.home keys: " + str(home_keys), flush=True)
            if "incidents" in sample and sample["incidents"]:
                inc = sample["incidents"][0]
                print("  incidents[0] keys: " + str(list(inc.keys())), flush=True)
            if "odds" in sample:
                print("  odds keys: " + str(list(sample["odds"].keys())), flush=True)
            print("  [DISCOVERY] FIN\n", flush=True)
        discovery_done = True
    except Exception as e:
        print("  [DISCOVERY] Erreur: " + str(e), flush=True)


# ═══════════════════════════════════════════════════════════════
# RECUPERATION DES MATCHS LIVE
# ═══════════════════════════════════════════════════════════════

def get_live_matches():
    """
    GET /api/live/
    Cache 30s pour eviter appels inutiles entre les cycles de 45s.
    Filtre sur nos ligues.
    """
    key = "all"
    if key in live_cache:
        ts, cached = live_cache[key]
        if time.time() - ts < 30:
            return cached

    data = api_get("/api/live/")
    if not data:
        return []

    # Log structure au 1er appel
    log_structure(data, "LIVE")

    results  = data.get("results", [])
    filtered = []
    for m in results:
        lid = None
        league_obj = m.get("league")
        if isinstance(league_obj, dict):
            lid = league_obj.get("id")
        elif isinstance(league_obj, int):
            lid = league_obj
        if lid in WATCHED_LEAGUES:
            filtered.append(m)

    live_cache[key] = (time.time(), filtered)
    total = len(results)
    print("  [LIVE] " + str(total) + " matchs, "
          + str(len(filtered)) + " dans nos ligues", flush=True)
    return filtered


# ═══════════════════════════════════════════════════════════════
# PREDICTIONS ML
# ═══════════════════════════════════════════════════════════════

def get_prediction(event_id):
    """
    GET /api/predictions/?event=ID
    Cache 10 min (les predictions ML ne changent pas souvent).
    """
    key = str(event_id)
    if key in pred_cache:
        ts, pred = pred_cache[key]
        if time.time() - ts < 600:
            return pred

    data = api_get("/api/predictions/", {"event": event_id})
    if not data:
        pred_cache[key] = (time.time(), {})
        return {}

    results = data.get("results", [])
    pred    = results[0] if results else {}
    pred_cache[key] = (time.time(), pred)
    return pred


# ═══════════════════════════════════════════════════════════════
# EXTRACTION SECURISEE DES DONNEES
# ═══════════════════════════════════════════════════════════════

def safe_float(val, default=0.0):
    """Convertit une valeur en float de facon securisee."""
    if val is None:
        return default
    if isinstance(val, str):
        val = val.replace("%", "").replace(",", ".").strip()
        if not val:
            return default
    try:
        return float(val)
    except (ValueError, TypeError):
        return default


def get_score(match):
    """Score du match. home_score/away_score -> int, null = 0."""
    hg = match.get("home_score")
    ag = match.get("away_score")
    return (int(hg) if hg is not None else 0,
            int(ag) if ag is not None else 0)


def get_minute(match):
    """Minute actuelle. Champ 'minute' de l'API Bzzoiro."""
    m = match.get("minute")
    if m is not None:
        try:
            v = int(m)
            if 1 <= v <= 130:
                return v
        except (ValueError, TypeError):
            pass
    return 0


def get_stat(match, side, key):
    """
    Extrait stat depuis match.statistics.home/away.
    Gere les cas: statistics absent, side absent, valeur null/str.
    """
    stats = match.get("statistics")
    if not isinstance(stats, dict):
        return 0.0
    side_data = stats.get(side)
    if not isinstance(side_data, dict):
        return 0.0
    return safe_float(side_data.get(key))


def get_odds_from_match(match):
    """
    Cotes depuis le champ 'odds' si present dans le match.
    Structure: { home, draw, away, over_25, under_25, btts_yes, btts_no }
    """
    odds = match.get("odds")
    if not isinstance(odds, dict):
        return {}
    return {k: safe_float(v) for k, v in odds.items()}


def get_incidents(match):
    """Retourne la liste des incidents, toujours une liste."""
    inc = match.get("incidents")
    if isinstance(inc, list):
        return inc
    return []


# ═══════════════════════════════════════════════════════════════
# ANALYSE DES INCIDENTS
# ═══════════════════════════════════════════════════════════════

def get_goal_scorers(incidents):
    """
    Extrait les buteurs depuis les incidents.
    Incident: { type, team, minute, player, detail }
    """
    home_scorers, away_scorers = [], []
    for inc in incidents:
        try:
            if str(inc.get("type", "")).lower() != "goal":
                continue
            detail = str(inc.get("detail", "")).lower()
            if "own" in detail:
                continue
            minute = int(inc.get("minute") or 0)
            player = str(inc.get("player") or "?")
            # Prenom initial + Nom
            parts  = player.strip().split()
            short  = (parts[0][0] + ". " + " ".join(parts[1:])) if len(parts) > 1 else player
            team   = str(inc.get("team", "")).lower()
            entry  = short + " " + str(minute) + "'"
            if team == "home":
                home_scorers.append(entry)
            elif team == "away":
                away_scorers.append(entry)
        except Exception:
            continue
    return home_scorers, away_scorers


def get_recent_activity(incidents, minute):
    """
    Analyse les incidents des 12 dernieres minutes.
    Retourne score d'activite home/away + labels evenements.
    """
    window    = 12
    min_start = max(0, minute - window)
    h_pts, a_pts   = 0, 0
    h_evts, a_evts = [], []

    for inc in incidents:
        try:
            m    = int(inc.get("minute") or 0)
            if m < min_start:
                continue
            team   = str(inc.get("team", "")).lower()
            itype  = str(inc.get("type", "")).lower()
            detail = str(inc.get("detail", "")).lower()

            pts, lbl = 0, ""
            if itype == "goal" and "own" not in detail:
                pts, lbl = 5, "\u26bd BUT"
            elif itype == "goal":
                pts, lbl = 3, "CSC"

            if pts > 0:
                entry = str(m) + "' " + lbl
                if team == "home":
                    h_pts += pts
                    h_evts.append(entry)
                elif team == "away":
                    a_pts += pts
                    a_evts.append(entry)
        except Exception:
            continue

    return h_pts, a_pts, h_evts[-3:], a_evts[-3:]


# ═══════════════════════════════════════════════════════════════
# CALCUL DU SCORE MOMENTUM (0-100)
# Algorithme multi-criteres ponderes
# ═══════════════════════════════════════════════════════════════

def calc_efficiency(shots_on, shots_total):
    if shots_total < 3:
        return 0.0, "neutre", 0
    r = shots_on / shots_total
    if r >= 0.50:   return r, "efficace",   +10
    elif r >= 0.30: return r, "neutre",       0
    else:           return r, "inefficace",  -8


def compute_momentum(match, pred, odds):
    """
    Score de momentum 0-100 :
    Critere              Poids max
    ─────────────────────────────
    Tirs cadres          22 pts
    Tirs surface         16 pts
    Corners               9 pts
    Arrets gardien        8 pts
    Attaques dang.        6 pts
    xG ML predits        22 pts
    Over 2.5 ML          10 pts
    Efficacite tirs      10 pts
    Activite recente      8 pts
    ─────────────────────────────
    TOTAL MAX           111 pts → normalise sur 100
    """
    minute    = get_minute(match)
    incidents = get_incidents(match)
    hg, ag    = get_score(match)

    # Stats live
    h_son = get_stat(match, "home", "shots_on_goal")
    a_son = get_stat(match, "away", "shots_on_goal")
    h_sib = get_stat(match, "home", "shots_insidebox")
    a_sib = get_stat(match, "away", "shots_insidebox")
    h_tot = get_stat(match, "home", "shots_total")
    a_tot = get_stat(match, "away", "shots_total")
    h_cor = get_stat(match, "home", "corners")
    a_cor = get_stat(match, "away", "corners")
    h_sav = get_stat(match, "home", "saves")
    a_sav = get_stat(match, "away", "saves")
    h_pos = get_stat(match, "home", "ball_possession")
    a_pos = get_stat(match, "away", "ball_possession")
    h_dan = get_stat(match, "home", "dangerous_attacks")
    a_dan = get_stat(match, "away", "dangerous_attacks")

    # xG depuis prediction ML
    h_xg  = safe_float(pred.get("expected_goals_home"))
    a_xg  = safe_float(pred.get("expected_goals_away"))
    xg_ok = (h_xg + a_xg) > 0.05

    # Probabilites ML
    p_over25 = safe_float(pred.get("prob_over_25"))
    p_over15 = safe_float(pred.get("prob_over_15"))
    p_btts   = safe_float(pred.get("prob_btts_yes"))
    p_home   = safe_float(pred.get("prob_home_win"))
    p_away   = safe_float(pred.get("prob_away_win"))
    p_draw   = safe_float(pred.get("prob_draw"))
    conf     = safe_float(pred.get("confidence"))
    mls      = str(pred.get("most_likely_score") or "")
    res      = str(pred.get("predicted_result") or "")

    raw = 0

    # ── Tirs cadres (indicateur #1 le plus predictif) ─────
    son_t = h_son + a_son
    if son_t >= 14:   raw += 22
    elif son_t >= 10: raw += 17
    elif son_t >= 7:  raw += 12
    elif son_t >= 4:  raw += 7
    elif son_t >= 2:  raw += 3

    # ── Tirs dans la surface ──────────────────────────────
    sib_t = h_sib + a_sib
    if sib_t >= 16:   raw += 16
    elif sib_t >= 11: raw += 12
    elif sib_t >= 7:  raw += 8
    elif sib_t >= 4:  raw += 4

    # ── Corners ───────────────────────────────────────────
    cor_t = h_cor + a_cor
    if cor_t >= 14:   raw += 9
    elif cor_t >= 9:  raw += 7
    elif cor_t >= 6:  raw += 5
    elif cor_t >= 3:  raw += 2

    # ── Arrets gardien (reflet de la pression offensive) ──
    sav_t = h_sav + a_sav
    if sav_t >= 9:    raw += 8
    elif sav_t >= 6:  raw += 6
    elif sav_t >= 4:  raw += 4
    elif sav_t >= 2:  raw += 2

    # ── Attaques dangereuses ──────────────────────────────
    dan_t = h_dan + a_dan
    if dan_t >= 70:   raw += 6
    elif dan_t >= 45: raw += 4
    elif dan_t >= 25: raw += 2

    # ── xG ML (le plus fiable quantitativement) ───────────
    xg_t = h_xg + a_xg
    if xg_ok:
        if xg_t >= 4.0:   raw += 22
        elif xg_t >= 3.0: raw += 18
        elif xg_t >= 2.0: raw += 14
        elif xg_t >= 1.2: raw += 9
        elif xg_t >= 0.6: raw += 5
        elif xg_t >= 0.2: raw += 2

    # ── Over 2.5 ML ───────────────────────────────────────
    if p_over25 >= 85:  raw += 10
    elif p_over25 >= 70: raw += 7
    elif p_over25 >= 55: raw += 4
    elif p_over25 >= 40: raw += 1

    # ── Efficacite de tir ─────────────────────────────────
    h_ratio, h_eff, h_bonus = calc_efficiency(h_son, h_tot)
    a_ratio, a_eff, a_bonus = calc_efficiency(a_son, a_tot)
    raw += h_bonus if h_tot >= a_tot else a_bonus

    # ── Activite recente (12 dernieres min) ───────────────
    h_rec, a_rec, h_rev, a_rev = get_recent_activity(incidents, minute)
    rec_t = h_rec + a_rec
    if rec_t >= 10:   raw += 8
    elif rec_t >= 5:  raw += 5
    elif rec_t >= 2:  raw += 2

    # Normalisation sur 100 (max theorique = ~111)
    score = int(min(raw * 100 / 111, 100))
    score = max(score, 0)

    return {
        "score":    score,
        "raw":      raw,
        # Stats
        "h_son": h_son,  "a_son": a_son,
        "h_sib": h_sib,  "a_sib": a_sib,
        "h_tot": h_tot,  "a_tot": a_tot,
        "h_cor": h_cor,  "a_cor": a_cor,
        "h_sav": h_sav,  "a_sav": a_sav,
        "h_pos": h_pos,  "a_pos": a_pos,
        "h_dan": h_dan,  "a_dan": a_dan,
        # xG & ML
        "h_xg":  h_xg,   "a_xg":  a_xg,
        "xg_ok": xg_ok,
        "p_over25": p_over25,
        "p_over15": p_over15,
        "p_btts":   p_btts,
        "p_home":   p_home,
        "p_draw":   p_draw,
        "p_away":   p_away,
        "conf":     conf,
        "mls":      mls,
        "res":      res,
        # Efficacite
        "h_eff": h_eff,  "a_eff": a_eff,
        "h_ratio": h_ratio, "a_ratio": a_ratio,
        # Activite recente
        "h_rec": h_rec,  "a_rec": a_rec,
        "h_rev": h_rev,  "a_rev": a_rev,
    }


# ═══════════════════════════════════════════════════════════════
# SEUIL INTELLIGENT ADAPTATIF
# ═══════════════════════════════════════════════════════════════

def get_threshold(minute, hg, ag, m):
    """
    Seuil dynamique selon :
    - Minute du match (urgence en fin de match)
    - Contexte scoreline (match serre = plus interessant)
    - Efficacite de l'equipe dominante
    - Confirmation ML (Over 2.5, confiance)
    """
    base = 45

    # Temps de jeu - urgence croissante
    if minute >= 83:    base -= 16
    elif minute >= 76:  base -= 12
    elif minute >= 68:  base -= 8
    elif minute >= 58:  base -= 5
    elif minute >= 47:  base -= 2
    elif minute < 12:   base += 14
    elif minute < 22:   base += 8
    elif minute < 32:   base += 3

    # Contexte du score
    diff = abs(hg - ag)
    total_g = hg + ag
    if diff == 0:
        if minute >= 70:  base -= 8   # 0-0 ou egalite en fin = risque max
        elif minute >= 55: base -= 4
    elif diff == 1:
        if minute >= 70:  base -= 8   # Score serre en fin
        elif minute >= 55: base -= 5
    elif diff == 2:
        base += 4                      # Match presque plié
    elif diff >= 3:
        base += 12                     # Match plié - moins d'interet

    # Mi-temps: legere hausse du seuil
    if 44 <= minute <= 47:
        base += 3

    # Confirmation ML
    if m["p_over25"] >= 75:   base -= 6
    elif m["p_over25"] >= 60: base -= 3
    if m["conf"] >= 70:       base -= 4
    elif m["conf"] >= 55:     base -= 2

    # Efficacite equipe dominante
    dom_eff = m["h_eff"] if hg <= ag else m["a_eff"]
    if dom_eff == "efficace":    base -= 4
    elif dom_eff == "inefficace": base += 5

    return max(32, min(base, 73))


# ═══════════════════════════════════════════════════════════════
# EQUIPE DOMINANTE
# ═══════════════════════════════════════════════════════════════

def get_dominant(m, h_name, a_name):
    """
    Calcule l'equipe dominante via un score de puissance pondere.
    xG est le meilleur indicateur, suivi des tirs cadres.
    """
    h_pwr = (m["h_son"] * 5 + m["h_sib"] * 3 + m["h_cor"] * 2
             + m["h_sav"] * 2 + m["h_xg"] * 12 + m["h_dan"] * 0.08)
    a_pwr = (m["a_son"] * 5 + m["a_sib"] * 3 + m["a_cor"] * 2
             + m["a_sav"] * 2 + m["a_xg"] * 12 + m["a_dan"] * 0.08)

    if h_pwr > a_pwr * 1.3:
        return "home", h_name, m["h_eff"], h_pwr, a_pwr
    elif a_pwr > h_pwr * 1.3:
        return "away", a_name, m["a_eff"], h_pwr, a_pwr
    return "balanced", None, "neutre", h_pwr, a_pwr


def eff_emoji(e):
    if e == "efficace":   return " \U0001f3af"
    if e == "inefficace": return " \U0001f4a8"
    return ""


# ═══════════════════════════════════════════════════════════════
# CONSTRUCTION DU MESSAGE TELEGRAM
# ═══════════════════════════════════════════════════════════════

def build_message(match, m, pred, odds, threshold):
    league  = str(match.get("league", {}).get("name", "Ligue ?"))
    h_name  = str(match.get("home_team", "Domicile"))
    a_name  = str(match.get("away_team", "Visiteur"))
    hg, ag  = get_score(match)
    minute  = get_minute(match)
    score   = m["score"]
    total_g = hg + ag
    incidents = get_incidents(match)

    dom_side, dom_name, dom_eff, h_pwr, a_pwr = get_dominant(m, h_name, a_name)

    # ── Niveau d'alerte ───────────────────────────────────
    margin = score - threshold
    if margin >= 20 or score >= 80:
        lvl = "\U0001f534 ALERTE MAX"
        emj = "\U0001f534"
    elif margin >= 10 or score >= 65:
        lvl = "\U0001f7e0 FORTE PRESSION"
        emj = "\U0001f7e0"
    else:
        lvl = "\U0001f7e1 PRESSION"
        emj = "\U0001f7e1"

    # ── Jauge de momentum ─────────────────────────────────
    filled = int(score / 10)
    gauge  = "\U0001f7e9" * filled + "\u2b1c" * (10 - filled)

    # ── Buteurs ───────────────────────────────────────────
    h_scorers, a_scorers = get_goal_scorers(incidents)
    scorers_block = ""
    if h_scorers or a_scorers:
        h_str = ", ".join(h_scorers) if h_scorers else "\u2014"
        a_str = ", ".join(a_scorers) if a_scorers else "\u2014"
        scorers_block = (SEP + "\n\u26bd BUTEURS:\n"
                         + "  \U0001f539 " + h_name + ": " + h_str + "\n"
                         + "  \U0001f539 " + a_name + ": " + a_str + "\n")

    # ── Stats live ────────────────────────────────────────
    def fmt_stat_line(tname, son, sib, cor, tot, sav, pos, dan, eff, ratio):
        parts = []
        if son >= 1:  parts.append(str(int(son)) + " tirs cadres")
        if sib >= 1:  parts.append(str(int(sib)) + " surface")
        if cor >= 1:  parts.append(str(int(cor)) + " corners")
        if sav >= 1:  parts.append(str(int(sav)) + " arrets adv.")
        if dan >= 8:  parts.append(str(int(dan)) + " att.dang.")
        if not parts:
            return "  \U0001f539 " + tname + ": stats non dispo"
        line = "  \U0001f539 " + tname + ": " + " | ".join(parts)
        extras = []
        if tot >= 3:
            extras.append("precis: " + str(int(ratio * 100)) + "%" + eff_emoji(eff))
        if pos > 0:
            extras.append("poss: " + str(int(pos)) + "%")
        if extras:
            line += "\n    (" + " · ".join(extras) + ")"
        return line

    h_stat = fmt_stat_line(h_name, m["h_son"], m["h_sib"], m["h_cor"],
                           m["h_tot"], m["h_sav"], m["h_pos"], m["h_dan"],
                           m["h_eff"], m["h_ratio"])
    a_stat = fmt_stat_line(a_name, m["a_son"], m["a_sib"], m["a_cor"],
                           m["a_tot"], m["a_sav"], m["a_pos"], m["a_dan"],
                           m["a_eff"], m["a_ratio"])

    # ── xG ────────────────────────────────────────────────
    xg_block = ""
    if m["xg_ok"]:
        xg_t   = m["h_xg"] + m["a_xg"]
        h_xg_s = str(round(m["h_xg"], 2))
        a_xg_s = str(round(m["a_xg"], 2))
        bar_h  = "\U0001f7e2" if m["h_xg"] > m["a_xg"] else "\U0001f534"
        bar_a  = "\U0001f7e2" if m["a_xg"] > m["h_xg"] else "\U0001f534"
        xg_block = (SEP + "\n\U0001f4d0 xG ATTENDUS (ML):\n"
                    + "  " + bar_h + " " + h_name + ": " + h_xg_s + "\n"
                    + "  " + bar_a + " " + a_name + ": " + a_xg_s + "\n"
                    + "  Total xG: " + str(round(xg_t, 2)) + "\n")

    # ── Predictions ML ────────────────────────────────────
    ml_block = ""
    if pred:
        ml_lines = []

        # Probabilites 1X2
        ph = m["p_home"]
        pd = m["p_draw"]
        pa = m["p_away"]
        if ph > 0 or pd > 0 or pa > 0:
            # Barre visuelle
            def pct_bar(p, maxlen=8):
                filled_b = int(round(p / 100 * maxlen))
                return "\u2588" * filled_b + "\u2591" * (maxlen - filled_b)
            ml_lines.append("  1X2:")
            ml_lines.append("  " + h_name[:13] + " " + str(round(ph, 1)) + "% " + pct_bar(ph))
            ml_lines.append("  Nul         " + str(round(pd, 1)) + "% " + pct_bar(pd))
            ml_lines.append("  " + a_name[:13] + " " + str(round(pa, 1)) + "% " + pct_bar(pa))

        # Over/Under
        po25 = m["p_over25"]
        po15 = m["p_over15"]
        if po25 > 0:
            ic = "\U0001f7e2" if po25 >= 65 else ("\U0001f7e1" if po25 >= 45 else "\U0001f534")
            ml_lines.append("  " + ic + " Over 2.5: " + str(round(po25, 1)) + "%")
        if po15 > 0:
            ic = "\U0001f7e2" if po15 >= 75 else "\U0001f7e1"
            ml_lines.append("  " + ic + " Over 1.5: " + str(round(po15, 1)) + "%")

        # BTTS
        pb = m["p_btts"]
        if pb > 0:
            ic = "\U0001f7e2" if pb >= 60 else ("\U0001f7e1" if pb >= 45 else "\U0001f534")
            ml_lines.append("  " + ic + " BTTS: " + str(round(pb, 1)) + "%")

        # Score le plus probable
        if m["mls"] and m["mls"] not in ("?", "None", ""):
            ml_lines.append("  \U0001f3af Score probable: " + m["mls"])

        # Confiance
        cf = m["conf"]
        if cf > 0:
            cf_bar = "\U0001f7e2" if cf >= 65 else ("\U0001f7e1" if cf >= 45 else "\U0001f534")
            ml_lines.append("  " + cf_bar + " Confiance modele: " + str(round(cf, 1)) + "%")

        if ml_lines:
            ml_block = (SEP + "\n\U0001f916 PREDICTIONS ML (CatBoost v4):\n"
                        + "\n".join(ml_lines) + "\n")

    # ── Cotes live ────────────────────────────────────────
    odds_block = ""
    if odds:
        odds_lines = []
        o_h   = odds.get("home", 0)
        o_d   = odds.get("draw", 0)
        o_a   = odds.get("away", 0)
        o_o25 = odds.get("over_25", 0)
        o_u25 = odds.get("under_25", 0)
        o_bts = odds.get("btts_yes", 0)
        if o_h:   odds_lines.append("  1: @" + str(round(o_h, 2)))
        if o_d:   odds_lines.append("  X: @" + str(round(o_d, 2)))
        if o_a:   odds_lines.append("  2: @" + str(round(o_a, 2)))
        if o_o25: odds_lines.append("  Over 2.5: @" + str(round(o_o25, 2)))
        if o_u25: odds_lines.append("  Under 2.5: @" + str(round(o_u25, 2)))
        if o_bts: odds_lines.append("  BTTS: @" + str(round(o_bts, 2)))
        if odds_lines:
            odds_block = (SEP + "\n\U0001f4b9 COTES LIVE:\n"
                          + "\n".join(odds_lines) + "\n")

    # ── Activite recente ──────────────────────────────────
    rec_block = ""
    if m["h_rev"] or m["a_rev"]:
        rec_block = SEP + "\n\u26a1 MOMENTUM 12 DERNIERES MIN:\n"
        if m["h_rev"]:
            rec_block += "  \U0001f539 " + h_name + ": " + " | ".join(m["h_rev"]) + "\n"
        if m["a_rev"]:
            rec_block += "  \U0001f539 " + a_name + ": " + " | ".join(m["a_rev"]) + "\n"
        if m["h_rec"] > m["a_rec"] * 1.4:
            rec_block += "  \u27a1\ufe0f " + h_name + " en pleine montee en puissance\n"
        elif m["a_rec"] > m["h_rec"] * 1.4:
            rec_block += "  \u27a1\ufe0f " + a_name + " en pleine montee en puissance\n"
        else:
            rec_block += "  \u27a1\ufe0f Pression des deux cotes\n"

    # ── Recommandations de paris ──────────────────────────
    recs = []

    # 1. Prochain but
    if dom_name:
        if dom_eff == "efficace":
            recs.append("  \u2192 \u26bd Prochain but : "
                        + dom_name + " (dominante + efficace) \U0001f3af")
        elif dom_eff == "inefficace":
            recs.append("  \u2192 \u26bd " + dom_name
                        + " domine mais imprecise \U0001f4a8 - patience")
        else:
            recs.append("  \u2192 \u26bd Prochain but probable : " + dom_name)
    else:
        recs.append("  \u2192 \u26bd Match ouvert - les deux equipes menacent")

    # 2. Over/Under avec support ML
    if m["p_over25"] >= 70:
        recs.append("  \u2192 \U0001f4c8 Over " + str(total_g) + ".5 buts"
                    + " (\U0001f916 " + str(round(m["p_over25"], 0)) + "% ML)")
    elif dom_eff != "inefficace":
        recs.append("  \u2192 \U0001f4c8 Over " + str(total_g) + ".5 buts dans le match")

    # Over MT
    if minute < 44:
        if m["p_over15"] >= 65:
            recs.append("  \u2192 \U0001f4c8 Over 1.5 buts 1ere MT (\U0001f916 "
                        + str(round(m["p_over15"], 0)) + "% ML)")
        else:
            recs.append("  \u2192 \U0001f4c8 Over 0.5 buts 1ere MT")
    elif minute < 92:
        recs.append("  \u2192 \U0001f4c8 Over 0.5 buts 2eme MT")

    # 3. BTTS
    if m["p_btts"] >= 65:
        recs.append("  \u2192 \U0001f3af BTTS Yes (\U0001f916 "
                    + str(round(m["p_btts"], 0)) + "% ML)")
    elif hg == 0 and ag == 0 and minute >= 28:
        recs.append("  \u2192 \U0001f3af BTTS - match nul 0-0 sous pression")
    elif total_g >= 1 and (hg == 0 or ag == 0) and minute >= 52:
        equipe_0 = h_name if hg == 0 else a_name
        recs.append("  \u2192 \U0001f3af BTTS - " + equipe_0 + " n'a pas encore marque")

    # 4. Score exact ML
    if m["mls"] and m["mls"] not in ("?", "None", ""):
        recs.append("  \u2192 \U0001f3af Score exact: " + m["mls"] + " (distribution Poisson ML)")

    # 5. xG insight
    if m["xg_ok"]:
        xg_t = m["h_xg"] + m["a_xg"]
        if xg_t >= 3.5:
            recs.append("  \u2192 \U0001f4d0 xG total: " + str(round(xg_t, 2))
                        + " - match TRES offensif")
        elif xg_t >= 2.0:
            recs.append("  \u2192 \U0001f4d0 xG total: " + str(round(xg_t, 2))
                        + " - bonne opportunite offensive")

    rec_section = (SEP + "\n\U0001f4a1 QUOI JOUER:\n"
                   + "\n".join(recs) + "\n")

    # ── Assemblage final ──────────────────────────────────
    return (emj + " " + lvl + " - BUT POTENTIEL\n"
            + SEP + "\n"
            + "\U0001f3c6 " + league + "\n"
            + "\u2694\ufe0f  " + h_name + " \U0001f7e5 " + str(hg)
            + "  \u2014  " + str(ag) + " \U0001f7e6 " + a_name + "\n"
            + "\u23f1\ufe0f  " + str(minute) + "' | Momentum: "
            + str(score) + "/100\n"
            + gauge + "\n"
            + scorers_block
            + SEP + "\n"
            + "\U0001f4ca STATS LIVE:\n"
            + h_stat + "\n"
            + a_stat + "\n"
            + xg_block
            + ml_block
            + odds_block
            + rec_block
            + rec_section
            + SEP + "\n"
            + "\u26a0\ufe0f Parie de facon responsable")


# ═══════════════════════════════════════════════════════════════
# BOUCLE PRINCIPALE
# ═══════════════════════════════════════════════════════════════

async def run_forever():
    bot        = Bot(token=TELEGRAM_TOKEN)
    loop_count = 0

    # Message de demarrage
    try:
        start_msg = ("\U0001f7e2 BOT FOOTBALL DEMARRE\n"
                     + SEP + "\n"
                     + "\U0001f4cb " + str(len(WATCHED_LEAGUES)) + " ligues surveillees\n"
                     + "\u23f1\ufe0f Verification toutes les "
                     + str(INTERVAL) + "s\n"
                     + "\U0001f916 ML CatBoost v4 actif\n"
                     + "\U0001f4d0 xG + cotes live\n"
                     + SEP)
        await bot.send_message(chat_id=str(TELEGRAM_CHAT_ID), text=start_msg)
        print("  Message de demarrage envoye", flush=True)
    except Exception as e:
        print("  Erreur msg demarrage: " + str(e), flush=True)

    while True:
        loop_count += 1
        now_str = datetime.now().strftime("%H:%M:%S")
        print("\n" + "=" * 45, flush=True)
        print("  Cycle #" + str(loop_count) + " - " + now_str, flush=True)

        try:
            matches = get_live_matches()

            if not matches:
                print("  Aucun match live dans nos ligues", flush=True)
            else:
                for match in matches:
                    try:
                        event_id = match.get("id")
                        if not event_id:
                            continue

                        # Verifier statut live
                        status = str(match.get("status", "")).lower().strip()
                        # Bzzoiro: "live" pour les matchs en cours
                        # Ignorer: "notstarted", "finished", "postponed", etc.
                        if status not in ("live",):
                            continue

                        minute  = get_minute(match)
                        hg, ag  = get_score(match)
                        h_name  = str(match.get("home_team", "?"))
                        a_name  = str(match.get("away_team", "?"))
                        league  = str(match.get("league", {}).get("name", "?"))

                        # Ignorer mi-temps (minute 45 fixe)
                        if minute == 45:
                            continue

                        # Predictions ML (cache 10 min)
                        pred = get_prediction(event_id)

                        # Cotes depuis le match (si disponibles)
                        odds = get_odds_from_match(match)

                        # Calcul momentum
                        m         = compute_momentum(match, pred, odds)
                        score     = m["score"]
                        threshold = get_threshold(minute, hg, ag, m)

                        # Log de supervision
                        xg_s  = (" xG:" + str(round(m["h_xg"], 1))
                                 + "/" + str(round(m["a_xg"], 1))) if m["xg_ok"] else ""
                        ml_s  = (" O25=" + str(round(m["p_over25"], 0))
                                 + "%") if m["p_over25"] > 0 else ""
                        print("  [" + str(minute) + "'] "
                              + h_name[:12] + " " + str(hg) + "-"
                              + str(ag) + " " + a_name[:12]
                              + " | " + str(score) + "/" + str(threshold)
                              + " | son=" + str(int(m["h_son"])) + "/"
                              + str(int(m["a_son"]))
                              + " cor=" + str(int(m["h_cor"])) + "/"
                              + str(int(m["a_cor"]))
                              + xg_s + ml_s,
                              flush=True)

                        # Alerte si seuil atteint (fenetre 15 min anti-spam)
                        window    = minute // 15
                        alert_key = str(event_id) + "_" + str(window)

                        if score >= threshold and alert_key not in alerts_sent:
                            alerts_sent[alert_key] = True
                            msg = build_message(match, m, pred, odds, threshold)
                            await bot.send_message(
                                chat_id=str(TELEGRAM_CHAT_ID),
                                text=msg
                            )
                            print("  \u2705 ALERTE ENVOYEE: "
                                  + h_name + " vs " + a_name
                                  + " [" + str(minute) + "']"
                                  + " score=" + str(score),
                                  flush=True)
                            await asyncio.sleep(1.5)

                    except Exception as e:
                        eid = match.get("id", "?") if isinstance(match, dict) else "?"
                        print("  ERREUR match " + str(eid) + ": " + str(e), flush=True)
                        traceback.print_exc()

            # Nettoyage memoire periodique
            if len(alerts_sent) > 1000:
                keys = list(alerts_sent.keys())[:500]
                for k in keys:
                    del alerts_sent[k]
                print("  [MEM] Cache alertes nettoye", flush=True)

            if len(pred_cache) > 300:
                keys = list(pred_cache.keys())[:150]
                for k in keys:
                    del pred_cache[k]

            if len(live_cache) > 10:
                live_cache.clear()

        except Exception as e:
            print("  ERREUR BOUCLE: " + str(e), flush=True)
            traceback.print_exc()
            await asyncio.sleep(10)   # pause courte avant retry

        print("  Prochaine verif dans " + str(INTERVAL) + "s", flush=True)
        await asyncio.sleep(INTERVAL)


# ═══════════════════════════════════════════════════════════════
# POINT D'ENTREE
# ═══════════════════════════════════════════════════════════════

if __name__ == "__main__":
    # Verification variables d'environnement
    missing = []
    if not BZZOIRO_KEY:
        missing.append("BZZOIRO_KEY")
    if not TELEGRAM_TOKEN:
        missing.append("TELEGRAM_TOKEN")
    if not TELEGRAM_CHAT_ID:
        missing.append("TELEGRAM_CHAT_ID")

    if missing:
        print("", flush=True)
        print("  ERREUR - Variables manquantes:", flush=True)
        for v in missing:
            print("  ✗ " + v, flush=True)
        print("", flush=True)
        print("  Comment les configurer:", flush=True)
        print("  Railway > ton projet > Variables > + New Variable", flush=True)
        print("  BZZOIRO_KEY : ta cle de sports.bzzoiro.com/register/", flush=True)
        exit(1)

    print("  Config OK:", flush=True)
    print("  ✓ BZZOIRO_KEY      : " + BZZOIRO_KEY[:8] + "...", flush=True)
    print("  ✓ TELEGRAM_TOKEN   : " + TELEGRAM_TOKEN[:10] + "...", flush=True)
    print("  ✓ TELEGRAM_CHAT_ID : " + str(TELEGRAM_CHAT_ID), flush=True)
    print("  ✓ Ligues suivies   : " + str(len(WATCHED_LEAGUES)), flush=True)
    print("", flush=True)

    asyncio.run(run_forever())
