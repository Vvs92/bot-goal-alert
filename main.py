import requests
import logging
import os
import asyncio
import sys
import time
import json
import traceback
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
    79: "Bundesliga 2",
    9: "Championship",
    72: "Eredivisie",
    600: "Super Lig",
    208: "Belgian Pro League",
    501: "Premiership (Ecosse)",
}

URL = "https://api.sportmonks.com/v3/football"


def debug_fixture(fixture_id):
    try:
        r = requests.get(
            URL + "/fixtures/" + str(fixture_id),
            params={
                "api_token": SPORTMONKS_TOKEN,
                "include": "participants;scores;state;statistics.type;events.type"
            },
            timeout=15
        )
        print("=== DEBUG FIXTURE " + str(fixture_id) + " status=" + str(r.status_code) + " ===", flush=True)
        if r.status_code != 200:
            return
        d = r.json().get("data", {})

        # STATE
        state = d.get("state", {})
        print("STATE: " + json.dumps(state, default=str)[:400], flush=True)

        # TIMESTAMP
        ts = d.get("starting_at_timestamp", 0)
        elapsed = int((time.time() - ts) / 60) if ts else 0
        print("TIMESTAMP=" + str(ts) + " ELAPSED=" + str(elapsed) + "min", flush=True)

        # SCORES
        scores = d.get("scores", [])
        print("SCORES (" + str(len(scores)) + "):", flush=True)
        for s in scores:
            print("  desc=" + str(s.get("description")) + " | " + json.dumps(s.get("score", {})), flush=True)

        # STATS
        stats = d.get("statistics", [])
        print("STATS: " + str(len(stats)) + " entrees", flush=True)
        codes_found = set()
        for s in stats:
            t = s.get("type", {})
            code = t.get("code", "?") if isinstance(t, dict) else "?"
            codes_found.add(code)
        print("CODES STATS: " + str(sorted(codes_found)), flush=True)

        # EVENTS
        events = d.get("events", [])
        print("EVENTS: " + str(len(events)) + " entrees", flush=True)
        for ev in events[:8]:
            t = ev.get("type", {})
            code = t.get("code", "?") if isinstance(t, dict) else "?"
            print("  min=" + str(ev.get("minute")) + " code=" + code + " team=" + str(ev.get("participant_id")), flush=True)

    except Exception as e:
        print("ERREUR debug_fixture: " + str(e), flush=True)
        traceback.print_exc()


async def run_forever():
    bot = Bot(token=TELEGRAM_TOKEN)
    print("BOUCLE DEBUG DEMARREE", flush=True)
    already_debugged = set()

    while True:
        try:
            print("--- Check ---", flush=True)
            r = requests.get(
                URL + "/livescores/inplay",
                params={
                    "api_token": SPORTMONKS_TOKEN,
                    "include": "participants;scores;state",
                    "per_page": 50
                },
                timeout=15
            )
            print("Livescores status: " + str(r.status_code), flush=True)

            if r.status_code == 200:
                all_f = r.json().get("data", [])
                filtered = [f for f in all_f if f.get("league_id") in LEAGUES]
                print(str(len(all_f)) + " live, " + str(len(filtered)) + " dans nos ligues", flush=True)

                for f in filtered:
                    fid = f.get("id")
                    ts = f.get("starting_at_timestamp", 0)
                    elapsed = int((time.time() - ts) / 60) if ts else 0

                    h, a = "?", "?"
                    for p in f.get("participants", []):
                        loc = p.get("meta", {}).get("location", "")
                        if loc == "home":
                            h = str(p.get("name", "?"))
                        elif loc == "away":
                            a = str(p.get("name", "?"))

                    hg, ag = 0, 0
                    for s in f.get("scores", []):
                        desc = s.get("description", "")
                        sd = s.get("score", {})
                        print("  SCORE_RAW desc=" + desc + " goals=" + str(sd.get("goals")) + " participant=" + str(sd.get("participant")), flush=True)
                        if desc == "CURRENT":
                            hg = int(sd.get("goals", 0) or 0)
                            ag = int(sd.get("participant", 0) or 0)

                    state = f.get("state", {})
                    clock = state.get("clock", {}) if isinstance(state, dict) else {}
                    mm = clock.get("mm") if isinstance(clock, dict) else None
                    state_name = state.get("name", "?") if isinstance(state, dict) else "?"

                    print("Match: " + h + " " + str(hg) + "-" + str(ag) + " " + a
                          + " | elapsed=" + str(elapsed) + "min"
                          + " | clock.mm=" + str(mm)
                          + " | state=" + state_name, flush=True)

                    # Debug complet une seule fois par match
                    if fid not in already_debugged:
                        already_debugged.add(fid)
                        debug_fixture(fid)

        except Exception as e:
            print("ERREUR BOUCLE: " + str(e), flush=True)
            traceback.print_exc()

        print("Prochaine verif dans 60s", flush=True)
        await asyncio.sleep(60)


if __name__ == "__main__":
    asyncio.run(run_forever())
