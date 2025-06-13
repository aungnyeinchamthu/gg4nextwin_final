import os
import logging
import html
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request, Response
import uvicorn
import json

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application,
    CommandHandler,
    ContextTypes,
)

# --- Basic Setup & CRITICAL VALIDATION ---
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO
)
logger = logging.getLogger(__name__)

# Load Environment Variables
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
PUBLIC_URL = os.getenv("PUBLIC_URL")

# Add a check to ensure variables are not missing. This prevents silent crashes.
if not TELEGRAM_BOT_TOKEN:
    raise ValueError("CRITICAL ERROR: TELEGRAM_BOT_TOKEN environment variable is not set!")
if not PUBLIC_URL:
    raise ValueError("CRITICAL ERROR: PUBLIC_URL environment variable is not set!")


# --- A Simple Start Handler ---
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Sends a simple welcome message."""
    await update.message.reply_html("Success! The bot is stable and responding correctly.")


# --- FastAPI & Application Setup ---
@asynccontextmanager
async def lifespan(app: FastAPI):
    """Handles bot startup and shutdown."""
    webhook_url = f"https://{PUBLIC_URL}/webhook"
    
    logger.info("Lifespan: Initializing bot...")
    await ptb_app.initialize()
    
    logger.info(f"Lifespan: Setting webhook to {webhook_url}")
    await ptb_app.bot.set_webhook(
        url=webhook_url,
        allowed_updates=Update.ALL_TYPES
    )
    
    await ptb_app.start()
    yield
    logger.info("Lifespan: Shutting down bot...")
    await ptb_app.stop()
    await ptb_app.shutdown()

app = FastAPI(lifespan=lifespan)

# Build the PTB application and add the simple handler
ptb_app = Application.builder().token(TELEGRAM_BOT_TOKEN).build()
ptb_app.add_handler(CommandHandler("start", start))


# --- Webhook Endpoint ---
@app.post("/webhook")
async def process_telegram_update(request: Request):
    """Processes updates from Telegram."""
    try:
        json_data = await request.json()
        update = Update.de_json(json_data, ptb_app.bot)
        await ptb_app.process_update(update)
    except Exception as e:
        logger.error("Error processing update:", exc_info=True)
        
    return Response(status_code=200)

@app.get("/")
async def health_check():
    """Health check for Railway."""
    return {"status": "ok", "message": "Basic bot is running."}