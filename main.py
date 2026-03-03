“””
BOT TELEGRAM - ALERTES FOOTBALL PREMIUM
API : Bzzoiro Sports Data (sports.bzzoiro.com)
100% gratuit, illimite, ML CatBoost

STRUCTURE CONFIRMEE DOC OFFICIELLE :

GET /api/live/
{ count, results: [ {
id, api_id,
home_team, away_team,
home_team_obj, away_team_obj,
home_score, away_score,       (integer|null)
current_minute,               (integer|null) <- CHAMP EXACT
period,                       (string: 1T, HT, 2T, FT)
status,                       (string: voir ci-dessous)
event_date,
league: { id, api_id, name, country, season_id },
incidents: [                  (array|null)
{ type,        (goal|card|substitution)
minute,
player_name, (string)
is_home      (boolean) <- PAS team string !
}
],
live_stats: {                 <- PAS statistics !
home: { ball_possession, total_shots, shots_on_target,
corner_kicks, fouls, yellow_cards, red_cards, offsides },
away: { … }
}
} ] }

STATUS VALUES (doc officielle) :
notstarted | inprogress | 1st_half | halftime | 2nd_half
finished | postponed | cancelled

GET /api/predictions/?event=ID
{ results: [ {
id, event (object), created_at,
prob_home_win, prob_draw, prob_away_win,  (float 0-100)
predicted_result,                          (H|D|A)
expected_home_goals, expected_away_goals,  (float) <- CHAMPS EXACTS
prob_over_15, prob_over_25, prob_over_35,  (float 0-100)
prob_btts_yes,                             (float 0-100)
confidence,                                (float 0-1) <- 0 a 1 pas 0-100 !
model_version,
most_likely_score,                         (string ex: “2-1”)
favorite,                                  (H|A|null)
favorite_prob,                             (float|null)
favorite_recommend,                        (boolean)
over_15_recommend, over_25_recommend,      (boolean)
over_35_recommend, btts_recommend,         (boolean)
winner_recommend                           (boolean)
} ] }

VARIABLES RAILWAY :
BZZOIRO_KEY      -> cle API (sports.bzzoiro.com/register/)
TELEGRAM_TOKEN   -> token bot (@BotFather)
TELEGRAM_CHAT_ID -> chat ID (@userinfobot)
“””

import os
import time
import asyncio
import traceback
import requests
from datetime import datetime
from telegram import Bot

# ================================================================

# CONFIGURATION

# ================================================================

BZZOIRO_KEY      = os.environ.get(“BZZOIRO_KEY”, “”)
TELEGRAM_TOKEN   = os.environ.get(“TELEGRAM_TOKEN”, “”)
TELEGRAM_CHAT_ID = os.environ.get(“TELEGRAM_CHAT_ID”, “”)

BASE_URL = “https://sports.bzzoiro.com”
HEADERS  = {“Authorization”: “Token “ + BZZOIRO_KEY}

INTERVAL = 45
SEP      = “\u2501” * 22

print(”=” * 55, flush=True)
print(”  BOT FOOTBALL PREMIUM v8 - Bzzoiro API”, flush=True)
print(”  “ + datetime.now().strftime(”%Y-%m-%d %H:%M:%S”), flush=True)
print(”=” * 55, flush=True)

# Ligues Bzzoiro (ID interne = league.id dans la reponse)

# IDs confirmes sur sports.bzzoiro.com/leagues/

WATCHED_LEAGUES = {
1:  “Premier League”,
2:  “Liga Portugal”,
3:  “La Liga”,
4:  “Serie A”,
5:  “Bundesliga”,
6:  “Ligue 1”,
7:  “Champions League”,
8:  “Europa League”,
9:  “Brasileirao Serie A”,
10: “Eredivisie”,
11: “Trendyol Super Lig”,
12: “Championship”,
14: “Belgian Pro League”,
15: “Super League Suisse”,
17: “Saudi Pro League”,
18: “MLS”,
19: “Liga MX Apertura”,
20: “Liga MX Clausura”,
22: “Parva Liga”,
23: “Superliga Roumanie”,
24: “Super League Grece”,
}

# Statuts live valides (doc officielle Bzzoiro)

LIVE_STATUSES = {“inprogress”, “1st_half”, “2nd_half”}

# halftime exclu volontairement (mi-temps = pas d’action)

# notstarted, finished, postponed, cancelled exclus

# Caches

alerts_sent  = {}
pred_cache   = {}
live_cache   = {}

# ================================================================

# APPEL API

# ================================================================

def api_get(endpoint, params=None):
try:
r = requests.get(
BASE_URL + endpoint,
headers=HEADERS,
params=params or {},
timeout=15
)
if r.status_code == 401:
print(”  [API] 401 - BZZOIRO_KEY invalide”, flush=True)
return None
if r.status_code == 429:
print(”  [API] Rate limit - pause 30s”, flush=True)
time.sleep(30)
return None
if r.status_code != 200:
print(”  [API] HTTP “ + str(r.status_code) + “ “ + endpoint, flush=True)
return None
return r.json()
except requests.exceptions.Timeout:
print(”  [API] Timeout “ + endpoint, flush=True)
return None
except Exception as e:
print(”  [API] Exception: “ + str(e), flush=True)
return None

# ================================================================

# MATCHS LIVE

# ================================================================

def get_live_matches():
“””
Essaie /api/live/ puis fallback sur /api/events/?status=inprogress
Log TOUT pour diagnostic complet.
“””
key = “live”
if key in live_cache:
ts, cached = live_cache[key]
if time.time() - ts < 30:
return cached

```
# Essai 1 : endpoint /api/live/
data = api_get("/api/live/")
results = []
if data:
    results = data.get("results", [])
    print("  [LIVE] /api/live/ -> " + str(len(results)) + " matchs", flush=True)

# Fallback : /api/events/ avec chaque statut live si live/ vide
if not results:
    print("  [LIVE] live/ vide, essai /api/events/ ...", flush=True)
    all_results = []
    for status_val in ["inprogress", "1st_half", "2nd_half"]:
        d = api_get("/api/events/", {"status": status_val})
        if d:
            r = d.get("results", [])
            print("  [LIVE] events/?status=" + status_val
                  + " -> " + str(len(r)) + " matchs", flush=True)
            all_results.extend(r)
    results = all_results

total = len(results)

# Log TOUS les matchs recus
if total == 0:
    print("  [LIVE] 0 matchs live recus au total", flush=True)
else:
    print("  [LIVE] " + str(total) + " matchs live TOTAL:", flush=True)
    for m in results:
        lg  = m.get("league", {})
        lid = lg.get("id", "?") if isinstance(lg, dict) else str(lg)
        lnm = lg.get("name", "?") if isinstance(lg, dict) else "?"
        print("    id=" + str(lid) + " [" + str(lnm) + "] "
              + str(m.get("home_team", "?"))[:10]
              + " vs " + str(m.get("away_team", "?"))[:10]
              + " status=" + str(m.get("status", "?"))
              + " min=" + str(m.get("current_minute", "?")),
              flush=True)

# Filtre : nos ligues + statuts live
filtered = []
for m in results:
    lg     = m.get("league", {})
    lid    = lg.get("id") if isinstance(lg, dict) else None
    status = str(m.get("status", "")).lower().strip()
    in_league = (lid in WATCHED_LEAGUES)
    is_live   = (status in LIVE_STATUSES)
    if not in_league:
        pass  # deja logue ci-dessus
    if in_league and is_live:
        filtered.append(m)

live_cache[key] = (time.time(), filtered)
print("  [LIVE] " + str(len(filtered)) + "/" + str(total)
      + " retenus (nos ligues + live)", flush=True)
return filtered
```

# ================================================================

# PREDICTIONS ML

# ================================================================

def get_prediction(event_id):
“””
GET /api/predictions/?event=ID
Cache 10 min.
Champs cles: expected_home_goals, expected_away_goals,
confidence (0-1), prob_* (0-100),
favorite, *_recommend (boolean)
“””
key = str(event_id)
if key in pred_cache:
ts, pred = pred_cache[key]
if time.time() - ts < 600:
return pred

```
data = api_get("/api/predictions/", {"event": event_id})
if not data:
    pred_cache[key] = (time.time(), {})
    return {}

results = data.get("results", [])
pred    = results[0] if results else {}
pred_cache[key] = (time.time(), pred)
return pred
```

# ================================================================

# EXTRACTION SECURISEE

# ================================================================

def sf(val, default=0.0):
“”“safe_float: convertit en float sans planter.”””
if val is None:
return default
if isinstance(val, bool):
return 1.0 if val else 0.0
if isinstance(val, str):
val = val.replace(”%”, “”).strip()
if not val:
return default
try:
return float(val)
except (ValueError, TypeError):
return default

def get_score(match):
“”“home_score / away_score -> int, null = 0.”””
hg = match.get(“home_score”)
ag = match.get(“away_score”)
return (int(hg) if hg is not None else 0,
int(ag) if ag is not None else 0)

def get_minute(match):
“””
current_minute (doc officielle) -> int|null.
Fallback sur period string.
“””
m = match.get(“current_minute”)
if m is not None:
try:
v = int(m)
if 1 <= v <= 130:
return v
except (ValueError, TypeError):
pass
# Fallback periode
period = str(match.get(“period”, “”)).upper()
if period == “HT”:
return 45
if period == “FT”:
return 90
return 0

def get_live_stat(match, side, key):
“””
live_stats.home/away.KEY (doc officielle = live_stats, pas statistics).
“””
ls = match.get(“live_stats”)
if not isinstance(ls, dict):
return 0.0
side_data = ls.get(side)
if not isinstance(side_data, dict):
return 0.0
return sf(side_data.get(key))

def get_incidents(match):
inc = match.get(“incidents”)
if isinstance(inc, list):
return inc
return []

# ================================================================

# ANALYSE INCIDENTS

# Champs: type, minute, player_name, is_home (boolean)

# ================================================================

def get_goal_scorers(incidents):
“”“Extrait buteurs. is_home=True -> domicile, False -> visiteur.”””
home_sc, away_sc = [], []
for inc in incidents:
try:
if str(inc.get(“type”, “”)).lower() != “goal”:
continue
minute  = int(inc.get(“minute”) or 0)
player  = str(inc.get(“player_name”) or “?”)
is_home = inc.get(“is_home”)
parts   = player.strip().split()
short   = (parts[0][0] + “. “ + “ “.join(parts[1:])) if len(parts) > 1 else player
entry   = short + “ “ + str(minute) + “’”
if is_home:
home_sc.append(entry)
else:
away_sc.append(entry)
except Exception:
continue
return home_sc, away_sc

def get_recent_activity(incidents, minute):
“””
Activite des 12 dernieres minutes.
is_home (bool) -> True=domicile, False=visiteur.
“””
min_start = max(0, minute - 12)
h_pts, a_pts   = 0, 0
h_evts, a_evts = [], []
for inc in incidents:
try:
m       = int(inc.get(“minute”) or 0)
if m < min_start:
continue
itype   = str(inc.get(“type”, “”)).lower()
is_home = inc.get(“is_home”)
pts, lbl = 0, “”
if itype == “goal”:
pts, lbl = 5, “\u26bd BUT”
if pts > 0:
entry = str(m) + “’ “ + lbl
if is_home:
h_pts += pts
h_evts.append(entry)
else:
a_pts += pts
a_evts.append(entry)
except Exception:
continue
return h_pts, a_pts, h_evts[-3:], a_evts[-3:]

# ================================================================

# CALCUL MOMENTUM (0-100)

# ================================================================

def calc_efficiency(shots_on, shots_total):
if shots_total < 3:
return 0.0, “neutre”, 0
r = shots_on / shots_total
if r >= 0.50:   return r, “efficace”,   +10
elif r >= 0.30: return r, “neutre”,       0
else:           return r, “inefficace”,  -8

def compute_momentum(match, pred):
minute    = get_minute(match)
incidents = get_incidents(match)
hg, ag    = get_score(match)

```
# Stats live (champs doc officielle)
h_son = get_live_stat(match, "home", "shots_on_target")
a_son = get_live_stat(match, "away", "shots_on_target")
h_tot = get_live_stat(match, "home", "total_shots")
a_tot = get_live_stat(match, "away", "total_shots")
h_cor = get_live_stat(match, "home", "corner_kicks")
a_cor = get_live_stat(match, "away", "corner_kicks")
h_pos = get_live_stat(match, "home", "ball_possession")
a_pos = get_live_stat(match, "away", "ball_possession")
h_fls = get_live_stat(match, "home", "fouls")
a_fls = get_live_stat(match, "away", "fouls")

# xG ML (champs exacts doc officielle)
h_xg  = sf(pred.get("expected_home_goals"))
a_xg  = sf(pred.get("expected_away_goals"))
xg_ok = (h_xg + a_xg) > 0.05

# Probabilites ML (0-100)
p_over15 = sf(pred.get("prob_over_15"))
p_over25 = sf(pred.get("prob_over_25"))
p_over35 = sf(pred.get("prob_over_35"))
p_btts   = sf(pred.get("prob_btts_yes"))
p_home   = sf(pred.get("prob_home_win"))
p_draw   = sf(pred.get("prob_draw"))
p_away   = sf(pred.get("prob_away_win"))

# confidence est 0-1 dans la doc -> convertir en %
conf_raw = sf(pred.get("confidence"))
conf_pct = conf_raw * 100 if conf_raw <= 1.0 else conf_raw

# Recommandations ML (boolean)
rec_over25 = bool(pred.get("over_25_recommend"))
rec_btts   = bool(pred.get("btts_recommend"))
rec_winner = bool(pred.get("winner_recommend"))
rec_fav    = bool(pred.get("favorite_recommend"))
favorite   = str(pred.get("favorite") or "")
mls        = str(pred.get("most_likely_score") or "")
res        = str(pred.get("predicted_result") or "")
fav_prob   = sf(pred.get("favorite_prob"))

raw = 0

# 1. Tirs cadres
son_t = h_son + a_son
if son_t >= 14:   raw += 22
elif son_t >= 10: raw += 17
elif son_t >= 7:  raw += 12
elif son_t >= 4:  raw += 7
elif son_t >= 2:  raw += 3

# 2. Tirs total (indicateur de pression)
tot_t = h_tot + a_tot
if tot_t >= 25:   raw += 12
elif tot_t >= 18: raw += 9
elif tot_t >= 12: raw += 6
elif tot_t >= 7:  raw += 3

# 3. Corners
cor_t = h_cor + a_cor
if cor_t >= 14:   raw += 9
elif cor_t >= 9:  raw += 7
elif cor_t >= 6:  raw += 5
elif cor_t >= 3:  raw += 2

# 4. xG ML
xg_t = h_xg + a_xg
if xg_ok:
    if xg_t >= 4.0:   raw += 22
    elif xg_t >= 3.0: raw += 18
    elif xg_t >= 2.0: raw += 14
    elif xg_t >= 1.2: raw += 9
    elif xg_t >= 0.6: raw += 5
    elif xg_t >= 0.2: raw += 2

# 5. Over 2.5 ML
if p_over25 >= 85:   raw += 10
elif p_over25 >= 70: raw += 7
elif p_over25 >= 55: raw += 4
elif p_over25 >= 40: raw += 1

# 6. Recommandations ML (signal fort)
if rec_over25: raw += 6
if rec_btts:   raw += 4

# 7. Efficacite tirs
h_ratio, h_eff, h_bonus = calc_efficiency(h_son, h_tot)
a_ratio, a_eff, a_bonus = calc_efficiency(a_son, a_tot)
raw += h_bonus if h_tot >= a_tot else a_bonus

# 8. Activite recente
h_rec, a_rec, h_rev, a_rev = get_recent_activity(incidents, minute)
rec_t = h_rec + a_rec
if rec_t >= 10:   raw += 8
elif rec_t >= 5:  raw += 5
elif rec_t >= 2:  raw += 2

# Normalisation sur 100 (max theorique ~93)
score = int(min(raw, 100))

return {
    "score":    score,
    "h_son": h_son,  "a_son": a_son,
    "h_tot": h_tot,  "a_tot": a_tot,
    "h_cor": h_cor,  "a_cor": a_cor,
    "h_pos": h_pos,  "a_pos": a_pos,
    "h_xg":  h_xg,   "a_xg":  a_xg,
    "xg_ok": xg_ok,
    "p_over15": p_over15, "p_over25": p_over25,
    "p_over35": p_over35, "p_btts":   p_btts,
    "p_home":   p_home,   "p_draw":   p_draw,
    "p_away":   p_away,
    "conf_pct": conf_pct,
    "mls":      mls,
    "res":      res,
    "favorite": favorite,
    "fav_prob": fav_prob,
    "rec_over25": rec_over25,
    "rec_btts":   rec_btts,
    "rec_winner": rec_winner,
    "rec_fav":    rec_fav,
    "h_eff": h_eff,  "a_eff": a_eff,
    "h_ratio": h_ratio, "a_ratio": a_ratio,
    "h_rec": h_rec,  "a_rec": a_rec,
    "h_rev": h_rev,  "a_rev": a_rev,
}
```

# ================================================================

# SEUIL ADAPTATIF

# ================================================================

def get_threshold(minute, hg, ag, m):
base = 38

```
# Urgence fin de match
if minute >= 83:    base -= 16
elif minute >= 76:  base -= 12
elif minute >= 68:  base -= 8
elif minute >= 58:  base -= 5
elif minute >= 47:  base -= 2
elif minute < 12:   base += 12
elif minute < 22:   base += 7
elif minute < 32:   base += 3

# Contexte score
diff = abs(hg - ag)
if diff == 0:
    if minute >= 70:   base -= 8
    elif minute >= 55: base -= 4
elif diff == 1:
    if minute >= 70:   base -= 8
    elif minute >= 55: base -= 5
elif diff == 2:        base += 4
elif diff >= 3:        base += 12

# Confirmation ML
if m["p_over25"] >= 75:    base -= 6
elif m["p_over25"] >= 60:  base -= 3
if m["rec_over25"]:        base -= 5
if m["conf_pct"] >= 70:    base -= 4
elif m["conf_pct"] >= 50:  base -= 2

# Efficacite
dom_eff = m["h_eff"] if hg <= ag else m["a_eff"]
if dom_eff == "efficace":    base -= 4
elif dom_eff == "inefficace": base += 5

return max(28, min(base, 68))
```

# ================================================================

# EQUIPE DOMINANTE

# ================================================================

def get_dominant(m, h_name, a_name):
h_pwr = m[“h_son”]*5 + m[“h_tot”]*2 + m[“h_cor”]*2 + m[“h_xg”]*12
a_pwr = m[“a_son”]*5 + m[“a_tot”]*2 + m[“a_cor”]*2 + m[“a_xg”]*12
if h_pwr > a_pwr * 1.3:   return “home”, h_name, m[“h_eff”]
elif a_pwr > h_pwr * 1.3: return “away”, a_name, m[“a_eff”]
return “balanced”, None, “neutre”

def eff_str(e):
if e == “efficace”:   return “ \U0001f3af”
if e == “inefficace”: return “ \U0001f4a8”
return “”

# ================================================================

# CONSTRUCTION MESSAGE TELEGRAM

# ================================================================

def build_message(match, m, pred, threshold):
league  = str(match.get(“league”, {}).get(“name”, “?”))
h_name  = str(match.get(“home_team”, “Domicile”))
a_name  = str(match.get(“away_team”, “Visiteur”))
hg, ag  = get_score(match)
minute  = get_minute(match)
score   = m[“score”]
total_g = hg + ag
incidents = get_incidents(match)

```
dom_side, dom_name, dom_eff = get_dominant(m, h_name, a_name)

# Niveau d'alerte
margin = score - threshold
if margin >= 20 or score >= 80:
    lvl, emj = "\U0001f534 ALERTE MAX", "\U0001f534"
elif margin >= 10 or score >= 62:
    lvl, emj = "\U0001f7e0 FORTE PRESSION", "\U0001f7e0"
else:
    lvl, emj = "\U0001f7e1 PRESSION", "\U0001f7e1"

# Jauge
filled = int(score / 10)
gauge  = "\U0001f7e9" * filled + "\u2b1c" * (10 - filled)

# Buteurs
h_sc, a_sc = get_goal_scorers(incidents)
scorers_block = ""
if h_sc or a_sc:
    scorers_block = (SEP + "\n\u26bd BUTEURS:\n"
                     + "  \U0001f539 " + h_name + ": "
                     + (", ".join(h_sc) if h_sc else "\u2014") + "\n"
                     + "  \U0001f539 " + a_name + ": "
                     + (", ".join(a_sc) if a_sc else "\u2014") + "\n")

# Stats live
def stat_line(tname, son, tot, cor, pos, eff, ratio):
    parts = []
    if son >= 1: parts.append(str(int(son)) + " tirs cadres")
    if tot >= 1: parts.append(str(int(tot)) + " tirs tot.")
    if cor >= 1: parts.append(str(int(cor)) + " corners")
    line = "  \U0001f539 " + tname + ": " + (" | ".join(parts) if parts else "stats N/A")
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
    xg_t  = m["h_xg"] + m["a_xg"]
    bh    = "\U0001f7e2" if m["h_xg"] >= m["a_xg"] else "\U0001f534"
    ba    = "\U0001f7e2" if m["a_xg"] > m["h_xg"] else "\U0001f534"
    xg_block = (SEP + "\n\U0001f4d0 xG ATTENDUS (ML CatBoost):\n"
                + "  " + bh + " " + h_name + ": " + str(round(m["h_xg"], 2)) + "\n"
                + "  " + ba + " " + a_name + ": " + str(round(m["a_xg"], 2)) + "\n"
                + "  Total: " + str(round(xg_t, 2)) + "\n")

# Predictions ML
ml_block = ""
if pred:
    ml_lines = []

    # 1X2
    ph, pd, pa = m["p_home"], m["p_draw"], m["p_away"]
    if ph > 0 or pd > 0 or pa > 0:
        def bar(p):
            n = int(round(p / 100 * 8))
            return "\u2588" * n + "\u2591" * (8 - n)
        ml_lines.append("  1X2:")
        ml_lines.append("  " + h_name[:13] + " " + str(round(ph,1)) + "% " + bar(ph))
        ml_lines.append("  Nul          " + str(round(pd,1)) + "% " + bar(pd))
        ml_lines.append("  " + a_name[:13] + " " + str(round(pa,1)) + "% " + bar(pa))

    # Over/Under avec recommandation ML
    po15, po25, po35 = m["p_over15"], m["p_over25"], m["p_over35"]
    if po15 > 0:
        ic = "\U0001f7e2" if po15 >= 75 else "\U0001f7e1"
        ml_lines.append("  " + ic + " Over 1.5: " + str(round(po15,1)) + "%"
                        + (" \u2713 ML" if bool(pred.get("over_15_recommend")) else ""))
    if po25 > 0:
        ic = "\U0001f7e2" if po25 >= 65 else ("\U0001f7e1" if po25 >= 45 else "\U0001f534")
        ml_lines.append("  " + ic + " Over 2.5: " + str(round(po25,1)) + "%"
                        + (" \u2713 ML" if m["rec_over25"] else ""))
    if po35 > 0:
        ic = "\U0001f7e2" if po35 >= 55 else "\U0001f7e1"
        ml_lines.append("  " + ic + " Over 3.5: " + str(round(po35,1)) + "%"
                        + (" \u2713 ML" if bool(pred.get("over_35_recommend")) else ""))

    # BTTS
    pb = m["p_btts"]
    if pb > 0:
        ic = "\U0001f7e2" if pb >= 60 else ("\U0001f7e1" if pb >= 45 else "\U0001f534")
        ml_lines.append("  " + ic + " BTTS: " + str(round(pb,1)) + "%"
                        + (" \u2713 ML" if m["rec_btts"] else ""))

    # Favori ML
    fav = m["favorite"]
    if fav in ("H", "A") and m["fav_prob"] > 0:
        fav_name = h_name if fav == "H" else a_name
        rec_str  = " \u2713 Recommande" if m["rec_fav"] else ""
        ml_lines.append("  \u2b50 Favori: " + fav_name
                        + " (" + str(round(m["fav_prob"],1)) + "%)" + rec_str)

    # Score probable
    if mls and mls not in ("?", "None", ""):
        ml_lines.append("  \U0001f3af Score probable: " + mls)

    # Confiance modele
    cf = m["conf_pct"]
    if cf > 0:
        ic = "\U0001f7e2" if cf >= 65 else ("\U0001f7e1" if cf >= 45 else "\U0001f534")
        ml_lines.append("  " + ic + " Confiance modele: " + str(round(cf,1)) + "%")

    if ml_lines:
        ml_block = (SEP + "\n\U0001f916 PREDICTIONS ML (CatBoost):\n"
                    + "\n".join(ml_lines) + "\n")

# Activite recente
rec_block = ""
if m["h_rev"] or m["a_rev"]:
    rec_block = SEP + "\n\u26a1 MOMENTUM 12 DERNIERES MIN:\n"
    if m["h_rev"]:
        rec_block += "  \U0001f539 " + h_name + ": " + " | ".join(m["h_rev"]) + "\n"
    if m["a_rev"]:
        rec_block += "  \U0001f539 " + a_name + ": " + " | ".join(m["a_rev"]) + "\n"
    if m["h_rec"] > m["a_rec"] * 1.4:
        rec_block += "  \u27a1\ufe0f " + h_name + " en pleine montee\n"
    elif m["a_rec"] > m["h_rec"] * 1.4:
        rec_block += "  \u27a1\ufe0f " + a_name + " en pleine montee\n"
    else:
        rec_block += "  \u27a1\ufe0f Pression des deux cotes\n"

# Recommandations
recs = []

# Prochain but
if dom_name:
    if dom_eff == "efficace":
        recs.append("  \u2192 \u26bd Prochain but: " + dom_name
                    + " (dominante + efficace) \U0001f3af")
    elif dom_eff == "inefficace":
        recs.append("  \u2192 \u26bd " + dom_name + " domine mais imprecise \U0001f4a8")
    else:
        recs.append("  \u2192 \u26bd Prochain but probable: " + dom_name)
else:
    recs.append("  \u2192 \u26bd Match ouvert - buts des deux cotes possibles")

# Over/Under
if m["rec_over25"]:
    recs.append("  \u2192 \U0001f4c8 Over " + str(total_g) + ".5 buts"
                + " (\U0001f916 ML recommande, " + str(round(m["p_over25"],0)) + "%)")
elif m["p_over25"] >= 65:
    recs.append("  \u2192 \U0001f4c8 Over " + str(total_g) + ".5 buts"
                + " (" + str(round(m["p_over25"],0)) + "% ML)")
else:
    recs.append("  \u2192 \U0001f4c8 Over " + str(total_g) + ".5 buts dans le match")

if minute < 44:
    recs.append("  \u2192 \U0001f4c8 Over 0.5 buts 1ere MT")
elif minute < 90:
    recs.append("  \u2192 \U0001f4c8 Over 0.5 buts 2eme MT")

# BTTS
if m["rec_btts"]:
    recs.append("  \u2192 \U0001f3af BTTS Yes (\U0001f916 ML recommande, "
                + str(round(m["p_btts"],0)) + "%)")
elif m["p_btts"] >= 60:
    recs.append("  \u2192 \U0001f3af BTTS (" + str(round(m["p_btts"],0)) + "% ML)")
elif hg == 0 and ag == 0 and minute >= 28:
    recs.append("  \u2192 \U0001f3af BTTS - 0-0 sous pression")

# Score exact
if mls and mls not in ("?", "None", ""):
    recs.append("  \u2192 \U0001f3af Score exact: " + mls + " (Poisson ML)")

# xG insight
if m["xg_ok"]:
    xg_t = m["h_xg"] + m["a_xg"]
    if xg_t >= 3.5:
        recs.append("  \u2192 \U0001f4d0 xG tres eleve (" + str(round(xg_t,2))
                    + ") - match tres offensif")

rec_section = (SEP + "\n\U0001f4a1 QUOI JOUER:\n"
               + "\n".join(recs) + "\n")

# Assemblage
mls = m["mls"]
return (emj + " " + lvl + " - BUT POTENTIEL\n"
        + SEP + "\n"
        + "\U0001f3c6 " + league + "\n"
        + "\u2694\ufe0f  " + h_name + " \U0001f7e5 " + str(hg)
        + "  \u2014  " + str(ag) + " \U0001f7e6 " + a_name + "\n"
        + "\u23f1\ufe0f  " + str(minute) + "' | Momentum: " + str(score) + "/100\n"
        + gauge + "\n"
        + scorers_block
        + SEP + "\n"
        + "\U0001f4ca STATS LIVE:\n"
        + h_stat + "\n"
        + a_stat + "\n"
        + xg_block
        + ml_block
        + rec_block
        + rec_section
        + SEP + "\n"
        + "\u26a0\ufe0f Parie de facon responsable")
```

# ================================================================

# BOUCLE PRINCIPALE

# ================================================================

async def run_forever():
bot        = Bot(token=TELEGRAM_TOKEN)
loop_count = 0

```
# Message de demarrage
try:
    await bot.send_message(
        chat_id=str(TELEGRAM_CHAT_ID),
        text=("\U0001f7e2 BOT FOOTBALL DEMARRE\n" + SEP + "\n"
              + "\U0001f4cb " + str(len(WATCHED_LEAGUES)) + " ligues\n"
              + "\u23f1\ufe0f Cycle toutes les " + str(INTERVAL) + "s\n"
              + "\U0001f916 ML CatBoost actif\n"
              + "\U0001f4d0 xG + recommandations ML\n"
              + SEP)
    )
except Exception as e:
    print("  Erreur demarrage: " + str(e), flush=True)

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

                    # Predictions ML
                    pred = get_prediction(event_id)

                    # Momentum
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

                    # Alerte (fenetre 15 min)
                    alert_key = str(event_id) + "_" + str(minute // 15)
                    if score >= threshold and alert_key not in alerts_sent:
                        alerts_sent[alert_key] = True
                        msg = build_message(match, m, pred, threshold)
                        await bot.send_message(
                            chat_id=str(TELEGRAM_CHAT_ID),
                            text=msg
                        )
                        print("  \u2705 ALERTE: " + h_name + " vs " + a_name
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
```

# ================================================================

# ENTREE

# ================================================================

if **name** == “**main**”:
missing = []
if not BZZOIRO_KEY:      missing.append(“BZZOIRO_KEY”)
if not TELEGRAM_TOKEN:   missing.append(“TELEGRAM_TOKEN”)
if not TELEGRAM_CHAT_ID: missing.append(“TELEGRAM_CHAT_ID”)
if missing:
print(“VARIABLES MANQUANTES: “ + “, “.join(missing), flush=True)
exit(1)
print(”  Config OK - “ + str(len(WATCHED_LEAGUES))
+ “ ligues surveillees”, flush=True)
asyncio.run(run_forever())
