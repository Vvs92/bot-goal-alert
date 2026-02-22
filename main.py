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
    72: "Bundesliga 2",
    48: "Championship",
    37: "Super Lig",
    377: "Belgian Pro League",
    955: "Saudi Pro League"
}

THRESHOLD = 50
INTERVAL = 90

logging.basicConfig(format="%(asctime)s %(message)s", level=logging.INFO, stream=sys.stdout)

URL = "https://api.sportmonks.com/v3/football"
alerts = {}


def get_fixtures():
    try:
        r = requests.get(
            URL + "/livescores/inplay",
            params={
                "api_token": SPORTMONKS_TOKEN,
                "include": "participants;scores;state;statistics.type",
                "per_page": 50
            },
            timeout=15
        )
        print("API status: " + str(r.status_code), flush=True)
        if r.status_code != 200:
            print("API error: " + r.text[:200], flush=True)
            return []
        d = r.json()
        all_f = d.get("data", [])
        filtered = [f for f in all_f if f.get("league_id") in LEAGUES]
        print(str(len(all_f)) + " matchs total, " + str(len(filtered)) + " dans nos ligues", flush=True)
        return filtered
    except Exception as e:
        print("ERREUR API: " + str(e), flush=True)
        return []


def team_name(fixture, home=True):
    try:
        for p in fixture.get("participants", []):
            loc = p.get("meta", {}).get("location", "")
            if home and loc == "home":
                return p.get("name", "Home")
            if not home and loc == "away":
                return p.get("name", "Away")
    except Exception:
        pass
    return "Home" if home else "Away"


def get_goals(fixture, home=True):
    try:
        for s in fixture.get("scores", []):
            if s.get("description") == "CURRENT":
                sd = s.get("score", {})
                if home:
                    return sd.get("goals", 0) or 0
                else:
                    return sd.get("participant", 0) or 0
    except Exception:
        pass
    return 0


def get_minute(fixture):
    try:
        starting = fixture.get("starting_at_timestamp", 0)
        if starting:
            elapsed = int((time.time() - starting) / 60)
            if 0 < elapsed < 120:
                return elapsed
    except Exception:
        pass
    return 0


def stat_val(stats, code, tid=None):
    try:
        for s in stats:
            t = s.get("type", {})
            tcode = t.get("code", "") if isinstance(t, dict) else ""
            if tcode == code:
                if tid is None or s.get("participant_id") == tid:
                    v = s.get("data", {}).get("value", 0)
                    return float(v) if v else 0.0
    except Exception:
        pass
    return 0.0


def get_dominant_team(fixture, stats):
    """Trouve l'equipe qui domine le match"""
    hid = None
    aid = None
    hname = ""
    aname = ""
    for p in fixture.get("participants", []):
        loc = p.get("meta", {}).get("location", "")
        if loc == "home":
            hid = p.get("id")
            hname = p.get("name", "Home")
        else:
            aid = p.get("id")
            aname = p.get("name", "Away")

    h_dan = stat_val(stats, "dangerous-attacks", hid)
    a_dan = stat_val(stats, "dangerous-attacks", aid)
    h_son = stat_val(stats, "shots-on-target", hid)
    a_son = stat_val(stats, "shots-on-target", aid)
    h_cor = stat_val(stats, "corners", hid)
    a_cor = stat_val(stats, "corners", aid)

    h_total = h_dan + (h_son * 3) + h_cor
    a_total = a_dan + (a_son * 3) + a_cor

    if h_total > a_total * 1.4:
        return hname, "home", h_total, a_total
    elif a_total > h_total * 1.4:
        return aname, "away", a_total, h_total
    else:
        return None, "balanced", max(h_total, a_total), min(h_total, a_total)


def momentum(fixture):
    score = 0
    info = []

    minute = get_minute(fixture)
    stats = fixture.get("statistics", [])

    hid = None
    aid = None
    for p in fixture.get("participants", []):
        loc = p.get("meta", {}).get("location", "")
        if loc == "home":
            hid = p.get("id")
        else:
            aid = p.get("id")

    son = stat_val(stats, "shots-on-target", hid) + stat_val(stats, "shots-on-target", aid)
    sib = stat_val(stats, "shots-insidebox", hid) + stat_val(stats, "shots-insidebox", aid)
    cor = stat_val(stats, "corners", hid) + stat_val(stats, "corners", aid)
    dan = stat_val(stats, "dangerous-attacks", hid) + stat_val(stats, "dangerous-attacks", aid)
    tot = stat_val(stats, "shots-total", hid) + stat_val(stats, "shots-total", aid)

    if son >= 10:
        score += 25
        info.append(("shots", str(int(son)) + " tirs cadres"))
    elif son >= 6:
        score += 16
        info.append(("shots", str(int(son)) + " tirs cadres"))
    elif son >= 3:
        score += 8
        info.append(("shots", str(int(son)) + " tirs cadres"))

    if sib >= 12:
        score += 25
        info.append(("box", str(int(sib)) + " tirs dans la surface"))
    elif sib >= 7:
        score += 16
        info.append(("box", str(int(sib)) + " tirs dans la surface"))
    elif sib >= 4:
        score += 8
        info.append(("box", str(int(sib)) + " tirs dans la surface"))

    if cor >= 10:
        score += 15
        info.append(("corners", str(int(cor)) + " corners"))
    elif cor >= 6:
        score += 9
        info.append(("corners", str(int(cor)) + " corners"))
    elif cor >= 3:
        score += 4
        info.append(("corners", str(int(cor)) + " corners"))

    if dan >= 80:
        score += 20
        info.append(("pressure", str(int(dan)) + " attaques dangereuses"))
    elif dan >= 50:
        score += 13
        info.append(("pressure", str(int(dan)) + " attaques dangereuses"))
    elif dan >= 25:
        score += 6
        info.append(("pressure", str(int(dan)) + " attaques dangereuses"))

    if tot > 0 and son / tot >= 0.5:
        score += 10
        info.append(("precision", "Precision " + str(int(son / tot * 100)) + "%"))

    if minute > 0:
        mh = minute % 45
        if 40 <= mh <= 45 or minute >= 85:
            score += 5
            info.append(("endgame", "Fin de periode min " + str(minute)))

    return min(score, 100), info, son, sib, cor, dan, tot, minute


def build_recommendations(score, info_tags, son, sib, cor, dan, tot, minute, hg, ag, dominant_team, dominant_side):
    """Genere les recommandations de paris selon le contexte"""
    recs = []
    tags = [t[0] for t in info_tags]
    total_goals = hg + ag

    # --- PROCHAIN BUT ---
    if score >= 60 and ("shots" in tags or "box" in tags):
        if dominant_side == "home":
            recs.append("⚽ PROCHAIN BUT -> " + dominant_team + " (domine)")
        elif dominant_side == "away":
            recs.append("⚽ PROCHAIN BUT -> " + dominant_team + " (domine)")
        else:
            recs.append("⚽ PROCHAIN BUT -> Les deux peuvent scorer (match ouvert)")

    # --- OVER BUTS ---
    if score >= 55 and (son >= 6 or sib >= 6):
        current = "Over " + str(total_goals) + ".5 buts"
        recs.append("📈 " + current + " dans le match")
        if minute < 45:
            recs.append("📈 Over 0.5 buts ce reste de 1ere MT")
        elif minute < 75:
            recs.append("📈 Over 0.5 buts ce reste de 2eme MT")

    # --- CORNERS ---
    if "corners" in tags and cor >= 5:
        next_cor = int(cor) + 2
        recs.append("🚩 Prochain corner probable / Over " + str(next_cor) + ".5 corners")

    # --- EQUIPE ETOUFFEE ---
    if dominant_team and score >= 55:
        recs.append("😤 " + dominant_team + " etouffe l'adversaire -> miser sur elle")

    # --- BTTS ---
    if hg == 0 and ag == 0 and score >= 55 and minute >= 30:
        recs.append("🎯 BTTS (les deux marquent) possible - 0-0 sous pression")
    elif hg > 0 and ag == 0 and score >= 55:
        recs.append("🎯 BTTS possible - equipe a 0 sous forte pression")
    elif hg == 0 and ag > 0 and score >= 55:
        recs.append("🎯 BTTS possible - equipe a 0 sous forte pression")

    # --- CARTONS ---
    if dan >= 70 and score >= 55:
        recs.append("🟨 Pression intense -> cartons probables")

    if not recs:
        recs.append("👀 A surveiller - pression en hausse")

    return recs


async def send_alert(bot, fixture, score, info_tags, son, sib, cor, dan, tot, minute):
    lid = fixture.get("league_id")
    league = LEAGUES.get(lid, "Ligue")
    h = team_name(fixture, True)
    a = team_name(fixture, False)
    hg = get_goals(fixture, True)
    ag = get_goals(fixture, False)

    stats = fixture.get("statistics", [])
    dominant_team, dominant_side, dom_score, weak_score = get_dominant_team(fixture, stats)

    gauge = "X" * int(score / 10) + "." * (10 - int(score / 10))

    if score >= 70:
        lvl = "ALERTE MAX"
        emoji = "🔴"
    elif score >= 55:
        lvl = "FORTE PRESSION"
        emoji = "🟠"
    else:
        lvl = "PRESSION"
        emoji = "🟡"

    stats_lines = "\n".join(["  • " + t[1] for t in info_tags])
    recs = build_recommendations(score, info_tags, son, sib, cor, dan, tot, minute, hg, ag, dominant_team, dominant_side)
    recs_lines = "\n".join(["  → " + r for r in recs])

    msg = (
        emoji + " " + lvl + " - BUT POTENTIEL\n"
        + "━━━━━━━━━━━━━━━━━━━━\n"
        + "🏆 " + league + "\n"
        + "⚔️  " + h + " " + str(hg) + " - " + str(ag) + " " + a + "\n"
        + "⏱️  " + str(minute) + "' | Score momentum: " + str(score) + "/100\n"
        + gauge + "\n"
        + "━━━━━━━━━━━━━━━━━━━━\n"
        + "📊 STATS:\n"
        + stats_lines + "\n"
        + "━━━━━━━━━━━━━━━━━━━━\n"
        + "💡 QUOI JOUER SUR BETIFY:\n"
        + recs_lines + "\n"
        + "━━━━━━━━━━━━━━━━━━━━\n"
        + "⚠️ Parie de facon responsable"
    )

    try:
        await bot.send_message(chat_id=TELEGRAM_CHAT_ID, text=msg)
        print("Alerte envoyee: " + h + " vs " + a + " score=" + str(score), flush=True)
    except Exception as e:
        print("ERREUR TG: " + str(e), flush=True)


async def run_forever():
    bot = Bot(token=TELEGRAM_TOKEN)
    print("BOUCLE INFINIE DEMARREE - seuil=" + str(THRESHOLD), flush=True)

    while True:
        print("--- Check ---", flush=True)
        try:
            fixtures = get_fixtures()
            if not fixtures:
                print("Aucun match dans nos ligues", flush=True)
            else:
                for f in fixtures:
                    fid = f.get("id")
                    minute = get_minute(f)
                    sc, info_tags, son, sib, cor, dan, tot, minute = momentum(f)
                    h = team_name(f, True)
                    a = team_name(f, False)
                    hg = get_goals(f, True)
                    ag = get_goals(f, False)
                    print("[" + str(minute) + "'] " + h + " " + str(hg) + "-" + str(ag) + " " + a + " -> " + str(sc), flush=True)
                    key = str(fid) + "_" + str(minute // 15)
                    if sc >= THRESHOLD and key not in alerts:
                        alerts[key] = True
                        await send_alert(bot, f, sc, info_tags, son, sib, cor, dan, tot, minute)
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
