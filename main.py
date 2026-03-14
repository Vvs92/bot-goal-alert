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

alerts_sent = {}
pred_cache  = {}


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


def analyse(match, pred):
    minute  = get_minute(match)
    hg, ag  = get_score(match)
    diff    = abs(hg - ag)

    h_son = get_stat(match, "home", "shots_on_target")
    a_son = get_stat(match, "away", "shots_on_target")
    h_tot = get_stat(match, "home", "total_shots")
    a_tot = get_stat(match, "away", "total_shots")
    h_cor = get_stat(match, "home", "corner_kicks")
    a_cor = get_stat(match, "away", "corner_kicks")
    h_pos = get_stat(match, "home", "ball_possession")
    a_pos = get_stat(match, "away", "ball_possession")

    p_over25   = sf(pred.get("prob_over_25"))
    p_btts     = sf(pred.get("prob_btts_yes"))
    rec_over25 = bool(pred.get("over_25_recommend"))
    rec_btts   = bool(pred.get("btts_recommend"))
    rec_fav    = bool(pred.get("favorite_recommend"))
    favorite   = str(pred.get("favorite") or "")
    fav_prob   = sf(pred.get("favorite_prob"))
    h_xg       = sf(pred.get("expected_home_goals"))
    a_xg       = sf(pred.get("expected_away_goals"))

    def team_pressure(son, tot, cor, pos):
        score = 0.0

        # SIGNAL 1 : Tirs cadres (qualite des occasions)
        # C'est le signal le plus predictif d'un but imminent
        if son >= 7:    score += 35
        elif son >= 5:  score += 27
        elif son >= 3:  score += 18
        elif son >= 2:  score += 10
        elif son >= 1:  score += 4

        # SIGNAL 2 : Adresse (tirs cadres / tirs totaux)
        # Une equipe adroite est plus dangereuse meme avec moins de tirs
        if tot >= 3:
            ratio = son / tot
            if ratio >= 0.55:   score += 18
            elif ratio >= 0.40: score += 10
            elif ratio >= 0.25: score += 4
            else:               score -= 6

        # SIGNAL 3 : Corners (indice de pression territoriale)
        # Nombreux corners = equipe qui pousse dans le camp adverse
        if cor >= 8:    score += 20
        elif cor >= 6:  score += 15
        elif cor >= 4:  score += 10
        elif cor >= 2:  score += 5

        # SIGNAL 4 : Possession (proxy du temps passe en zone offensive)
        # Possession elevee = plus de temps dans le camp adverse
        if pos >= 65:   score += 12
        elif pos >= 58: score += 8
        elif pos >= 52: score += 4

        return score

    h_pressure = team_pressure(h_son, h_tot, h_cor, h_pos)
    a_pressure = team_pressure(a_son, a_tot, a_cor, a_pos)

    total = h_pressure + a_pressure
    if total < 5:
        return None

    if h_pressure > a_pressure * 1.35:
        dom_side     = "home"
        dom_pressure = h_pressure
        dom_son      = h_son
        dom_tot      = h_tot
        dom_cor      = h_cor
        dom_pos      = h_pos
        dom_ratio    = h_son / h_tot if h_tot >= 3 else 0
    elif a_pressure > h_pressure * 1.35:
        dom_side     = "away"
        dom_pressure = a_pressure
        dom_son      = a_son
        dom_tot      = a_tot
        dom_cor      = a_cor
        dom_pos      = a_pos
        dom_ratio    = a_son / a_tot if a_tot >= 3 else 0
    else:
        dom_side     = "balanced"
        dom_pressure = total / 2
        dom_son      = max(h_son, a_son)
        dom_tot      = max(h_tot, a_tot)
        dom_cor      = max(h_cor, a_cor)
        dom_pos      = max(h_pos, a_pos)
        dom_ratio    = 0

    # Score momentum global
    raw = min(dom_pressure, 75)

    # Bonus fenetre optimale
    if 60 <= minute <= 80:
        raw += 12
    elif 25 <= minute <= 42:
        raw += 7

    # Bonus match serre
    if diff == 0:   raw += 8
    elif diff == 1: raw += 4
    elif diff >= 3: raw -= 20

    # Bonus ML predictions
    if p_over25 >= 72: raw += 8
    if rec_over25:     raw += 5
    if h_xg + a_xg >= 2.5: raw += 6

    momentum = min(max(int(raw), 0), 100)

    return {
        "momentum":    momentum,
        "dom_side":    dom_side,
        "dom_pressure": dom_pressure,
        "dom_ratio":   dom_ratio,
        "dom_son":     dom_son,
        "dom_cor":     dom_cor,
        "dom_pos":     dom_pos,
        "h_son": h_son, "a_son": a_son,
        "h_tot": h_tot, "a_tot": a_tot,
        "h_cor": h_cor, "a_cor": a_cor,
        "h_pos": h_pos, "a_pos": a_pos,
        "h_xg": h_xg,  "a_xg": a_xg,
        "p_over25":   p_over25,
        "p_btts":     p_btts,
        "rec_over25": rec_over25,
        "rec_btts":   rec_btts,
        "rec_fav":    rec_fav,
        "favorite":   favorite,
        "fav_prob":   fav_prob,
    }


def get_threshold(minute, diff, a):
    base = 44
    if 60 <= minute <= 80:   base -= 8
    elif 25 <= minute <= 42: base -= 5
    elif minute > 82:        base += 30
    elif minute < 15:        base += 8
    if diff == 0:   base -= 5
    elif diff >= 3: base += 25
    if a["p_over25"] >= 70: base -= 4
    if a["rec_over25"]:     base -= 3
    return max(32, min(base, 72))


def build_alert(match, a, threshold):
    league  = str(match.get("league", {}).get("name", "?"))
    h_name  = str(match.get("home_team", "Dom"))
    a_name  = str(match.get("away_team", "Ext"))
    hg, ag  = get_score(match)
    minute  = get_minute(match)
    mom     = a["momentum"]
    total_g = hg + ag

    # Equipe qui va marquer
    if a["dom_side"] == "home":
        scorer  = h_name
        adroit  = " (adroit 🎯)" if a["dom_ratio"] >= 0.45 else ""
        pression = str(int(a["h_son"])) + " tirs cadres | " + str(int(a["h_cor"])) + " corners | " + str(int(a["h_pos"])) + "% poss."
    elif a["dom_side"] == "away":
        scorer  = a_name
        adroit  = " (adroit 🎯)" if a["dom_ratio"] >= 0.45 else ""
        pression = str(int(a["a_son"])) + " tirs cadres | " + str(int(a["a_cor"])) + " corners | " + str(int(a["a_pos"])) + "% poss."
    else:
        scorer  = "Les deux equipes"
        adroit  = ""
        pression = (str(int(a["h_son"])) + "/" + str(int(a["a_son"])) + " tirs cadres | "
                    + str(int(a["h_cor"])) + "/" + str(int(a["a_cor"])) + " corners")

    # Niveau
    margin = mom - threshold
    if margin >= 18 or mom >= 78:
        emj = "🔴🔴"
        lvl = "ALERTE MAX"
    elif margin >= 8 or mom >= 62:
        emj = "🟠"
        lvl = "FORTE PRESSION"
    else:
        emj = "🟡"
        lvl = "PRESSION"

    # Paris
    bets = []
    if a["rec_over25"] or a["p_over25"] >= 65:
        bets.append("Over " + str(total_g) + ".5 (" + str(int(a["p_over25"])) + "%)")
    if a["rec_btts"] or a["p_btts"] >= 62:
        bets.append("BTTS (" + str(int(a["p_btts"])) + "%)")
    if a["favorite"] in ("H", "A") and a["rec_fav"]:
        fn = h_name if a["favorite"] == "H" else a_name
        bets.append(fn[:12] + " win (" + str(int(a["fav_prob"])) + "%)")
    bets.append("Next goal " + ("MT1" if minute < 43 else "MT2"))
    bets_str = " | ".join(bets)

    lines = [
        emj + " " + lvl + " - " + league,
        h_name + " " + str(hg) + "-" + str(ag) + " " + a_name + " | " + str(minute) + "min",
        "⚽ Prochain but: " + scorer + adroit,
        "📊 " + pression,
        "💡 " + bets_str,
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
                        event_id = match.get("id")
                        minute   = get_minute(match)
                        hg, ag   = get_score(match)
                        h_name   = str(match.get("home_team", "?"))
                        a_name   = str(match.get("away_team", "?"))

                        if minute > 85:
                            continue

                        pred = get_prediction(event_id)
                        a    = analyse(match, pred)

                        if a is None:
                            print("  [skip-nodata] " + h_name[:10] + " vs " + a_name[:10], flush=True)
                            continue

                        mom       = a["momentum"]
                        threshold = get_threshold(minute, abs(hg - ag), a)

                        print("  [" + str(minute) + "'] "
                              + h_name[:10] + " " + str(hg) + "-" + str(ag) + " " + a_name[:10]
                              + " | mom=" + str(mom) + " seuil=" + str(threshold)
                              + " dom=" + a["dom_side"]
                              + " son=" + str(int(a["h_son"])) + "/" + str(int(a["a_son"]))
                              + " cor=" + str(int(a["h_cor"])) + "/" + str(int(a["a_cor"]))
                              + " pos=" + str(int(a["h_pos"])) + "/" + str(int(a["a_pos"])),
                              flush=True)

                        alert_key = str(event_id) + "_" + str(minute // 15)
                        if mom >= threshold and alert_key not in alerts_sent:
                            alerts_sent[alert_key] = True
                            msg = build_alert(match, a, threshold)
                            await bot.send_message(
                                chat_id=str(TELEGRAM_CHAT_ID),
                                text=msg
                            )
                            print("  >>> ALERTE: " + h_name + " vs " + a_name + " [" + str(minute) + "']", flush=True)
                            await asyncio.sleep(1)

                    except Exception as e:
                        print("  ERREUR match: " + str(e), flush=True)
                        traceback.print_exc()

            if len(alerts_sent) > 500:
                for k in list(alerts_sent.keys())[:200]:
                    del alerts_sent[k]
            if len(pred_cache) > 200:
                for k in list(pred_cache.keys())[:100]:
                    del pred_cache[k]

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
