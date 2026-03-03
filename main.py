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


def get_live_matches():
    for status in ["inprogress", "live", "1H", "2H", "HT"]:
        try:
            url = f"{BASE_URL}/api/events/?status={status}"
            r = requests.get(url, headers=HEADERS, timeout=10)
            data = r.json()
            results = data.get("results", [])
            if results:
                logger.info(f"Found {len(results)} matches with status={status}")
                return results
        except Exception as e:
            logger.error(f"Error status={status}: {e}")
    return []


def format_match(match):
    home = match.get("home_team") or match.get("home") or {}
    away = match.get("away_team") or match.get("away") or {}
    if isinstance(home, dict):
        home = home.get("name") or "?"
    if isinstance(away, dict):
        away = away.get("name") or "?"
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
    await update.message.reply_text("🔍 Recherche des matchs en cours...")
    matches = get_live_matches()
    if not matches:
        await update.message.reply_text("❌ Aucun match en direct pour le moment.")
        return
    lines = [f"📡 Matchs en direct ({len(matches)})\n"]
    for match in matches:
        lines.append(format_match(match))
        lines.append("")
    await update.message.reply_text("\n".join(lines))


async def debug(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # Test different statuses to see what exists
    statuses = ["inprogress", "live", "finished", "scheduled", "1H", "2H", "HT", "NS"]
    lines = []
    for status in statuses:
        try:
            url = f"{BASE_URL}/api/events/?status={status}"
            r = requests.get(url, headers=HEADERS, timeout=10)
            data = r.json()
            count = data.get("count", "?")
            lines.append(f"{status}: {count} matchs")
        except Exception as e:
            lines.append(f"{status}: erreur")
    await update.message.reply_text("Résultats par statut:\n\n" + "\n".join(lines))


async def debug2(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # Show raw structure of first available match
    statuses = ["finished", "scheduled", "NS"]
    for status in statuses:
        try:
            url = f"{BASE_URL}/api/events/?status={status}&limit=1"
            r = requests.get(url, headers=HEADERS, timeout=10)
            data = r.json()
            results = data.get("results", [])
            if results:
                await update.message.reply_text(f"Exemple ({status}):\n{str(results[0])[:500]}")
                return
        except Exception as e:
            pass
    await update.message.reply_text("Aucun match trouve dans aucun statut.")


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "👋 Bot Football\n\n"
        "/live - Matchs en direct\n"
        "/debug - Compter les matchs par statut\n"
        "/debug2 - Voir la structure d un match"
    )


def main():
    if not TELEGRAM_TOKEN:
        raise ValueError("TELEGRAM_TOKEN manquante")
    if not BZZOIRO_KEY:
        raise ValueError("BZZOIRO_KEY manquante")

    app = ApplicationBuilder().token(TELEGRAM_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("live", live))
    app.add_handler(CommandHandler("debug", debug))
    app.add_handler(CommandHandler("debug2", debug2))

    logger.info("Bot demarre...")
    app.run_polling()


if __name__ == "__main__":
    main()
