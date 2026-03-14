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

alerts_count = {}
alerts_sent  = {}
pred_cache   = {}


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
    hg, ag = get_score(match)
    diff   = abs(hg - ag)

    # Match plie = aucun interet
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
    rec_fav    = bool(pred.get("favorite_recommend"))
    favorite   = str(pred.get("favorite") or "")
    fav_prob   = sf(pred.get("favorite_prob"))

    def team_score(son, tot, cor, pos, xg):
        score = 0.0

        # SIGNAL 1 : Tirs cadres
        if son >= 8:    score += 40
        elif son >= 6:  score += 32
        elif son >= 4:  score += 22
        elif son >= 3:  score += 14
        elif son >= 2:  score += 7

        # SIGNAL 2 : Adresse via xG (si dispo) sinon ratio
        if xg_ok and xg > 0:
            if xg >= 2.0:    score += 25
            elif xg >= 1.5:  score += 20
            elif xg >= 1.0:  score += 14
            elif xg >= 0.6:  score += 8
            elif xg >= 0.3:  score += 3
        elif tot >= 3:
            ratio = son / tot
            if ratio >= 0.55:   score += 20
            elif ratio >= 0.40: score += 12
            elif ratio >= 0.25: score += 5

        # SIGNAL 3 : Corners
        if cor >= 9:    score += 22
        elif cor >= 7:  score += 17
        elif cor >= 5:  score += 12
        elif cor >= 4:  score += 8
        elif cor >= 2:  score += 3

        # SIGNAL 4 : Possession
        if pos >= 72:   score += 16
        elif pos >= 66: score += 12
        elif pos >= 60: score += 7

        return score

    h_score = team_score(h_son, h_tot, h_cor, h_pos, h_xg)
    a_score = team_score(a_son, a_tot, a_cor, a_pos, a_xg)

    # Score combine du match (les deux equipes)
    match_score = h_score + a_score

    # Criteres minimaux sur le MATCH ENTIER (pas par equipe)
    # Au moins 3 tirs cadres au total ET 5 corners au total ET xG > 0.5
    total_son = h_son + a_son
    total_cor = h_cor + a_cor
    total_xg  = h_xg + a_xg

    if total_son < 3:  return None
    if total_cor < 5:  return None
    if xg_ok and total_xg < 0.5: return None
    if h_score == 0 and a_score == 0: return None

    # Equipe dominante
    # Le score du match sert de base au momentum
    _ = match_score  # utilise pour le momentum global

    if h_score > a_score * 1.3:
        dom_side  = "home"
        dom_score = h_score
        dom_son   = h_son
        dom_cor   = h_cor
        dom_pos   = h_pos
        dom_xg    = h_xg
        dom_ratio = h_son / h_tot if h_tot >= 3 else 0
    elif a_score > h_score * 1.3:
        dom_side  = "away"
        dom_score = a_score
        dom_son   = a_son
        dom_cor   = a_cor
        dom_pos   = a_pos
        dom_xg    = a_xg
        dom_ratio = a_son / a_tot if a_tot >= 3 else 0
    else:
        dom_side  = "balanced"
        dom_score = max(h_score, a_score)
        dom_son   = max(h_son, a_son)
        dom_cor   = max(h_cor, a_cor)
        dom_pos   = max(h_pos, a_pos)
        dom_xg    = max(h_xg, a_xg)
        dom_ratio = 0

    # Momentum final : base sur pression dominante + bonus match actif
    momentum = min(dom_score, 70)
    # Bonus si les deux equipes poussent (match ouvert)
    if h_score > 5 and a_score > 5:
        momentum += min(int((h_score + a_score) / 10), 10)

    # Bonus score serre
    if diff == 0:   momentum += 8
    elif diff == 1: momentum += 4

    # Bonus ML
    if p_over25 >= 72:              momentum += 6
    if rec_over25:                  momentum += 4
    if xg_ok and (h_xg + a_xg) >= 2.5: momentum += 5

    momentum = min(max(int(momentum), 0), 100)

    return {
        "momentum":  momentum,
        "dom_side":  dom_side,
        "dom_score": dom_score,
        "dom_son":   dom_son,
        "dom_cor":   dom_cor,
        "dom_pos":   dom_pos,
        "dom_xg":    dom_xg,
        "dom_ratio": dom_ratio,
        "h_son": h_son, "a_son": a_son,
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
        "xg_ok":      xg_ok,
        "diff":       diff,
    }


def get_threshold(a, minute, diff):
    # Seuil de base strict
    base = 55

    # Ajustement selon score
    if diff == 0 and minute >= 60:   base -= 10
    elif diff == 0:                  base -= 5
    elif diff == 1 and minute >= 70: base -= 8
    elif diff == 1:                  base -= 4

    # Boost ML
    if a["rec_over25"]:              base -= 4
    if a["p_over25"] >= 75:          base -= 3
    if a["xg_ok"] and a["dom_xg"] >= 1.2: base -= 4

    return max(35, min(base, 70))


def check_second_half_goals(match, a, minute, hg, ag):
    """
    Scenario special : alerte 'Plus de buts en 2eme MT'
    Conditions :
    - Score 0-0 avec pression forte, OU
    - Equipe domicile mene 1-0 mais se fait dominer apres 35min
    - Equipe domicile perd 0-1 entre 35-45min avec occasions
    Retourne un message ou None
    """
    # Accepter aussi la mi-temps exacte (statut halftime, minute=45)
    is_halftime = str(match.get("period", "")).upper() in ("HT", "HALFTIME") or str(match.get("status", "")).lower() in ("halftime", "ht")
    if not is_halftime and (minute < 35 or minute > 45):
        return None
    if a is None:
        return None

    dom_son = a["dom_son"]
    dom_cor = a["dom_cor"]
    dom_pos = a["dom_pos"]
    dom_xg  = a["dom_xg"]

    # Criteres minimaux : au moins quelques occasions
    if dom_son < 2 or dom_cor < 3:
        return None

    trigger = False
    raison  = ""

    # Scenario 1 : 0-0 avec vraie pression en fin de 1ere MT
    if hg == 0 and ag == 0:
        if dom_son >= 3 and dom_cor >= 4 and dom_pos >= 58:
            trigger = True
            raison  = "Match nul avec forte pression - buts attendus en 2MT"

    # Scenario 2 : equipe domicile qui dominait se fait mener 0-1
    # Elle va pousser fort en 2eme MT pour egaliser
    if hg == 0 and ag == 1 and a["dom_side"] == "home":
        if dom_son >= 2 and dom_cor >= 3:
            trigger = True
            raison  = "Domicile domine mais mene 0-1 - reaction attendue en 2MT"

    # Scenario 3 : equipe domicile mene 1-0 mais se fait dominer
    # L'equipe visiteuse va pousser en 2eme MT
    if hg == 1 and ag == 0 and a["dom_side"] == "away":
        if dom_son >= 2 and dom_cor >= 3:
            trigger = True
            raison  = "Visiteur domine malgre 0-1 - egalisation probable en 2MT"

    if not trigger:
        return None

    league = str(match.get("league", {}).get("name", "?"))
    h_name = str(match.get("home_team", "Dom"))
    a_name = str(match.get("away_team", "Ext"))

    xg_str = ""
    if a["xg_ok"] and (a["h_xg"] + a["a_xg"]) > 0.1:
        xg_str = " | xG total: " + str(round(a["h_xg"] + a["a_xg"], 1))

    lines = [
        "⏱️ ALERTE 2EME MI-TEMPS - " + league,
        h_name + " " + str(hg) + "-" + str(ag) + " " + a_name + " | " + str(minute) + "min",
        "📈 " + raison,
        "📊 " + str(int(dom_son)) + " tirs cadres | " + str(int(dom_cor)) + " corners" + xg_str,
        "💡 Plus de buts en 2MT | Over 0.5 buts 2MT",
    ]
    return "\n".join(lines)


def build_alert(match, a, threshold):
    league  = str(match.get("league", {}).get("name", "?"))
    h_name  = str(match.get("home_team", "Dom"))
    a_name  = str(match.get("away_team", "Ext"))
    hg, ag  = get_score(match)
    minute  = get_minute(match)
    mom     = a["momentum"]
    total_g = hg + ag

    if a["dom_side"] == "home":
        scorer   = h_name
        son_disp = str(int(a["h_son"])) + " tirs cadres"
        cor_disp = str(int(a["h_cor"])) + " corners"
        pos_disp = str(int(a["h_pos"])) + "% poss."
    elif a["dom_side"] == "away":
        scorer   = a_name
        son_disp = str(int(a["a_son"])) + " tirs cadres"
        cor_disp = str(int(a["a_cor"])) + " corners"
        pos_disp = str(int(a["a_pos"])) + "% poss."
    else:
        scorer   = "Les deux equipes"
        son_disp = str(int(a["h_son"])) + "/" + str(int(a["a_son"])) + " tirs cadres"
        cor_disp = str(int(a["h_cor"])) + "/" + str(int(a["a_cor"])) + " corners"
        pos_disp = str(int(a["h_pos"])) + "/" + str(int(a["a_pos"])) + "% poss."

    adroit = ""
    if a["xg_ok"] and a["dom_xg"] >= 1.2:
        adroit = " (xG " + str(round(a["dom_xg"], 1)) + " 📈)"
    elif a["dom_ratio"] >= 0.45:
        adroit = " (adroit 🎯)"

    margin = mom - threshold
    if margin >= 15 or mom >= 78:
        emj = "🔴🔴"
        lvl = "ALERTE MAX"
    elif margin >= 7 or mom >= 63:
        emj = "🟠"
        lvl = "FORTE PRESSION"
    else:
        emj = "🟡"
        lvl = "PRESSION"

    bets = []
    if a["rec_over25"] or a["p_over25"] >= 65:
        bets.append("Over " + str(total_g) + ".5 (" + str(int(a["p_over25"])) + "%)")
    if a["rec_btts"] or a["p_btts"] >= 62:
        bets.append("BTTS (" + str(int(a["p_btts"])) + "%)")
    if a["favorite"] in ("H", "A") and a["rec_fav"]:
        fn = h_name if a["favorite"] == "H" else a_name
        bets.append(fn[:12] + " win (" + str(int(a["fav_prob"])) + "%)")
    bets.append("Next goal " + ("MT1" if minute < 43 else "MT2"))

    lines = [
        emj + " " + lvl + " - " + league,
        h_name + " " + str(hg) + "-" + str(ag) + " " + a_name + " | " + str(minute) + "min",
        "⚽ Prochain but: " + scorer + adroit,
        "📊 " + son_disp + " | " + cor_disp + " | " + pos_disp,
        "💡 " + " | ".join(bets),
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

                        # Pas d'alerte apres 85min
                        if minute > 85:
                            continue

                        # Max 3 alertes par match
                        if alerts_count.get(event_id, 0) >= 3:
                            continue

                        pred      = get_prediction(event_id)
                        a         = analyse(match, pred)

                        if a is None:
                            print("  [skip] " + h_name[:10] + " vs " + a_name[:10], flush=True)
                            continue

                        mom       = a["momentum"]
                        threshold = get_threshold(a, minute, diff)

                        print("  [" + str(minute) + "'] "
                              + h_name[:10] + " " + str(hg) + "-" + str(ag) + " " + a_name[:10]
                              + " | mom=" + str(mom) + "/" + str(threshold)
                              + " dom=" + a["dom_side"]
                              + " son=" + str(int(a["dom_son"]))
                              + " cor=" + str(int(a["dom_cor"]))
                              + " pos=" + str(int(a["dom_pos"])),
                              flush=True)

                        # Alerte uniquement a une minute donnee, pas de doublon
                        # Scenario special 2eme mi-temps (35-45min)
                        sh_key = event_id + "_2mt_" + str(minute // 20)
                        if sh_key not in alerts_sent:
                            sh_msg = check_second_half_goals(match, a, minute, hg, ag)
                            if sh_msg:
                                alerts_sent[sh_key] = True
                                await bot.send_message(
                                    chat_id=str(TELEGRAM_CHAT_ID),
                                    text=sh_msg
                                )
                                print("  >>> ALERTE 2MT: " + h_name + " vs " + a_name, flush=True)
                                await asyncio.sleep(1)

                        # 1 alerte max par tranche de 20 minutes par match
                        alert_key = event_id + "_" + str(minute // 20)
                        if mom >= threshold and alert_key not in alerts_sent:
                            alerts_sent[alert_key] = True
                            alerts_count[event_id] = alerts_count.get(event_id, 0) + 1
                            msg = build_alert(match, a, threshold)
                            await bot.send_message(
                                chat_id=str(TELEGRAM_CHAT_ID),
                                text=msg
                            )
                            print("  >>> ALERTE #" + str(alerts_count[event_id])
                                  + " : " + h_name + " vs " + a_name
                                  + " [" + str(minute) + "'] mom=" + str(mom),
                                  flush=True)
                            await asyncio.sleep(1)

                    except Exception as e:
                        print("  ERREUR match: " + str(e), flush=True)
                        traceback.print_exc()

            if len(alerts_sent) > 1000:
                for k in list(alerts_sent.keys())[:400]:
                    del alerts_sent[k]
            if len(pred_cache) > 200:
                for k in list(pred_cache.keys())[:100]:
                    del pred_cache[k]
            if len(alerts_count) > 100:
                for k in list(alerts_count.keys())[:50]:
                    del alerts_count[k]

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
