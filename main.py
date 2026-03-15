import os
import time
import asyncio
import traceback
import requests
from datetime import datetime
from telegram import Bot

BZZOIRO_KEY      = os.environ.get("BZZOIRO_KEY", "")
TELEGRAM_TOKEN   = os.environ.get("TELEGRAM_TOKEN", "")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "")

BASE_URL = "https://sports.bzzoiro.com"
HEADERS  = {"Authorization": "Token " + BZZOIRO_KEY}

INTERVAL = 60

WATCHED_LEAGUES = {
    1, 2, 3, 4, 5, 6, 7, 8, 9, 10,
    11, 12, 13, 14, 15, 17, 18, 19, 20, 22, 23, 24
}

LIVE_STATUSES = {
    "inprogress", "1st_half", "2nd_half", "halftime",
    "ht", "live", "playing", "in_play", "1h", "2h"
}

# Cle = event_id
# Valeur = {"count": int, "last_alert_minute": int}
match_alerts = {}

# Cle mi-temps = event_id + "_ht"
ht_alerts_sent = {}

# Cache predictions
pred_cache = {}

# Cache stats precedentes pour detecter montee de pression
# Cle = event_id, valeur = {"son": float, "cor": float, "xg": float, "minute": int}
prev_stats = {}


def api_get(endpoint, params=None):
    try:
        r = requests.get(
            BASE_URL + endpoint,
            headers=HEADERS,
            params=params or {},
            timeout=15
        )
        if r.status_code == 401:
            print("[API] 401 cle invalide", flush=True)
            return None
        if r.status_code == 429:
            print("[API] Rate limit 30s", flush=True)
            time.sleep(30)
            return None
        if r.status_code != 200:
            print("[API] HTTP " + str(r.status_code), flush=True)
            return None
        return r.json()
    except Exception as e:
        print("[API] " + str(e), flush=True)
        return None


def extract_list(data):
    if isinstance(data, list):
        return data
    if isinstance(data, dict):
        for k in ("results", "data", "matches", "events"):
            v = data.get(k)
            if isinstance(v, list):
                return v
    return []


def api_pages(endpoint, params=None, max_pages=8):
    out = []
    p = dict(params) if params else {}
    for page in range(1, max_pages + 1):
        p["page"] = page
        data = api_get(endpoint, p)
        if not data:
            break
        rows = extract_list(data)
        if not rows:
            break
        out.extend(rows)
        if isinstance(data, dict) and not data.get("next"):
            break
    return out


def sf(val, default=0.0):
    if val is None:
        return default
    if isinstance(val, bool):
        return 1.0 if val else 0.0
    try:
        return float(str(val).replace("%", "").strip())
    except Exception:
        return default


def get_score(match):
    return (int(match.get("home_score") or 0),
            int(match.get("away_score") or 0))


def get_minute(match):
    mn = match.get("current_minute")
    if mn is not None:
        try:
            v = int(mn)
            if 1 <= v <= 130:
                return v
        except Exception:
            pass
    per = str(match.get("period", "")).upper()
    if per in ("HT", "HALFTIME"):
        return 45
    if per in ("FT", "FINISHED"):
        return 90
    return 0


def get_stat(match, side, key):
    ls = match.get("live_stats")
    if not isinstance(ls, dict):
        return 0.0
    sd = ls.get(side)
    if not isinstance(sd, dict):
        return 0.0
    return sf(sd.get(key))


def get_live_matches():
    today = datetime.now().strftime("%Y-%m-%d")
    candidates = []

    d = api_get("/api/live/")
    if d:
        rows = extract_list(d)
        print("[LIVE] /api/live/ -> " + str(len(rows)), flush=True)
        candidates.extend(rows)

    if not candidates:
        for sv in ["inprogress", "1st_half", "2nd_half", "halftime"]:
            rows = api_pages("/api/events/", {"status": sv, "date": today})
            if rows:
                print("[LIVE] status=" + sv + " -> " + str(len(rows)), flush=True)
                candidates.extend(rows)

    if not candidates:
        rows = api_pages("/api/events/", {"date": today}, max_pages=8)
        sts = {}
        for m in rows:
            st = str(m.get("status", "")).strip()
            sts[st] = sts.get(st, 0) + 1
        print("[LIVE] today=" + str(len(rows)) + " statuts=" + str(sts), flush=True)
        candidates.extend(rows)

    live = []
    for m in candidates:
        lg  = m.get("league", {})
        lid = lg.get("id") if isinstance(lg, dict) else None
        if lid not in WATCHED_LEAGUES:
            continue
        st  = str(m.get("status", "")).lower().strip()
        mn  = m.get("current_minute")
        per = str(m.get("period", "")).upper()
        is_live = (
            st in LIVE_STATUSES
            or (mn is not None and str(mn).isdigit() and int(mn) > 0)
            or per in ("1T", "2T")
        )
        if is_live:
            live.append(m)

    print("[LIVE] retenus: " + str(len(live)) + "/" + str(len(candidates)), flush=True)
    return live


def get_prediction(event_id):
    key = str(event_id)
    if key in pred_cache:
        ts, pred = pred_cache[key]
        if time.time() - ts < 600:
            return pred
    d = api_get("/api/predictions/", {"event": event_id})
    pred = {}
    if d:
        rows = extract_list(d)
        pred = rows[0] if rows else {}
    pred_cache[key] = (time.time(), pred)
    return pred


def analyse(match, pred, event_id):
    """
    Calcule le danger global du match qu un but tombe.
    Combine snapshot actuel + detection de montee de pression.
    """
    hg, ag = get_score(match)
    diff   = abs(hg - ag)
    minute = get_minute(match)

    if diff >= 3:
        return None

    h_son = get_stat(match, "home", "shots_on_target")
    a_son = get_stat(match, "away", "shots_on_target")
    h_tot = get_stat(match, "home", "total_shots")
    a_tot = get_stat(match, "away", "total_shots")
    h_cor = get_stat(match, "home", "corner_kicks")
    a_cor = get_stat(match, "away", "corner_kicks")
    h_pos = get_stat(match, "home", "ball_possession")
    a_pos = get_stat(match, "away", "ball_possession")
    h_xg  = sf(pred.get("expected_home_goals"))
    a_xg  = sf(pred.get("expected_away_goals"))
    xg_ok = (h_xg + a_xg) > 0.1

    p_over25   = sf(pred.get("prob_over_25"))
    p_btts     = sf(pred.get("prob_btts_yes"))
    rec_over25 = bool(pred.get("over_25_recommend"))
    rec_btts   = bool(pred.get("btts_recommend"))
    favorite   = str(pred.get("favorite") or "")
    fav_prob   = sf(pred.get("favorite_prob"))
    rec_fav    = bool(pred.get("favorite_recommend"))

    total_son = h_son + a_son
    total_cor = h_cor + a_cor
    total_xg  = h_xg + a_xg
    dom_pos   = max(h_pos, a_pos)

    # Match ferme = rien a signaler
    if total_son < 3:   return None
    if total_cor < 4:   return None
    if xg_ok and total_xg < 0.4: return None

    # === SCORE DE DANGER SNAPSHOT ===
    danger = 0.0

    # Signal 1 : tirs cadres totaux
    if total_son >= 10:  danger += 35
    elif total_son >= 7: danger += 28
    elif total_son >= 5: danger += 20
    elif total_son >= 3: danger += 12

    # Signal 2 : xG total
    if xg_ok:
        if total_xg >= 3.0:   danger += 30
        elif total_xg >= 2.0: danger += 24
        elif total_xg >= 1.5: danger += 18
        elif total_xg >= 1.0: danger += 12
        elif total_xg >= 0.5: danger += 6

    # Signal 3 : corners totaux
    if total_cor >= 12:  danger += 20
    elif total_cor >= 9: danger += 16
    elif total_cor >= 7: danger += 12
    elif total_cor >= 5: danger += 8
    elif total_cor >= 4: danger += 4

    # Signal 4 : possession dominante
    if dom_pos >= 70:   danger += 12
    elif dom_pos >= 65: danger += 8
    elif dom_pos >= 60: danger += 5

    # Signal 5 : les deux equipes attaquent
    if h_son >= 2 and a_son >= 2:
        danger += 8
    if xg_ok and h_xg >= 0.3 and a_xg >= 0.3:
        danger += 6

    # Bonus ML
    if p_over25 >= 72:  danger += 6
    if rec_over25:      danger += 4
    if rec_btts:        danger += 3

    # Bonus score serre
    if diff == 0:   danger += 8
    elif diff == 1: danger += 4

    # === DETECTION MONTEE DE PRESSION ===
    # Compare avec le cycle precedent pour voir si ca monte
    pressure_rising = False
    pressure_bonus  = 0
    prev = prev_stats.get(event_id)

    if prev and prev["minute"] < minute:
        delta_son = total_son - prev["son"]
        delta_cor = total_cor - prev["cor"]
        delta_xg  = total_xg  - prev["xg"]

        # Pression qui monte = tirs cadres ET corners augmentent
        if delta_son >= 2 and delta_cor >= 1:
            pressure_rising = True
            pressure_bonus  = 12
        elif delta_son >= 1 and delta_cor >= 2:
            pressure_rising = True
            pressure_bonus  = 8
        elif delta_son >= 1 and delta_xg >= 0.3:
            pressure_rising = True
            pressure_bonus  = 6

        danger += pressure_bonus

    # Mettre a jour le cache stats
    prev_stats[event_id] = {
        "son":    total_son,
        "cor":    total_cor,
        "xg":     total_xg,
        "minute": minute,
    }

    danger = min(max(int(danger), 0), 100)

    # Equipe la plus dangereuse (bonus info)
    h_danger = h_son * 8 + h_xg * 15 + h_cor * 3
    a_danger = a_son * 8 + a_xg * 15 + a_cor * 3
    if h_danger > a_danger * 1.4:
        likely_scorer = "home"
        likely_xg     = h_xg
    elif a_danger > h_danger * 1.4:
        likely_scorer = "away"
        likely_xg     = a_xg
    else:
        likely_scorer = "both"
        likely_xg     = max(h_xg, a_xg)

    return {
        "danger":          danger,
        "pressure_rising": pressure_rising,
        "pressure_bonus":  pressure_bonus,
        "likely_scorer":   likely_scorer,
        "likely_xg":       likely_xg,
        "total_son":       total_son,
        "total_cor":       total_cor,
        "total_xg":        total_xg,
        "h_son": h_son, "a_son": a_son,
        "h_cor": h_cor, "a_cor": a_cor,
        "h_pos": h_pos, "a_pos": a_pos,
        "h_xg":  h_xg,  "a_xg":  a_xg,
        "p_over25":   p_over25,
        "p_btts":     p_btts,
        "rec_over25": rec_over25,
        "rec_btts":   rec_btts,
        "rec_fav":    rec_fav,
        "favorite":   favorite,
        "fav_prob":   fav_prob,
        "xg_ok":      xg_ok,
        "diff":        diff,
    }


def get_threshold(a, diff, minute):
    # Seuil eleve par defaut = uniquement alertes max
    base = 62

    # Fenetre privilegiee 60-88min : seuil reduit = plus sensible
    if minute >= 60:
        base -= 14
    elif minute >= 50:
        base -= 5

    # Score serre
    if diff == 0:   base -= 8
    elif diff == 1: base -= 5
    elif diff == 2: base += 8

    # Boost ML
    if a["rec_over25"]:                       base -= 4
    if a["p_over25"] >= 75:                   base -= 3
    if a["xg_ok"] and a["total_xg"] >= 1.5:  base -= 4

    # Pression montante = signal fort
    if a["pressure_rising"]:                  base -= 6

    return max(35, min(base, 72))


def build_alert(match, a, threshold):
    league  = str(match.get("league", {}).get("name", "?"))
    h_name  = str(match.get("home_team", "Dom"))
    a_name  = str(match.get("away_team", "Ext"))
    hg, ag  = get_score(match)
    minute  = get_minute(match)
    danger  = a["danger"]
    total_g = hg + ag

    margin = danger - threshold
    if margin >= 15 or danger >= 78:
        emj = "🔴🔴"
        lvl = "ALERTE MAX"
    elif margin >= 7 or danger >= 63:
        emj = "🟠"
        lvl = "FORTE PRESSION"
    else:
        emj = "🟡"
        lvl = "PRESSION"

    rising_str = " 📈 PRESSION EN HAUSSE" if a["pressure_rising"] else ""

    son_str = str(int(a["h_son"])) + "/" + str(int(a["a_son"])) + " tirs cadres"
    cor_str = str(int(a["h_cor"])) + "/" + str(int(a["a_cor"])) + " corners"
    xg_str  = ""
    if a["xg_ok"]:
        xg_str = " | xG " + str(round(a["h_xg"], 1)) + "/" + str(round(a["a_xg"], 1))

    if a["likely_scorer"] == "home":
        scorer_str = "Favori: " + h_name
        if a["likely_xg"] >= 1.0:
            scorer_str += " (xG " + str(round(a["likely_xg"], 1)) + ")"
    elif a["likely_scorer"] == "away":
        scorer_str = "Favori: " + a_name
        if a["likely_xg"] >= 1.0:
            scorer_str += " (xG " + str(round(a["likely_xg"], 1)) + ")"
    else:
        scorer_str = "Match ouvert - but des deux cotes"

    bets = []
    if a["rec_over25"] or a["p_over25"] >= 65:
        bets.append("Over " + str(total_g) + ".5 (" + str(int(a["p_over25"])) + "%)")
    if a["rec_btts"] or a["p_btts"] >= 62:
        bets.append("BTTS (" + str(int(a["p_btts"])) + "%)")
    bets.append("Next goal " + ("MT1" if minute < 43 else "MT2"))

    lines = [
        emj + " " + lvl + " - " + league + rising_str,
        h_name + " " + str(hg) + "-" + str(ag) + " " + a_name + " | " + str(minute) + "min",
        "⚽ But imminent | " + scorer_str,
        "📊 " + son_str + " | " + cor_str + xg_str,
        "💡 " + " | ".join(bets),
    ]
    return "\n".join(lines)


def check_halftime_alert(match, a, minute):
    """
    Alerte a la 33eme minute UNIQUEMENT si 0-0 avec pression.
    """
    if a is None:
        return None

    hg, ag = get_score(match)
    if hg != 0 or ag != 0:
        return None

    # Uniquement autour de la 33eme minute
    if not (31 <= minute <= 35):
        return None

    # Pression minimale requise
    if a["total_son"] < 2 or a["total_cor"] < 3:
        return None

    h_name = str(match.get("home_team", "Dom"))
    a_name = str(match.get("away_team", "Ext"))
    league = str(match.get("league", {}).get("name", "?"))

    xg_str = ""
    if a["xg_ok"] and a["total_xg"] > 0.3:
        xg_str = " | xG " + str(round(a["total_xg"], 1))

    lines = [
        "⏱️ 0-0 A LA 33EME - " + league,
        h_name + " 0-0 " + a_name + " | " + str(minute) + "min",
        "📈 Score vierge avec pression - buts probables en 2MT",
        "📊 " + str(int(a["total_son"])) + " tirs cadres | " + str(int(a["total_cor"])) + " corners" + xg_str,
        "💡 Over 0.5 buts 2MT | BTTS value",
    ]
    return "\n".join(lines)


async def run_forever():
    bot   = Bot(token=TELEGRAM_TOKEN)
    cycle = 0

    try:
        await bot.send_message(
            chat_id=str(TELEGRAM_CHAT_ID),
            text="🟢 Bot demarre " + datetime.now().strftime("%H:%M:%S") + " | " + str(len(WATCHED_LEAGUES)) + " ligues"
        )
    except Exception as e:
        print("Erreur demarrage: " + str(e), flush=True)

    while True:
        cycle += 1
        print("\n=== Cycle #" + str(cycle) + " " + datetime.now().strftime("%H:%M:%S") + " ===", flush=True)

        try:
            matches = get_live_matches()

            if not matches:
                print("Aucun match live", flush=True)
            else:
                for match in matches:
                    try:
                        event_id = str(match.get("id"))
                        minute   = get_minute(match)
                        hg, ag   = get_score(match)
                        h_name   = str(match.get("home_team", "?"))
                        a_name   = str(match.get("away_team", "?"))
                        diff     = abs(hg - ag)

                        pred = get_prediction(event_id)
                        a    = analyse(match, pred, event_id)

                        if a is None:
                            print("  [skip] " + h_name[:10] + " vs " + a_name[:10]
                                  + " [" + str(minute) + "'] match ferme", flush=True)
                            continue

                        danger    = a["danger"]
                        threshold = get_threshold(a, diff, minute)
                        info      = a["match_alerts"] if "match_alerts" in a else match_alerts.get(event_id, {})
                        alert_info = match_alerts.get(event_id, {"count": 0, "last_minute": 0})

                        print("  [" + str(minute) + "'] "
                              + h_name[:10] + " " + str(hg) + "-" + str(ag) + " " + a_name[:10]
                              + " | danger=" + str(danger) + "/" + str(threshold)
                              + " rising=" + str(a["pressure_rising"])
                              + " son=" + str(int(a["total_son"]))
                              + " cor=" + str(int(a["total_cor"]))
                              + " xg=" + str(round(a["total_xg"], 1)),
                              flush=True)

                        # === ALERTE MI-TEMPS 33eme (1 seule par match) ===
                        ht_key = event_id + "_ht"
                        if ht_key not in ht_alerts_sent:
                            ht_msg = check_halftime_alert(match, a, minute)
                            if ht_msg:
                                ht_alerts_sent[ht_key] = True
                                await bot.send_message(
                                    chat_id=str(TELEGRAM_CHAT_ID),
                                    text=ht_msg
                                )
                                print("  >>> ALERTE 33EME: " + h_name + " vs " + a_name, flush=True)
                                await asyncio.sleep(1)

                        # === ALERTE PRINCIPALE ===
                        # Conditions :
                        # 1. Danger >= seuil
                        # 2. Max 2 alertes par match
                        # 3. Cooldown 20 minutes depuis derniere alerte
                        # 4. Pas apres la 88eme

                        if minute > 88:
                            continue

                        count       = alert_info["count"]
                        last_minute = alert_info["last_minute"]

                        if count >= 2:
                            continue

                        if count > 0 and (minute - last_minute) < 20:
                            print("  [cooldown] " + str(minute - last_minute) + "min depuis derniere alerte", flush=True)
                            continue

                        margin = danger - threshold
                        is_max_alert = (margin >= 15 or danger >= 78)

                        if danger >= threshold and is_max_alert:
                            match_alerts[event_id] = {
                                "count":       count + 1,
                                "last_minute": minute,
                            }
                            msg = build_alert(match, a, threshold)
                            await bot.send_message(
                                chat_id=str(TELEGRAM_CHAT_ID),
                                text=msg
                            )
                            print("  >>> ALERTE MAX #" + str(count + 1)
                                  + ": " + h_name + " vs " + a_name
                                  + " [" + str(minute) + "'] danger=" + str(danger),
                                  flush=True)
                            await asyncio.sleep(1)
                        elif danger >= threshold:
                            print("  [sous-seuil max] danger=" + str(danger)
                                  + " threshold=" + str(threshold)
                                  + " margin=" + str(margin) + " - pas envoye",
                                  flush=True)

                    except Exception as e:
                        print("  ERREUR match: " + str(e), flush=True)
                        traceback.print_exc()

            # Nettoyage memoire
            if len(ht_alerts_sent) > 200:
                for k in list(ht_alerts_sent.keys())[:100]:
                    del ht_alerts_sent[k]
            if len(match_alerts) > 200:
                for k in list(match_alerts.keys())[:100]:
                    del match_alerts[k]
            if len(pred_cache) > 200:
                for k in list(pred_cache.keys())[:100]:
                    del pred_cache[k]
            if len(prev_stats) > 200:
                for k in list(prev_stats.keys())[:100]:
                    del prev_stats[k]

        except Exception as e:
            print("ERREUR BOUCLE: " + str(e), flush=True)
            traceback.print_exc()
            await asyncio.sleep(10)

        await asyncio.sleep(INTERVAL)


if __name__ == "__main__":
    missing = []
    if not BZZOIRO_KEY:      missing.append("BZZOIRO_KEY")
    if not TELEGRAM_TOKEN:   missing.append("TELEGRAM_TOKEN")
    if not TELEGRAM_CHAT_ID: missing.append("TELEGRAM_CHAT_ID")
    if missing:
        print("VARIABLES MANQUANTES: " + ", ".join(missing), flush=True)
        exit(1)
    print("Config OK", flush=True)
    asyncio.run(run_forever())
