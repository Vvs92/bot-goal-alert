import requests
import logging
import os
import asyncio
import sys
from telegram import Bot

SPORTMONKS_TOKEN = os.environ.get("SPORTMONKS_TOKEN", "")
TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN", "")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "")

print("DEMARRAGE BOT DEBUG", flush=True)

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
            print("API error: " + r.text[:300], flush=True)
            return []
        d = r.json()
        all_f = d.get("data", [])
        filtered = [f for f in all_f if f.get("league_id") in LEAGUES]
        print(str(len(all_f)) + " matchs total, " + str(len(filtered)) + " dans nos ligues", flush=True)
        return filtered
    except Exception as e:
        print("ERREUR API: " + str(e), flush=True)
        return []


def debug_fixture(fixture):
    name = fixture.get("name", "unknown")
    print("=== DEBUG: " + name + " ===", flush=True)

    # Affiche la structure des stats brutes
    stats = fixture.get("statistics", [])
    print("Nb stats: " + str(len(stats)), flush=True)
    if stats:
        print("Exemple stat[0]: " + str(stats[0]), flush=True)
        if len(stats) > 1:
            print("Exemple stat[1]: " + str(stats[1]), flush=True)

    # Affiche la structure du state
    state = fixture.get("state", {})
    print("State: " + str(state), flush=True)

    # Affiche les cles disponibles
    print("Cles fixture: " + str(list(fixture.keys())), flush=True)


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
        state = fixture.get("state", {})
        if isinstance(state, dict):
            clock = state.get("clock", {})
            if isinstance(clock, dict):
                mm = clock.get("mm", 0)
                return mm or 0
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


def momentum(fixture):
    score = 0
    info = []

    minute = get_minute(fixture)
    if minute < 5:
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
    minute = get_minute(fixture)
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


async def run_forever():
    bot = Bot(token=TELEGRAM_TOKEN)
    print("BOUCLE INFINIE DEMARREE", flush=True)
    first_run = True

    while True:
        print("--- Check ---", flush=True)
        try:
            fixtures = get_fixtures()
            if not fixtures:
                print("Aucun match dans nos ligues", flush=True)
            else:
                # Debug uniquement sur le premier match du premier check
                if first_run and fixtures:
                    debug_fixture(fixtures[0])
                    first_run = False

                for f in fixtures:
                    fid = f.get("id")
                    minute = get_minute(f)
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
            print("ERREUR BOUCLE: " + str(e), flush=True)

        print("Prochaine verif dans " + str(INTERVAL) + "s", flush=True)
        await asyncio.sleep(INTERVAL)


if __name__ == "__main__":
    asyncio.run(run_forever())
