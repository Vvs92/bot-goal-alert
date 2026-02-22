import requests
import time
import schedule
import logging
import os
import asyncio
import sys
from telegram import Bot

SPORTMONKS_TOKEN = os.environ.get("SPORTMONKS_TOKEN", "")
TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN", "")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "")

print("DEMARRAGE BOT", flush=True)
print("TOKEN SM: " + str(bool(SPORTMONKS_TOKEN)), flush=True)
print("TOKEN TG: " + str(bool(TELEGRAM_TOKEN)), flush=True)
print("CHAT ID: " + str(bool(TELEGRAM_CHAT_ID)), flush=True)

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

THRESHOLD = 65
INTERVAL = 90

logging.basicConfig(format="%(asctime)s %(message)s", level=logging.INFO, stream=sys.stdout)
log = logging.getLogger(__name__)

URL = "https://api.sportmonks.com/v3/football"

alerts = {}


def get_fixtures():
    try:
        r = requests.get(
            URL + "/livescores/inplay",
            params={
                "api_token": SPORTMONKS_TOKEN,
                "include": "participants;scores;state;statistics",
                "per_page": 50
            },
            timeout=15
        )
        print("API status: " + str(r.status_code), flush=True)
        d = r.json()
        print("API data: " + str(d)[:300], flush=True)
        all_f = d.get("data", [])
        return [f for f in all_f if f.get("league_id") in LEAGUES]
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


def stat_val(stats, code, tid=None):
    try:
        for s in stats:
            if s.get("type", {}).get("code") == code:
                if tid is None or s.get("participant_id") == tid:
                    v = s.get("data", {}).get("value", 0)
                    return float(v) if v else 0.0
    except Exception:
        pass
    return 0.0


def momentum(fixture):
    score = 0
    info = []
    state = fixture.get("state", {})
    minute = state.get("clock", {}).get("mm", 0) or 0
    if minute < 10:
        return 0, []
    stats = fixture.get("statistics", [])
    hid = None
    aid = None
    for p in fixture.get("participants", []):
        loc = p.get("meta", {}).get("location", "")
        if loc == "home":
            hid = p.get("id")
        else:
            aid = p.get("id")
    xg = stat_val(stats, "expected-goals", hid) + stat_val(stats, "expected-goals", aid)
    son = stat_val(stats, "shots-on-target", hid) + stat_val(stats, "shots-on-target", aid)
    sib = stat_val(stats, "shots-insidebox", hid) + stat_val(stats, "shots-insidebox", aid)
    cor = stat_val(stats, "corners", hid) + stat_val(stats, "corners", aid)
    dan = stat_val(stats, "dangerous-attacks", hid) + stat_val(stats, "dangerous-attacks", aid)
    tot = stat_val(stats, "shots-total", hid) + stat_val(stats, "shots-total", aid)
    if xg >= 3.0:
        score += 25
        info.append("xG eleve: " + str(round(xg, 2)))
    elif xg >= 2.0:
        score += 18
        info.append("xG: " + str(round(xg, 2)))
    elif xg >= 1.0:
        score += 10
        info.append("xG: " + str(round(xg, 2)))
    if son >= 10:
        score += 15
        info.append(str(int(son)) + " tirs cadres")
    elif son >= 6:
        score += 9
        info.append(str(int(son)) + " tirs cadres")
    elif son >= 3:
        score += 5
        info.append(str(int(son)) + " tirs cadres")
    if sib >= 12:
        score += 15
        info.append(str(int(sib)) + " tirs surface")
    elif sib >= 7:
        score += 9
        info.append(str(int(sib)) + " tirs surface")
    elif sib >= 4:
        score += 5
        info.append(str(int(sib)) + " tirs surface")
    if cor >= 10:
        score += 10
        info.append(str(int(cor)) + " corners")
    elif cor >= 6:
        score += 6
        info.append(str(int(cor)) + " corners")
    elif cor >= 3:
        score += 3
        info.append(str(int(cor)) + " corners")
    if dan >= 60:
        score += 10
        info.append(str(int(dan)) + " att dangereuses")
    elif dan >= 35:
        score += 6
        info.append(str(int(dan)) + " att dangereuses")
    elif dan >= 20:
        score += 3
        info.append(str(int(dan)) + " att dangereuses")
    if tot > 0 and son / tot >= 0.5:
        score += 5
        info.append("Bonne precision")
    mh = minute % 45
    if 40 <= mh <= 45 or minute >= 85:
        score += 5
        info.append("Fin periode min " + str(minute))
    return min(score, 100), info


async def send_alert(bot, fixture, score, info):
    lid = fixture.get("league_id")
    league = LEAGUES.get(lid, "Ligue")
    h = team_name(fixture, True)
    a = team_name(fixture, False)
    hg = get_goals(fixture, True)
    ag = get_goals(fixture, False)
    minute = fixture.get("state", {}).get("clock", {}).get("mm", 0) or 0
    gauge = "X" * int(score / 10) + "." * (10 - int(score / 10))
    if score >= 85:
        lvl = "ALERTE MAX"
    elif score >= 75:
        lvl = "FORTE PRESSION"
    else:
        lvl = "PRESSION"
    msg = (
        "BUT POTENTIEL - " + lvl + "\n\n"
        + league + "\n"
        + h + " " + str(hg) + " - " + str(ag) + " " + a + "\n"
        + "Min: " + str(minute) + "'\n"
        + "Score: " + str(score) + "/100\n"
        + gauge + "\n\n"
        + "\n".join(["- " + i for i in info])
    )
    try:
        await bot.send_message(chat_id=TELEGRAM_CHAT_ID, text=msg)
        print("Alerte envoyee: " + h + " vs " + a, flush=True)
    except Exception as e:
        print("ERREUR TG: " + str(e), flush=True)


async def check():
    print("--- Check ---", flush=True)
    try:
        bot = Bot(token=TELEGRAM_TOKEN)
        fixtures = get_fixtures()
        if not fixtures:
            print("Aucun match", flush=True)
            return
        print(str(len(fixtures)) + " match(s)", flush=True)
        for f in fixtures:
            fid = f.get("id")
            minute = f.get("state", {}).get("clock", {}).get("mm", 0) or 0
            sc, info = momentum(f)
            h = team_name(f, True)
            a = team_name(f, False)
            print("[" + str(minute) + "] " + h + " vs " + a + " -> " + str(sc), flush=True)
            key = str(fid) + "_" + str(minute // 15)
            if sc >= THRESHOLD and key not in alerts:
                alerts[key] = True
                await send_alert(bot, f, sc, info)
                await asyncio.sleep(2)
        if len(alerts) > 500:
            for k in list(alerts.keys())[:250]:
                del alerts[k]
    except Exception as e:
        print("ERREUR CHECK: " + str(e), flush=True)


def run():
    asyncio.run(check())


def main():
    print("BOT DEMARRE", flush=True)
    run()
    schedule.every(INTERVAL).seconds.do(run)
    while True:
        schedule.run_pending()
        time.sleep(10)


if __name__ == "__main__":
    main()
