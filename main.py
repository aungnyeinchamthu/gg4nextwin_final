import os
import asyncio
import logging
from contextlib import asynccontextmanager
from fastapi import FastAPI, Request, Response
import uvicorn

from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes

# --- Basic Setup ---
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO
)
logger = logging.getLogger(__name__)

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")


# --- Telegram Command Handlers ---
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Sends a message when the command /start is issued."""
    user = update.effective_user
    await update.message.reply_html(
        rf"Hello {user.mention_html()}! The bot is now correctly initialized."
    )


# --- FastAPI & PTB Application Setup ---
# Build the python-telegram-bot application
ptb_app = Application.builder().token(TELEGRAM_BOT_TOKEN).build()
ptb_app.add_handler(CommandHandler("start", start))


# Define the lifespan manager for FastAPI
@asynccontextmanager
async def lifespan(app: FastAPI):
    """Handles startup and shutdown events for the application."""
    logger.info("Initializing bot...")
    await ptb_app.initialize()  # Initializes the bot application
    await ptb_app.start()       # Starts the background tasks

    yield  # The application runs while the server is up

    logger.info("Stopping bot...")
    await ptb_app.stop()        # Stops the background tasks
    await ptb_app.shutdown()    # Shuts down the bot application


# Create the FastAPI app with the defined lifespan
app = FastAPI(lifespan=lifespan)


@app.post(f"/webhook/{TELEGRAM_BOT_TOKEN}")
async def process_telegram_update(request: Request):
    """Processes one update from Telegram by passing it to the PTB application."""
    json_data = await request.json()
    update = Update.de_json(json_data, ptb_app.bot)
    await ptb_app.process_update(update)
    return Response(status_code=200)


@app.get("/")
def health_check():
    """A simple endpoint to check if the app is running."""
    return {"status": "ok"}