import os
import asyncio
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request, Response
import uvicorn

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application,
    CommandHandler,
    ContextTypes,
    CallbackQueryHandler, # Ensure this is here
    filters,
)

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
    logger.info(f"User {user.username} ({user.id}) started the bot.")
    
    keyboard = [
        [InlineKeyboardButton("💰 Deposit (Test)", callback_data="deposit_start_test")],
        [InlineKeyboardButton("💸 Withdraw", callback_data="withdraw_start")],
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)

    await update.message.reply_html(
        rf"Hello {user.mention_html()}! This is a test. Please click a button:",
        reply_markup=reply_markup
    )


# --- NEW DEBUGGING HANDLER ---
async def button_test(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    A simple handler to see if any button click is registered.
    This replaces the complex conversation handler for now.
    """
    query = update.callback_query
    await query.answer()  # Acknowledge the click, stops the loading icon

    logger.info(f"Button click received! Data: {query.data}")

    # Send a NEW message to confirm we received the click
    await query.bot.send_message(
        chat_id=update.effective_chat.id,
        text=f"Button click registered!\nData received: '{query.data}'"
    )


# --- FastAPI & PTB Application Setup ---
@asynccontextmanager
async def lifespan(app: FastAPI):
    """Handles startup and shutdown events."""
    logger.info("Server startup: Initializing bot...")
    await ptb_app.initialize()
    await ptb_app.start()
    yield
    logger.info("Server shutdown: Stopping bot...")
    await ptb_app.stop()
    await ptb_app.shutdown()

app = FastAPI(lifespan=lifespan)

# Build the PTB application and add handlers
ptb_app = Application.builder().token(TELEGRAM_BOT_TOKEN).build()
ptb_app.add_handler(CommandHandler("start", start))

# ADDING THE NEW, SIMPLE HANDLER. The ConversationHandler is removed for this test.
ptb_app.add_handler(CallbackQueryHandler(button_test))


# --- Webhook Endpoint ---
@app.post(f"/webhook/{TELEGRAM_BOT_TOKEN}")
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
def health_check():
    """Confirms the server is running."""
    return {"status": "ok"}