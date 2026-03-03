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
HEADERS = {"Authorization": f"Token {BZZOIRO_KEY}"}


async def debug(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # Test all possible endpoints
    endpoints = [
        "/api/",
        "/api/events/",
        "/api/live/",
        "/api/matches/",
        "/api/fixtures/",
        "/api/games/",
        "/api/football/",
        "/api/soccer/",
    ]
    lines = []
    for ep in endpoints:
        try:
            r = requests.get(f"{BASE_URL}{ep}", headers=HEADERS, timeout=10)
            lines.append(f"{ep} -> {r.status_code}: {r.text[:80]}")
        except Exception as e:
            lines.append(f"{ep} -> ERROR")

    await update.message.reply_text("\n\n".join(lines))


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("/debug - Explorer l API")


def main():
    app = ApplicationBuilder().token(TELEGRAM_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("debug", debug))
    logger.info("Bot demarre...")
    app.run_polling()


if __name__ == "__main__":
    main()
