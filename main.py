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
    """Fetch live/in-progress matches from Bzzoiro API."""
    headers = {"Authorization": f"Bearer {BZZOIRO_KEY}"}

    # Try /api/events/?status=inprogress first
    url = f"{BASE_URL}/api/events/?status=inprogress"
    try:
        response = requests.get(url, headers=headers, timeout=10)
        response.raise_for_status()
        data = response.json()
        logger.info(f"[inprogress] Response: {data}")

        # Handle different response structures
        if isinstance(data, list):
            return data
        if isinstance(data, dict):
            return data.get("results") or data.get("data") or data.get("events") or []
    except Exception as e:
        logger.error(f"Error fetching inprogress events: {e}")

    # Fallback: try /api/live/
    url_fallback = f"{BASE_URL}/api/live/"
    try:
        response = requests.get(url_fallback, headers=headers, timeout=10)
        response.raise_for_status()
        data = response.json()
        logger.info(f"[live fallback] Response: {data}")

        if isinstance(data, list):
            return data
        if isinstance(data, dict):
            return data.get("results") or data.get("data") or data.get("events") or []
    except Exception as e:
        logger.error(f"Error fetching live fallback: {e}")

    return []


def format_match(match):
    """Format a single match for display."""
    home = match.get("home_team") or match.get("home") or match.get("localTeam", {})
    away = match.get("away_team") or match.get("away") or match.get("visitorTeam", {})

    # Handle nested team objects
    if isinstance(home, dict):
        home = home.get("name") or home.get("title") or "?"
    if isinstance(away, dict):
        away = away.get("name") or away.get("title") or "?"

    score_home = match.get("score_home") or match.get("home_score") or match.get("goals_home") or "?"
    score_away = match.get("score_away") or match.get("away_score") or match.get("goals_away") or "?"

    minute = match.get("minute") or match.get("time") or match.get("elapsed") or ""
    minute_str = f" [{minute}']" if minute else ""

    league = match.get("league") or match.get("competition") or ""
    if isinstance(league, dict):
        league = league.get("name") or league.get("title") or ""
    league_str = f"🏆 {league}\n" if league else ""

    return f"{league_str}⚽ {home} {score_home} - {score_away} {away}{minute_str}"


async def live(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handler for /live command."""
    await update.message.reply_text("🔍 Recherche des matchs en cours...")

    matches = get_live_matches()

    if not matches:
        await update.message.reply_text(
            "❌ Aucun match en direct pour le moment.\n"
            "Essaie plus tard ou vérifie que BZZOIRO_KEY est bien configurée sur Railway."
        )
        return

    lines = [f"📡 *Matchs en direct ({len(matches)})*\n"]
    for match in matches:
        lines.append(format_match(match))
        lines.append("")

    await update.message.reply_text("\n".join(lines), parse_mode="Markdown")


async def debug(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Debug command to see raw API response."""
    headers = {"Authorization": f"Bearer {BZZOIRO_KEY}"}
    url = f"{BASE_URL}/api/events/?status=inprogress"

    try:
        response = requests.get(url, headers=headers, timeout=10)
        raw = response.text[:1000]  # limit output
        await update.message.reply_text(
            f"URL: {url}\nStatus: {response.status_code}\nRaw:\n{raw}"
        )
    except Exception as e:
        await update.message.reply_text(f"Erreur: {e}")


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "👋 Salut ! Je suis ton bot football.\n\n"
        "Commandes disponibles :\n"
        "/live - Matchs en direct\n"
        "/debug - Debug API brute"
    )


def main():
    if not TELEGRAM_TOKEN:
        raise ValueError("TELEGRAM_TOKEN manquante dans les variables Railway")
    if not BZZOIRO_KEY:
        raise ValueError("BZZOIRO_KEY manquante dans les variables Railway")

    app = ApplicationBuilder().token(TELEGRAM_TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("live", live))
    app.add_handler(CommandHandler("debug", debug))

    logger.info("Bot demarré...")
    app.run_polling()


if __name__ == "__main__":
    main()
