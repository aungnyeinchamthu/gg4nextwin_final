import os
import asyncio
import logging
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
# Define constants for the different steps of the conversation
ASKING_ID, ASKING_AMOUNT, ASKING_SCREENSHOT = range(3)


# --- Telegram Command Handlers ---
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Sends a message when the command /start is issued."""
    user = update.effective_user
    logger.info(f"User {user.username} ({user.id}) started the bot.")
    
    keyboard = [
        [InlineKeyboardButton("💰 Deposit", callback_data="deposit_start")],
        [InlineKeyboardButton("💸 Withdraw", callback_data="withdraw_start")], # Placeholder
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)

    await update.message.reply_html(
        rf"Hello {user.mention_html()}! Welcome. Please choose an option:",
        reply_markup=reply_markup
    )

async def deposit_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Starts the deposit conversation."""
    query = update.callback_query
    await query.answer() # Answer the callback query to remove the "loading" state
    
    await query.edit_message_text(text="You have started the deposit process.\n\nPlease enter your 1xBet User ID.")
    return ASKING_ID

async def receive_xbet_id(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Receives the 1xBet ID and asks for the amount."""
    context.user_data['xbet_id'] = update.message.text
    await update.message.reply_text("Thank you. Now, please enter the amount you wish to deposit (e.g., 10000).")
    return ASKING_AMOUNT

async def receive_amount(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Receives the amount and asks for the screenshot."""
    context.user_data['amount'] = update.message.text
    # In a real scenario, you would fetch bank details from the database here.
    bank_details = "Bank: KBZ Bank\nAccount Name: U Aung\nAccount Number: 9988776655"
    await update.message.reply_text(
        f"Please transfer the exact amount to the following account:\n\n{bank_details}\n\n"
        "After transferring, please send a screenshot of the receipt."
    )
    return ASKING_SCREENSHOT

def generate_request_id(length=6):
    """Generates a random alphanumeric request ID."""
    return 'DEP-' + ''.join(random.choices(string.ascii_uppercase + string.digits, k=length))

async def receive_screenshot(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Receives the screenshot, forwards it to admins, and ends the conversation."""
    if not update.message.photo:
        await update.message.reply_text("That doesn't look like a photo. Please send a screenshot.")
        return ASKING_SCREENSHOT

    photo_file = await update.message.photo[-1].get_file()
    
    # Gather all data
    user = update.effective_user
    xbet_id = context.user_data.get('xbet_id')
    amount = context.user_data.get('amount')
    request_id = generate_request_id()

    # Format the message for admins
    admin_caption = (
        f"--- <b>New Deposit Request</b> ---\n"
        f"<b>Request ID:</b> <code>{request_id}</code>\n"
        f"<b>User:</b> {user.mention_html()} ({user.id})\n"
        f"<b>1xBet ID:</b> {xbet_id}\n"
        f"<b>Amount:</b> {amount} MMK"
    )

    # Create the "Lock & Take" button
    keyboard = [[InlineKeyboardButton("🔒 Lock & Take", callback_data=f"lock_req:{request_id}")]]
    reply_markup = InlineKeyboardMarkup(keyboard)

    # Send to admin group
    if ADMIN_DEPOSIT_GROUP_ID:
        await context.bot.send_photo(
            chat_id=ADMIN_DEPOSIT_GROUP_ID,
            photo=photo_file.file_id,
            caption=admin_caption,
            parse_mode='HTML',
            reply_markup=reply_markup
        )
        await update.message.reply_text("Thank you! Your deposit request has been submitted and is being reviewed. We will notify you shortly.")
    else:
        logger.error("ADMIN_DEPOSIT_GROUP_ID is not set!")
        await update.message.reply_text("Sorry, there was a system error. Please contact support.")

    # End the conversation
    context.user_data.clear()
    return ConversationHandler.END

async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Cancels and ends the conversation."""
    await update.message.reply_text("Operation cancelled.")
    context.user_data.clear()
    return ConversationHandler.END


# --- FastAPI & PTB Application Setup ---
@asynccontextmanager
async def lifespan(app: FastAPI):
    """Handles startup and shutdown events."""
    logger.info("Server startup: Initializing bot...")
    # Here you could also create DB tables if they don't exist:
    # from database import engine, Base
    # async with engine.begin() as conn:
    #     await conn.run_sync(Base.metadata.create_all)
    await ptb_app.initialize()
    await ptb_app.start()
    yield
    logger.info("Server shutdown: Stopping bot...")
    await ptb_app.stop()
    await ptb_app.shutdown()

app = FastAPI(lifespan=lifespan)

# Define the ConversationHandler for the deposit flow
deposit_conv_handler = ConversationHandler(
    entry_points=[CallbackQueryHandler(deposit_start, pattern="^deposit_start$")],
    states={
        ASKING_ID: [MessageHandler(filters.TEXT & ~filters.COMMAND, receive_xbet_id)],
        ASKING_AMOUNT: [MessageHandler(filters.TEXT & ~filters.COMMAND, receive_amount)],
        ASKING_SCREENSHOT: [MessageHandler(filters.PHOTO, receive_screenshot)],
    },
    fallbacks=[CommandHandler("cancel", cancel)],
)

# Build the PTB application and add handlers
ptb_app = Application.builder().token(TELEGRAM_BOT_TOKEN).build()
ptb_app.add_handler(CommandHandler("start", start))
ptb_app.add_handler(deposit_conv_handler)
# We will add a handler for the "Lock" button later

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