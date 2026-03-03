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

LIVE_STATUSES = {"inprogress", "live", "1H", "2H", "HT", "ET", "PEN", "LIVE", "IN_PLAY"}


def get_all_events():
    all_results = []
    url = f"{BASE_URL}/api/events/"
    while url:
        try:
            r = requests.get(url, headers=HEADERS, timeout=10)
            data = r.json()
            all_results.extend(data.get("results", []))
            url = data.get("next")
        except Exception as e:
            logger.error(f"Error: {e}")
            break
    return all_results


def get_live_matches():
    all_events = get_all_events()
    live = []
    for match in all_events:
        status = str(match.get("status") or match.get("state") or match.get("match_status") or "").lower()
        if any(s.lower() in status for s in LIVE_STATUSES) or status not in {"", "scheduled", "ns", "finished", "ft", "postponed", "cancelled"}:
            live.append(match)
    return live


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
    status = match.get("status") or match.get("state") or ""
    return f"{league_str}⚽ {home} {score_home} - {score_away} {away}{minute_str} ({status})"


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
    # Show all unique statuses found in events
    all_events = get_all_events()
    statuses = {}
    for m in all_events:
        s = str(m.get("status") or m.get("state") or m.get("match_status") or "unknown")
        statuses[s] = statuses.get(s, 0) + 1

    lines = [f"Total: {len(all_events)} matchs\n", "Statuts trouvés:"]
    for s, count in sorted(statuses.items(), key=lambda x: -x[1]):
        lines.append(f"  {s}: {count}")

    await update.message.reply_text("\n".join(lines))


async def debug2(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # Show raw structure of first event
    r = requests.get(f"{BASE_URL}/api/events/", headers=HEADERS, timeout=10)
    data = r.json()
    results = data.get("results", [])
    if results:
        await update.message.reply_text(str(results[0])[:800])
    else:
        await update.message.reply_text("Aucun event trouve.")


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "👋 Bot Football\n\n"
        "/live - Matchs en direct\n"
        "/debug - Statuts disponibles\n"
        "/debug2 - Structure d un match"
    )


def main():
    app = ApplicationBuilder().token(TELEGRAM_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("live", live))
    app.add_handler(CommandHandler("debug", debug))
    app.add_handler(CommandHandler("debug2", debug2))
    logger.info("Bot demarre...")
    app.run_polling()


if __name__ == "__main__":
    main()
