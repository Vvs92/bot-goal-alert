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
    79: "Bundesliga 2",
    9: "Championship",
    72: "Eredivisie",
    600: "Super Lig",
    208: "Belgian Pro League",
    501: "Premiership (Ecosse)",
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
                return str(p.get("name", "Home"))
            if not home and loc == "away":
                return str(p.get("name", "Away"))
    except Exception:
        pass
    return "Home" if home else "Away"


def get_goals(fixture, home=True):
    try:
        for s in fixture.get("scores", []):
            if s.get("description") == "CURRENT":
                sd = s.get("score", {})
                if home:
                    return int(sd.get("goals", 0) or 0)
                else:
                    return int(sd.get("participant", 0) or 0)
    except Exception:
        pass
    return 0


def get_minute(fixture):
    try:
        starting = fixture.get("starting_at_timestamp", 0)
        if starting:
            elapsed = int((time.time() - starting) / 60)
            if 0 < elapsed < 130:
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
    hid = None
    aid = None
    hname = "Home"
    aname = "Away"
    for p in fixture.get("participants", []):
        loc = p.get("meta", {}).get("location", "")
        if loc == "home":
            hid = p.get("id")
            hname = str(p.get("name", "Home"))
        else:
            aid = p.get("id")
            aname = str(p.get("name", "Away"))

    h_dan = stat_val(stats, "dangerous-attacks", hid)
    a_dan = stat_val(stats, "dangerous-attacks", aid)
    h_son = stat_val(stats, "shots-on-target", hid)
    a_son = stat_val(stats, "shots-on-target", aid)
    h_cor = stat_val(stats, "corners", hid)
    a_cor = stat_val(stats, "corners", aid)

    h_total = h_dan + (h_son * 3) + h_cor
    a_total = a_dan + (a_son * 3) + a_cor

    if h_total > a_total * 1.4:
        return hname, "home"
    elif a_total > h_total * 1.4:
        return aname, "away"
    else:
        return None, "balanced"


def momentum(fixture):
    score = 0
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
    elif son >= 6:
        score += 16
    elif son >= 3:
        score += 8

    if sib >= 12:
        score += 25
    elif sib >= 7:
        score += 16
    elif sib >= 4:
        score += 8

    if cor >= 10:
        score += 15
    elif cor >= 6:
        score += 9
    elif cor >= 3:
        score += 4

    if dan >= 80:
        score += 20
    elif dan >= 50:
        score += 13
    elif dan >= 25:
        score += 6

    if tot > 0 and son / tot >= 0.5:
        score += 10

    return min(score, 100), son, sib, cor, dan, tot


def build_message(fixture, score, son, sib, cor, dan, tot):
    minute = get_minute(fixture)
    lid = fixture.get("league_id")
    league = str(LEAGUES.get(lid, "Ligue"))
    h = team_name(fixture, True)
    a = team_name(fixture, False)
    hg = get_goals(fixture, True)
    ag = get_goals(fixture, False)
    stats = fixture.get("statistics", [])
    dominant_team, dominant_side = get_dominant_team(fixture, stats)
    total_goals = hg + ag

    gauge = "\U0001f7e9" * int(score / 10) + "\u2b1c" * (10 - int(score / 10))

    if score >= 70:
        lvl = "ALERTE MAX"
        emoji = "\U0001f534"
    elif score >= 55:
        lvl = "FORTE PRESSION"
        emoji = "\U0001f7e0"
    else:
        lvl = "PRESSION"
        emoji = "\U0001f7e1"

    stats_lines = []
    if son >= 3:
        stats_lines.append("  \u2022 " + str(int(son)) + " tirs cadres")
    if sib >= 3:
        stats_lines.append("  \u2022 " + str(int(sib)) + " tirs dans la surface")
    if cor >= 3:
        stats_lines.append("  \u2022 " + str(int(cor)) + " corners")
    if dan >= 20:
        stats_lines.append("  \u2022 " + str(int(dan)) + " attaques dangereuses")

    recs = []

    if score >= 55 and (son >= 6 or sib >= 6):
        if dominant_side == "home":
            recs.append("  \u2192 \u26bd Prochain but: " + h + " (domine)")
        elif dominant_side == "away":
            recs.append("  \u2192 \u26bd Prochain but: " + a + " (domine)")
        else:
            recs.append("  \u2192 \u26bd Prochain but: Match ouvert")

    if score >= 55 and (son >= 5 or sib >= 5):
        recs.append("  \u2192 \U0001f4c8 Over " + str(total_goals) + ".5 buts dans le match")
        if minute < 46:
            recs.append("  \u2192 \U0001f4c8 Over 0.5 buts reste 1ere MT")
        else:
            recs.append("  \u2192 \U0001f4c8 Over 0.5 buts reste 2eme MT")

    if cor >= 5:
        next_cor = int(cor) + 2
        recs.append("  \u2192 \U0001f6a9 Plus de corners / Over " + str(next_cor) + ".5 corners")

    if dominant_team and score >= 55:
        recs.append("  \u2192 \U0001f621 " + dominant_team + " etouffe l'adversaire")

    if hg == 0 and ag == 0 and score >= 55 and minute >= 30:
        recs.append("  \u2192 \U0001f3af BTTS possible - 0-0 sous forte pression")
    elif total_goals > 0 and (hg == 0 or ag == 0) and score >= 55:
        recs.append("  \u2192 \U0001f3af BTTS possible - equipe a 0 sous pression")

    if dan >= 70:
        recs.append("  \u2192 \U0001f7e8 Cartons probables (intensite elevee)")

    if not recs:
        recs.append("  \u2192 \U0001f440 A surveiller - pression en hausse")

    stats_text = "\n".join(stats_lines) if stats_lines else "  \u2022 Stats en cours"
    recs_text = "\n".join(recs)

    sep = "\u2501" * 20

    msg = (
        emoji + " " + lvl + " - BUT POTENTIEL\n"
        + sep + "\n"
        + "\U0001f3c6 " + league + "\n"
        + "\u2694\ufe0f  " + h + " " + str(hg) + " - " + str(ag) + " " + a + "\n"
        + "\u23f1\ufe0f  " + str(minute) + "' | Score momentum: " + str(score) + "/100\n"
        + gauge + "\n"
        + sep + "\n"
        + "\U0001f4ca STATS:\n"
        + stats_text + "\n"
        + sep + "\n"
        + "\U0001f4a1 QUOI JOUER SUR BETIFY:\n"
        + recs_text + "\n"
        + sep + "\n"
        + "\u26a0\ufe0f Parie de facon responsable"
    )

    return msg


async def send_alert(bot, fixture, score, son, sib, cor, dan, tot):
    try:
        msg = build_message(fixture, score, son, sib, cor, dan, tot)
        h = team_name(fixture, True)
        a = team_name(fixture, False)
        await bot.send_message(chat_id=str(TELEGRAM_CHAT_ID), text=msg)
        print("Alerte envoyee: " + h + " vs " + a + " score=" + str(score), flush=True)
    except Exception as e:
        print("ERREUR ALERTE: " + str(e), flush=True)


async def run_forever():
    bot = Bot(token=TELEGRAM_TOKEN)
    print("BOUCLE DEMARREE - seuil=" + str(THRESHOLD), flush=True)

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
                    sc, son, sib, cor, dan, tot = momentum(f)
                    h = team_name(f, True)
                    a = team_name(f, False)
                    hg = get_goals(f, True)
                    ag = get_goals(f, False)
                    print("[" + str(minute) + "'] " + h + " " + str(hg) + "-" + str(ag) + " " + a + " -> " + str(sc), flush=True)
                    key = str(fid) + "_" + str(minute // 15)
                    if sc >= THRESHOLD and key not in alerts:
                        alerts[key] = True
                        await send_alert(bot, f, sc, son, sib, cor, dan, tot)
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
