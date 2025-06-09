import os
import asyncio
import logging
import html
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request, Response
import uvicorn

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


# --- Telegram Command Handlers ---
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Sends a message when the command /start is issued."""
    user = update.effective_user
    logger.info(f"User {user.username} ({user.id}) started the bot.")
    
    keyboard = [
        [InlineKeyboardButton("💰 Deposit (Self-Debugging Test)", callback_data="deposit_start_test")],
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)

    await update.message.reply_html(
        rf"Hello {user.mention_html()}! This is a new test. Please click the button:",
        reply_markup=reply_markup
    )


# --- NEW SELF-DEBUGGING HANDLER ---
async def button_test(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    This special version will catch any error and report it back to the chat.
    """
    query = update.callback_query
    await query.answer()
    logger.info(f"Attempting to process button click with data: {query.data}")
    
    try:
        # The action we want to test
        await query.bot.send_message(
            chat_id=update.effective_chat.id,
            text=f"✅ SUCCESS! Button click registered!\nData: '{query.data}'"
        )
        logger.info("Successfully sent button click confirmation.")

    except Exception as e:
        # If ANY error happens, catch it and report it directly in the chat
        logger.error(f"An error occurred in button_test: {e}", exc_info=True)
        
        # Escape the error message to make it safe for HTML parsing
        escaped_error = html.escape(str(e))
        
        error_message = (
            f"❌ An error occurred while processing the button click:\n\n"
            f"<b>Error Details:</b>\n<pre>{escaped_error}</pre>"
        )
        await query.bot.send_message(
            chat_id=update.effective_chat.id,
            text=error_message,
            parse_mode='HTML'
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