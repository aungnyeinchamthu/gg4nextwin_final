import os
import logging
import html
import random
import string
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request, Response
import uvicorn

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application,
    CommandHandler,
    ContextTypes,
    ConversationHandler,
    MessageHandler,
    CallbackQueryHandler,
    filters,
)

# --- Basic Setup ---
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO
)
logger = logging.getLogger(__name__)

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
ADMIN_DEPOSIT_GROUP_ID = os.getenv("ADMIN_DEPOSIT_GROUP_ID")

# --- Conversation States ---
ASKING_ID, ASKING_AMOUNT, ASKING_SCREENSHOT = range(3)

# --- Utility Functions ---
def generate_request_id(length=6):
    """Generates a random alphanumeric request ID."""
    return 'DEP-' + ''.join(random.choices(string.ascii_uppercase + string.digits, k=length))

async def report_error(update: Update, context: ContextTypes.DEFAULT_TYPE, error: Exception):
    """A helper function to catch errors and report them to the user."""
    logger.error(f"An error occurred: {error}", exc_info=True)
    escaped_error = html.escape(str(error))
    error_message = f"❌ An error occurred:\n\n<pre>{escaped_error}</pre>"
    
    if update.callback_query:
        await update.callback_query.bot.send_message(
            chat_id=update.effective_chat.id, text=error_message, parse_mode='HTML'
        )
    else:
        await update.message.reply_html(error_message)

# --- Main Handlers ---
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Sends a welcome message with a Deposit button."""
    user = update.effective_user
    logger.info(f"User {user.username} ({user.id}) started the bot.")
    keyboard = [[InlineKeyboardButton("💰 Deposit", callback_data="deposit_start")]]
    reply_markup = InlineKeyboardMarkup(keyboard)
    await update.message.reply_html(
        rf"Hello {user.mention_html()}! Please choose an option:",
        reply_markup=reply_markup
    )

async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Cancels and ends the conversation."""
    await update.message.reply_text("Operation cancelled.")
    context.user_data.clear()
    return ConversationHandler.END

# --- Deposit Conversation Handlers ---
async def deposit_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Starts the deposit conversation."""
    try:
        query = update.callback_query
        await query.answer()
        await query.edit_message_text(text="Please enter your 1xBet User ID.")
        return ASKING_ID
    except Exception as e:
        await report_error(update, context, e)
        return ConversationHandler.END

async def receive_xbet_id(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Receives the 1xBet ID and asks for the amount."""
    context.user_data['xbet_id'] = update.message.text
    await update.message.reply_text("Thank you. Please enter the deposit amount (e.g., 10000).")
    return ASKING_AMOUNT

async def receive_amount(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Receives the amount and asks for the screenshot."""
    context.user_data['amount'] = update.message.text
    bank_details = "Bank: KBZ Bank\nAccount Name: U Aung\nAccount Number: 9988776655"
    await update.message.reply_text(
        f"Please transfer to:\n\n{bank_details}\n\nThen send a screenshot of the receipt."
    )
    return ASKING_SCREENSHOT

async def receive_screenshot(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Receives the screenshot, forwards it to admins, and ends the conversation."""
    try:
        if not update.message.photo:
            await update.message.reply_text("That is not a photo. Please send a screenshot.")
            return ASKING_SCREENSHOT

        photo_file = await update.message.photo[-1].get_file()
        user = update.effective_user
        xbet_id = context.user_data.get('xbet_id', 'N/A')
        amount = context.user_data.get('amount', 'N/A')
        request_id = generate_request_id()

        admin_caption = (
            f"--- <b>New Deposit Request</b> ---\n"
            f"<b>Request ID:</b> <code>{request_id}</code>\n"
            f"<b>User:</b> {user.mention_html()} ({user.id})\n"
            f"<b>1xBet ID:</b> {xbet_id}\n"
            f"<b>Amount:</b> {amount} MMK"
        )
        keyboard = [[InlineKeyboardButton("🔒 Lock & Take", callback_data=f"lock_req:{request_id}")]]
        reply_markup = InlineKeyboardMarkup(keyboard)

        if ADMIN_DEPOSIT_GROUP_ID:
            await context.bot.send_photo(
                chat_id=ADMIN_DEPOSIT_GROUP_ID,
                photo=photo_file.file_id,
                caption=admin_caption,
                parse_mode='HTML',
                reply_markup=reply_markup
            )
            await update.message.reply_text("Thank you! Your request has been submitted.")
        else:
            logger.error("ADMIN_DEPOSIT_GROUP_ID is not set!")
            await update.message.reply_text("Sorry, there is a system error. Please contact support.")

        context.user_data.clear()
        return ConversationHandler.END
    except Exception as e:
        await report_error(update, context, e)
        return ConversationHandler.END


# --- FastAPI & Application Setup ---
@asynccontextmanager
async def lifespan(app: FastAPI):
    """Handles startup and shutdown events."""
    logger.info("Lifespan: Initializing bot...")
    await ptb_app.initialize()
    await ptb_app.start()
    yield
    logger.info("Lifespan: Shutting down bot...")
    await ptb_app.stop()
    await ptb_app.shutdown()

app = FastAPI(lifespan=lifespan)

deposit_conv_handler = ConversationHandler(
    entry_points=[CallbackQueryHandler(deposit_start, pattern="^deposit_start$")],
    states={
        ASKING_ID: [MessageHandler(filters.TEXT & ~filters.COMMAND, receive_xbet_id)],
        ASKING_AMOUNT: [MessageHandler(filters.TEXT & ~filters.COMMAND, receive_amount)],
        ASKING_SCREENSHOT: [MessageHandler(filters.PHOTO, receive_screenshot)],
    },
    fallbacks=[CommandHandler("cancel", cancel)],
    per_message=False
)

ptb_app = Application.builder().token(TELEGRAM_BOT_TOKEN).build()
ptb_app.add_handler(CommandHandler("start", start))
ptb_app.add_handler(deposit_conv_handler)

@app.post(f"/webhook/{TELEGRAM_BOT_TOKEN}")
async def process_telegram_update(request: Request):
    """Processes updates from Telegram."""
    json_data = await request.json()
    update = Update.de_json(json_data, ptb_app.bot)
    await ptb_app.process_update(update)
    return Response(status_code=200)

@app.get("/")
async def health_check():
    """Railway's health check endpoint. Responds with 200 OK."""
    logger.info("Health check endpoint was hit.")
    return {"status": "ok"}