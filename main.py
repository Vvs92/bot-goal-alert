import os
import logging
import requests
from telegram import Update
from telegram.ext import ApplicationBuilder, CommandHandler, ContextTypes

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO
)
logger = logging.getLogger(__name__)

TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN")
BZZOIRO_KEY = os.environ.get("BZZOIRO_KEY")
BASE_URL = "https://sports.bzzoiro.com"


def get_live_matches():
    headers = {"Authorization": f"Token {BZZOIRO_KEY}"}
    url = f"{BASE_URL}/api/events/?status=inprogress"
    try:
        response = requests.get(url, headers=headers, timeout=10)
        response.raise_for_status()
        data = response.json()
        if isinstance(data, list):
            return data
        if isinstance(data, dict):
            return data.get("results") or data.get("data") or data.get("events") or []
    except Exception as e:
        logger.error(f"Error: {e}")
    return []


def format_match(match):
    home = match.get("home_team") or match.get("home") or match.get("localTeam", {})
    away = match.get("away_team") or match.get("away") or match.get("visitorTeam", {})
    if isinstance(home, dict):
        home = home.get("name") or home.get("title") or "?"
    if isinstance(away, dict):
        away = away.get("name") or away.get("title") or "?"
    score_home = match.get("score_home") or match.get("home_score") or "?"
    score_away = match.get("score_away") or match.get("away_score") or "?"
    minute = match.get("minute") or match.get("time") or ""
    minute_str = f" [{minute}']" if minute else ""
    league = match.get("league") or match.get("competition") or ""
    if isinstance(league, dict):
        league = league.get("name") or ""
    league_str = f"🏆 {league}\n" if league else ""
    return f"{league_str}⚽ {home} {score_home} - {score_away} {away}{minute_str}"


async def live(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Recherche des matchs en cours...")
    matches = get_live_matches()
    if not matches:
        await update.message.reply_text("Aucun match en direct pour le moment.")
        return
    lines = [f"Matchs en direct ({len(matches)})\n"]
    for match in matches:
        lines.append(format_match(match))
        lines.append("")
    await update.message.reply_text("\n".join(lines))


async def debug(update: Update, context: ContextTypes.DEFAULT_TYPE):
    url = f"{BASE_URL}/api/events/?status=inprogress"
    results = []

    auth_formats = [
        ("Token", {"Authorization": f"Token {BZZOIRO_KEY}"}),
        ("Bearer", {"Authorization": f"Bearer {BZZOIRO_KEY}"}),
        ("Api-Key", {"Authorization": f"Api-Key {BZZOIRO_KEY}"}),
        ("URL param", {}),
    ]

    for name, headers in auth_formats:
        try:
            if name == "URL param":
                r = requests.get(f"{url}&api_key={BZZOIRO_KEY}", timeout=10)
            else:
                r = requests.get(url, headers=headers, timeout=10)
            results.append(f"{name}: {r.status_code}\n{r.text[:100]}")
        except Exception as e:
            results.append(f"{name}: ERROR {e}")

    await update.message.reply_text("\n\n".join(results))


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Bot football\n\n/live - Matchs en direct\n/debug - Debug API")


def main():
    if not TELEGRAM_TOKEN:
        raise ValueError("TELEGRAM_TOKEN manquante")
    if not BZZOIRO_KEY:
        raise ValueError("BZZOIRO_KEY manquante")

    app = ApplicationBuilder().token(TELEGRAM_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("live", live))
    app.add_handler(CommandHandler("debug", debug))

    logger.info("Bot demarre...")
    app.run_polling()


if __name__ == "__main__":
    main()
