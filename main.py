import requests
import time
import schedule
import logging
import os
import asyncio
from telegram import Bot

SPORTMONKS_TOKEN = os.environ.get("SPORTMONKS_TOKEN", "")
TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN", "")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "")

LEAGUES_TO_WATCH = {
    2:    "Champions League",
    5:    "Europa League",
    8:    "Premier League",
    564:  "La Liga",
    384:  "Serie A",
    82:   "Bundesliga",
    301:  "Ligue 1",
    72:   "Bundesliga 2",
    48:   "Championship",
    37:   "Super Lig",
    377:  "Belgian Pro League",
    955:  "Saudi Pro League",
}

ALERT_THRESHOLD = 65
CHECK_INTERVAL = 90

logging.basicConfig(
    format='%(asctime)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

BASE_URL = "https://api.sportmonks.com/v3/football"
HEADERS = {"Authorization": f"Bearer {SPORTMONKS_TOKEN}"}

sent_alerts = {}


def get_live_fixtures():
    try:
        response = requests.get(
            f"{BASE_URL}/livescores/inplay",
            headers=HEADERS,
            params={
                "include": "participants;scores;state;statistics",
                "per_page": 50
            },
            timeout=15
        )
        data = response.json()
        fixtures = data.get("data", [])
        filtered = [f for f in fixtures if f.get("league_id") in LEAGUES_TO_WATCH]
        return filtered
    except Exception as e:
        logger.error(f"Erreur get_live_fixtures: {e}")
        return []


def get_team_name(fixture, is_home=True):
    try:
        participants = fixture.get("participants", [])
        for p in participants:
            meta = p.get("meta", {})
            location = meta.get("location", "")
            if is_home and location == "home":
                return p.get("name", "Home")
            elif not is_home and location == "away":
                return p.get("name", "Away")
        return "Home" if is_home else "Away"
    except:
        return "Home" if is_home else "Away"


def get_score(fixture, is_home=True):
    try:
        scores = fixture.get("scores", [])
        for s in scores:
            if s.get("description") == "CURRENT":
                score_data = s.get("score", {})
                if is_home:
                    return score_data.get("goals", 0) or 0
                else:
                    return score_data.get("participant", 0) or 0
        return 0
    except:
        return 0


def get_stat_value(statistics, stat_type, team_id=None):
    try:
        for stat in statistics:
            type_data = stat.get("type", {})
            if type_data.get("code") == stat_type:
                if team_id is None or stat.get("participant_id") == team_id:
                    val = stat.get("data", {}).get("value", 0)
                    return float(val) if val else 0
        return 0
    except:
        return 0


def calculate_momentum_score(fixture):
    score = 0
    details = []

    state = fixture.get("state", {})
    clock = state.get("clock", {})
    minute = clock.get("mm", 0) or 0

    if minute < 10:
        return 0, []

    statistics = fixture.get("statistics", [])

    participants = fixture.get("participants", [])
    home_id = None
    away_id = None
    for p in participants:
        meta = p.get("meta", {})
        if meta.get("location") == "home":
            home_id = p.get("id")
        else:
            away_id = p.get("id")

    shots_on_h = get_stat_value(statistics, "shots-on-target", home_id)
    shots_on_a = get_stat_value(statistics, "shots-on-target", away_id)
    shots_total_h = get_stat_value(statistics, "shots-total", home_id)
    shots_total_a = get_stat_value(statistics, "shots-total", away_id)
    dangerous_h = get_stat_value(statistics, "shots-insidebox", home_id)
    dangerous_a = get_stat_value(statistics, "shots-insidebox", away_id)
    corners_h = get_stat_value(statistics, "corners", home_id)
    corners_a = get_stat_value(statistics, "corners", away_id)
    dangerous_att_h = get_stat_value(statistics, "dangerous-attacks", home_id)
    dangerous_att_a = get_stat_value(statistics, "dangerous-attacks", away_id)
    xg_h = get_stat_value(statistics, "expected-goals", home_id)
    xg_a = get_stat_value(statistics, "expected-goals", away_id)

    total_shots_on = shots_on_h + shots_on_a
    total_dangerous = dangerous_h + dangerous_a
    total_corners = corners_h + corners_a
    total_dan_att = dangerous_att_h + dangerous_att_a
    total_xg = xg_h + xg_a
    total_shots = shots_total_h + shots_total_a

    if total_xg >= 3.0:
        score += 25
        details.append(f"📊 xG élevé: {total_xg:.2f}")
    elif total_xg >= 2.0:
        score += 18
        details.append(f"📊 xG: {total_xg:.2f}")
    elif total_xg >= 1.0:
        score += 10
        details.append(f"📊 xG: {total_xg:.2f}")

    if total_shots_on >= 10:
        score += 15
        details.append(f"🎯 {int(total_shots_on)} tirs cadrés")
    elif total_shots_on >= 6:
        score += 9
        details.append(f"🎯 {int(total_shots_on)} tirs cadrés")
    elif total_shots_on >= 3:
        score += 5
        details.append(f"🎯 {int(total_shots_on)} tirs cadrés")

    if total_dangerous >= 12:
        score += 15
        details.append(f"📦 {int(total_dangerous)} tirs dans la surface")
    elif total_dangerous >= 7:
        score += 9
        details.append(f"📦 {int(total_dangerous)} tirs dans la surface")
    elif total_dangerous >= 4:
        score += 5
        details.append(f"📦 {int(total_dangerous)} tirs dans la surface")

    if total_corners >= 10:
        score += 10
        details.append(f"🚩 {int(total_corners)} corners")
    elif total_corners >= 6:
        score += 6
        details.append(f"🚩 {int(total_corners)} corners")
    elif total_corners >= 3:
        score += 3
        details.append(f"🚩 {int(total_corners)} corners")

    if total_dan_att >= 60:
        score += 10
        details.append(f"⚡ {int(total_dan_att)} attaques dangereuses")
    elif total_dan_att >= 35:
        score += 6
        details.append(f"⚡ {int(total_dan_att)} attaques dangereuses")
    elif total_dan_att >= 20:
        score += 3
        details.append(f"⚡ {int(total_dan_att)} attaques dangereuses")

    if total_shots > 0:
        ratio = total_shots_on / total_shots
        if ratio >= 0.5:
            score += 5
            details.append(f"🎯 Bonne précision ({int(ratio*100)}%)")

    minute_in_half = minute % 45
    if 40 <= minute_in_half <= 45 or minute >= 85:
        score += 5
        details.append(f"⏰ Fin de période (min {minute})")

    return min(score, 100), details


async def send_telegram_alert(bot, fixture, score, details):
    league_id = fixture.get("league_id")
    league_name = LEAGUES_TO_WATCH.get(league_id, "Championnat")
    home = get_team_name(fixture, is_home=True)
    away = get_team_name(fixture, is_home=False)
    home_goals = get_score(fixture, is_home=True)
    away_goals = get_score(fixture, is_home=False)
    state = fixture.get("state", {})
    clock = state.get("clock", {})
    minute = clock.get("mm", 0) or 0

    bars = int(score / 10)
    gauge = "🟩" * bars + "⬜" * (10 - bars)

    if score >= 85:
        level = "🔴 ALERTE MAXIMALE"
    elif score >= 75:
        level = "🟠 FORTE PRESSION"
    else:
        level = "🟡 PRESSION EN HAUSSE"

    details_text = "\n".join([f"  • {d}" for d in details])

    message = (
        f"⚽ {level}\n\n"
        f"🏆 {league_name}\n"
        f"🆚 {home} {home_goals} - {away_goals} {away}\n"
        f"⏱️ {minute}'\n\n"
        f"📊 Score Momentum: {score}/100\n"
        f"{gauge}\n\n"
        f"📈 Signaux détectés:\n"
        f"{details_text}\n\n"
        f"#but #momentum"
    )

    try:
        await bot.send_message(
            chat_id=TELEGRAM_CHAT_ID,
            text=message
        )
        logger.info(f"Alerte envoyée: {home} vs {away} (score: {score})")
    except Exception as e:
        logger.error(f"Erreur envoi Telegram: {e}")


async def check_matches():
    logger.info("Vérification des matchs en direct...")
    bot = Bot(token=TELEGRAM_TOKEN)
    fixtures = get_live_fixtures()

    if not fixtures:
        logger.info("Aucun match en direct pour nos ligues")
        return

    logger.info(f"{len(fixtures)} match(s) trouvé(s)")

    for fixture in fixtures:
        fixture_id = fixture.get("id")
        state = fixture.get("state", {})
        clock = state.get("clock", {})
        minute = clock.ge
