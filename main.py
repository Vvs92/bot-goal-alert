import requests
import time
import schedule
import logging
import os
import asyncio
from telegram import Bot

# ============================================
# 🔧 CONFIGURATION
# ============================================
SPORTMONKS_TOKEN = os.environ.get("SPORTMONKS_TOKEN", "METS_TON_TOKEN_ICI")
TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN", "METS_TON_TOKEN_BOT_ICI")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "METS_TON_CHAT_ID_ICI")

# ============================================
# 🏆 LIGUES À SURVEILLER (IDs SportMonks)
# ============================================
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

# ============================================
# ⚙️ PARAMÈTRES
# ============================================
ALERT_THRESHOLD = 65
CHECK_INTERVAL = 90

# ============================================
logging.basicConfig(
    format='%(asctime)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

BASE_URL = "https://api.sportmonks.com/v3/football"
HEADERS = {"Authorization": f"Bearer {SPORTMONKS_TOKEN}"}

sent_alerts = {}


def get_live_fixtures():
    """Récupère tous les matchs en direct"""
    try:
        response = requests.get(
            f"{BASE_URL}/livescores/inplay",
            headers=HEADERS,
            params={
                "include": "participants;scores;state;statistics;attackingMomentum",
                "per_page": 50
            },
            timeout=15
        )
        data = response.json()
        fixtures = data.get("data", [])

        # Filtre sur nos ligues
        filtered = [f for f in fixtures if f.get("league_id") in LEAGUES_TO_WATCH]
        return filtered

    except Exception as e:
        logger.error(f"Erreur get_live_fixtures: {e}")
        return []


def get_team_name(fixture, is_home=True):
    """Extrait le nom d'une équipe"""
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
    """Extrait le score actuel"""
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
    """Extrait une statistique spécifique"""
    try:
        for stat in statistics:
            if stat.get("type", {}).get("code") == stat_type:
                if team_id is None or stat.get("participant_id") == team_id:
                    return float(stat.get("data", {}).get("value", 0) or 0)
        return 0
    except:
        return 0


def calculate_momentum_score(fixture):
    """
    🧠 ALGORITHME MOMENTUM SPORTMONKS
    Calcule un score de 0 à 100
    """
    score = 0
    details = []

    # ── Minute du match ───────────────────────
    state = fixture.get("state", {})
    minute = state.get("clock", {}).get("mm", 0) or 0

    if minute < 10:
        return 0, []

    statistics = fixture.get("statistics", [])
    momentum_data = fixture.get("attackingMomentum", {})

    # ── Participants ──────────────────────────
    participants = fixture.get("participants", [])
    home_id, away_id = None, None
    for p in participants:
        meta = p.get("meta", {})
        if meta.get("location") == "home":
            home_id = p.get("id")
        else:
            away_id = p.get("id")

    # ── Stats collectées ──────────────────────
    shots_on_h = get_stat_value(statistics, "shots-on-target", home_id)
    shots_on_a = get_stat_value(statistics, "shots-on-target", away_id)
    shots_total_h = get_stat_value(statistics, "shots-total", home_id)
    shots_total_a = get_stat_value(statistics, "shots-total", away_id)
    dangerous_h = get_stat_value(statistics, "shots-insidebox", home_id)
    dangerous_a = get_stat_value(statistics, "shots-insidebox", away_id)
    corners_h = get_stat_value(statistics, "corners", home_id)
    corners_a = get_stat_value(statistics, "corners", away_id)
    dangerous_att_h = get_stat_value(
