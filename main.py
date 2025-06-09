import os
import asyncio
import logging
from fastapi import FastAPI, Request, Response
import uvicorn
from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes

# --- Basic Setup ---
# Enable logging to see errors
logging.basicConfig(format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO)
logger = logging.getLogger(__name__)

# Load the bot token from Railway's environment variables
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")

# --- Telegram Command Handlers ---
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Sends a message when the command /start is issued."""
    user = update.effective_user
    await update.message.reply_html(
        rf"Hello {user.mention_html()}! This is the start of our great bot."
    )

# --- FastAPI Webhook Setup ---
# This part creates a web server to receive messages from Telegram
app = FastAPI()
ptb_app = Application.builder().token(TELEGRAM_BOT_TOKEN).build()
ptb_app.add_handler(CommandHandler("start", start))

@app.post(f"/webhook/{TELEGRAM_BOT_TOKEN}")
async def process_telegram_update(request: Request):
    """Processes one update from Telegram."""
    json_data = await request.json()
    update = Update.de_json(json_data, ptb_app.bot)
    await ptb_app.process_update(update)
    return Response(status_code=200)

@app.get("/")
def health_check():
    """A simple endpoint to check if the app is running."""
    return {"status": "ok"}

# The main entry point for the Uvicorn server in the Procfile
# This part is not directly called but ensures the 'app' object is defined.
if __name__ == "__main__":
    # This block is for local testing, which we are not doing.
    # Uvicorn will run the 'app' object directly based on the Procfile.
    pass