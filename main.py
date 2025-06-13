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
    CallbackQueryHandler,
    filters,
)

# --- Basic Setup ---
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO
)
logger = logging.getLogger(__name__)

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")

# --- Handlers ---
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Sends a welcome message with a test button."""
    keyboard = [[InlineKeyboardButton("Click This Final Test Button", callback_data="final_test")]]
    reply_markup = InlineKeyboardMarkup(keyboard)
    await update.message.reply_html(
        "This is the definitive test. Please click the button below.",
        reply_markup=reply_markup
    )

async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handles the button click and confirms it works."""
    query = update.callback_query
    await query.answer()
    logger.info(f"SUCCESS! Button click received. Data: {query.data}")
    await query.edit_message_text(text=f"✅✅✅ IT WORKS! ✅✅✅\nThe button click was successful.")

# --- FastAPI & Application Setup ---
@asynccontextmanager
async def lifespan(app: FastAPI):
    """Handles bot startup and shutdown."""
    await ptb_app.initialize()
    await ptb_app.start()
    yield
    await ptb_app.stop()
    await ptb_app.shutdown()

app = FastAPI(lifespan=lifespan)
ptb_app = Application.builder().token(TELEGRAM_BOT_TOKEN).build()
ptb_app.add_handler(CommandHandler("start", start))
ptb_app.add_handler(CallbackQueryHandler(button_handler))

# --- THE SIMPLIFIED WEBHOOK ENDPOINT ---
@app.post("/webhook")
async def process_telegram_update(request: Request):
    """Logs and processes updates from Telegram."""
    json_data = await request.json()
    logger.info(f"--- INCOMING DATA ---\n{json.dumps(json_data, indent=2)}\n---")
    update = Update.de_json(json_data, ptb_app.bot)
    await ptb_app.process_update(update)
    return Response(status_code=200)

@app.get("/")
async def health_check():
    """Health check for Railway."""
    return {"status": "ok"}