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

print("=== DEMARRAGE BOT ===", flush=True)
print(f"SPORTMONKS_TOKEN present: {bool(SPORTMONKS_TOKEN)}", flush=True)
print(f"TELEGRAM_TOKEN present: {bool(TELEGRAM_TOKEN)}", flush=True)
print(f"TELEGRAM_CHAT_ID present: {bool(TELEGRAM_CHAT_ID)}", flush=True)

LEAGUES_TO_WATCH = {
    2:    "Champions League",
    5:    "Europa League",
    8:    "Premier League",
    564:  "La Liga",
    384:  "Serie A",
    82:   "Bundesliga",
    301:  "Ligue 1",
    9:   "Championship",
    600:   "Super Lig",
    208:  "Belgian Pro League",
}

ALERT_THRESHOLD = 65
CHECK_INTERVAL = 90

logging.basicConfig(
    format='%(asctime)s - %(levelname)s - %(message)s',
    level=logging.INFO,
    stream=sys.stdout
)
logger = logging.getLogger(__name__)

BASE_URL = "https://api.sportmonks.com/v3/football"
HEADERS = {"Authorization": f"Bearer {SPORTMONKS_TOKEN}"}

sent_alerts = {}


def get_live_fixtur
