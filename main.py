import os
import logging
import html
import random
import string
import re
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request, Response
import uvicorn
import json

from sqlalchemy.future import select

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

# Import our database and models
from database import AsyncSessionLocal, Base, engine
from models import User, Transaction


# --- Basic Setup ---
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO
)
logger = logging.getLogger(__name__)

# --- Load Environment Variables ---
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
PUBLIC_URL = os.getenv("PUBLIC_URL")
ADMIN_DEPOSIT_GROUP_ID = os.getenv("ADMIN_DEPOSIT_GROUP_ID")

if not TELEGRAM_BOT_TOKEN:
    raise ValueError("CRITICAL ERROR: TELEGRAM_BOT_TOKEN environment variable is not set!")
if not PUBLIC_URL:
    raise ValueError("CRITICAL ERROR: PUBLIC_URL environment variable is not set!")


# --- Conversation States ---
ASKING_ID, ASKING_AMOUNT, ASKING_SCREENSHOT = range(3)


# --- Utility Functions ---
def generate_request_id(prefix='DEP-', length=6):
    """Generates a random alphanumeric request ID."""
    return prefix + ''.join(random.choices(string.ascii_uppercase + string.digits, k=length))

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
    
    # Add user to DB if not exists
    async with AsyncSessionLocal() as session:
        result = await session.execute(select(User).filter_by(user_id=user.id))
        db_user = result.scalar_one_or_none()
        if not db_user:
            new_user = User(user_id=user.id, telegram_username=user.username)
            session.add(new_user)
            await session.commit()
            logger.info(f"New user {user.username} added to the database.")

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
    context.user_data['xbet_id'] = update.message.text
    await update.message.reply_text("Thank you. Please enter the deposit amount.")
    return ASKING_AMOUNT

async def receive_amount(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    context.user_data['amount'] = update.message.text
    bank_details = "Bank: KBZ Bank\nAccount Name: U Aung\nAccount Number: 9988776655"
    await update.message.reply_text(
        f"Please transfer to:\n\n{bank_details}\n\nThen send a screenshot of the receipt."
    )
    return ASKING_SCREENSHOT

async def receive_screenshot(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Receives the screenshot, saves to DB, forwards to admins, and ends the conversation."""
    try:
        if not update.message.photo:
            await update.message.reply_text("That is not a photo. Please send a screenshot.")
            return ASKING_SCREENSHOT

        photo_file_id = update.message.photo[-1].file_id
        user = update.effective_user
        xbet_id = context.user_data.get('xbet_id', 'N/A')
        amount = context.user_data.get('amount', 'N/A')
        request_id = generate_request_id()

        # Save the transaction to the database
        async with AsyncSessionLocal() as session:
            new_transaction = Transaction(
                user_id=user.id,
                request_id=request_id,
                type='DEPOSIT',
                amount=float(amount), # Convert amount to a number
                status='pending'
            )
            session.add(new_transaction)
            await session.commit()
            logger.info(f"Saved new transaction {request_id} to database.")

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
                chat_id=ADMIN_DEPOSIT_GROUP_ID, photo=photo_file_id,
                caption=admin_caption, parse_mode='HTML', reply_markup=reply_markup
            )
            await update.message.reply_text("Thank you! Your request has been submitted.")
        else:
            logger.error("ADMIN_DEPOSIT_GROUP_ID is not set!")
            await update.message.reply_text("Sorry, there is a system error.")

        context.user_data.clear()
        return ConversationHandler.END
    except Exception as e:
        await report_error(update, context, e)
        return ConversationHandler.END


# --- NEW: Admin Action Handlers ---
async def lock_request(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handles the 'Lock & Take' button click by an admin."""
    query = update.callback_query
    await query.answer("Request locked by you.")

    request_id = query.data.split(":")[1]
    admin = query.from_user

    async with AsyncSessionLocal() as session:
        result = await session.execute(select(Transaction).filter_by(request_id=request_id))
        transaction = result.scalar_one_or_none()

        if transaction and transaction.status == 'pending':
            transaction.status = 'locked'
            transaction.admin_id = admin.id
            await session.commit()
            logger.info(f"Transaction {request_id} locked by admin {admin.username} ({admin.id}).")

            # Create new buttons for Approve/Reject
            keyboard = [
                [
                    InlineKeyboardButton("✅ Approve", callback_data=f"approve_req:{request_id}"),
                    InlineKeyboardButton("❌ Reject", callback_data=f"reject_req:{request_id}")
                ]
            ]
            reply_markup = InlineKeyboardMarkup(keyboard)

            # Edit the original message
            original_caption = query.message.caption_html
            new_caption = f"{original_caption}\n\n---\n<b>Status:</b> Locked by {admin.mention_html()}"
            
            await query.edit_message_caption(caption=new_caption, parse_mode='HTML', reply_markup=reply_markup)
        
        elif transaction:
            await query.answer(f"This request was already handled (Status: {transaction.status}).", show_alert=True)
        else:
            await query.answer("Error: Transaction not found.", show_alert=True)


# --- FastAPI & Application Setup ---
@asynccontextmanager
async def lifespan(app: FastAPI):
    """Handles bot startup and shutdown."""
    async with engine.begin() as conn:
        # This will create the tables if they don't exist
        await conn.run_sync(Base.metadata.create_all)

    webhook_url = f"https://{PUBLIC_URL}/webhook"
    await ptb_app.bot.set_webhook(url=webhook_url, allowed_updates=Update.ALL_TYPES)
    await ptb_app.start()
    yield
    await ptb_app.stop()

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
# NEW: Add the handler for the admin lock button
ptb_app.add_handler(CallbackQueryHandler(lock_request, pattern="^lock_req:"))


# --- Webhook Endpoint ---
@app.post("/webhook")
async def process_telegram_update(request: Request):
    """Processes updates from Telegram."""
    update = Update.de_json(await request.json(), ptb_app.bot)
    await ptb_app.process_update(update)
    return Response(status_code=200)

@app.get("/")
async def health_check():
    """Health check for Railway."""
    return {"status": "ok", "message": "Deposit feature active."}