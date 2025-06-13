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

from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import sessionmaker, selectinload
from sqlalchemy.future import select

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.constants import ChatType
from telegram.ext import (
    Application,
    CommandHandler,
    ContextTypes,
    ConversationHandler,
    MessageHandler,
    CallbackQueryHandler,
    filters,
)

# --- Import our model and base ---
from database import Base
from models import User, Transaction

# --- Basic Setup ---
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO
)
logger = logging.getLogger(__name__)

# --- Load Environment Variables ---
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
DATABASE_URL = os.getenv("DATABASE_URL")
PUBLIC_URL = os.getenv("PUBLIC_URL")
ADMIN_DEPOSIT_GROUP_ID = os.getenv("ADMIN_DEPOSIT_GROUP_ID")

if not DATABASE_URL or not TELEGRAM_BOT_TOKEN or not PUBLIC_URL:
    raise ValueError("One or more critical environment variables are not set!")


# --- Conversation States ---
ASKING_ID, ASKING_AMOUNT, ASKING_SCREENSHOT = range(3)


# --- Utility Functions ---
def generate_request_id(prefix='DEP-', length=6):
    return prefix + ''.join(random.choices(string.ascii_uppercase + string.digits, k=length))


# --- Main Handlers ---
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    async with context.bot_data["db_session_factory"]() as session:
        result = await session.execute(select(User).filter_by(user_id=user.id))
        if not result.scalar_one_or_none():
            session.add(User(user_id=user.id, telegram_username=user.username))
            await session.commit()
            logger.info(f"New user {user.username} added to database.")

    keyboard = [[InlineKeyboardButton("💰 Deposit", callback_data="deposit_start")]]
    reply_markup = InlineKeyboardMarkup(keyboard)
    await update.message.reply_html(
        rf"Hello {user.mention_html()}! Please choose an option:",
        reply_markup=reply_markup
    )

async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    await update.message.reply_text("Operation cancelled.")
    context.user_data.clear()
    return ConversationHandler.END


# --- Deposit Conversation Handlers ---
async def deposit_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    await query.edit_message_text(text="Please enter your 1xBet User ID.")
    return ASKING_ID

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
    if not update.message.photo:
        await update.message.reply_text("That is not a photo. Please send a screenshot.")
        return ASKING_SCREENSHOT

    photo_file_id = update.message.photo[-1].file_id
    user = update.effective_user
    request_id = generate_request_id()

    async with context.bot_data["db_session_factory"]() as session:
        new_transaction = Transaction(
            user_id=user.id, request_id=request_id, type='DEPOSIT',
            amount=float(context.user_data.get('amount', 0)), status='pending'
        )
        session.add(new_transaction)
        await session.commit()
    
    admin_caption = (
        f"--- <b>New Deposit Request</b> ---\n"
        f"<b>Request ID:</b> <code>{request_id}</code>\n<b>User:</b> {user.mention_html()} ({user.id})\n"
        f"<b>1xBet ID:</b> {context.user_data.get('xbet_id', 'N/A')}\n<b>Amount:</b> {context.user_data.get('amount', 'N/A')} MMK"
    )
    keyboard = [[InlineKeyboardButton("🔒 Lock & Take", callback_data=f"lock_req:{request_id}")]]
    reply_markup = InlineKeyboardMarkup(keyboard)

    if ADMIN_DEPOSIT_GROUP_ID:
        await context.bot.send_photo(
            chat_id=ADMIN_DEPOSIT_GROUP_ID, photo=photo_file_id,
            caption=admin_caption, parse_mode='HTML', reply_markup=reply_markup
        )
    await update.message.reply_text("Thank you! Your request has been submitted.")
    context.user_data.clear()
    return ConversationHandler.END


# --- Admin Action Handlers ---
async def lock_request(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    request_id = query.data.split(":")[1]
    admin = query.from_user
    async with context.bot_data["db_session_factory"]() as session:
        result = await session.execute(select(Transaction).filter_by(request_id=request_id))
        transaction = result.scalar_one_or_none()
        if transaction and transaction.status == 'pending':
            await query.answer("Request locked by you.")
            transaction.status = 'locked'
            transaction.admin_id = admin.id
            await session.commit()
            keyboard = [[
                InlineKeyboardButton("✅ Approve", callback_data=f"approve_req:{request_id}"),
                InlineKeyboardButton("❌ Reject", callback_data=f"reject_req:{request_id}")
            ]]
            reply_markup = InlineKeyboardMarkup(keyboard)
            original_caption = query.message.caption_html
            new_caption = f"{original_caption}\n\n---\n<b>Status:</b> Locked by {admin.mention_html()}"
            await query.edit_message_caption(caption=new_caption, parse_mode='HTML', reply_markup=reply_markup)
        else:
            await query.answer("This request was already handled.", show_alert=True)

async def approve_request(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    request_id = query.data.split(":")[1]
    admin = query.from_user
    async with context.bot_data["db_session_factory"]() as session:
        result = await session.execute(
            select(Transaction).options(selectinload(Transaction.user)).filter_by(request_id=request_id)
        )
        transaction = result.scalar_one_or_none()
        if transaction and transaction.status == 'locked' and transaction.admin_id == admin.id:
            await query.answer("Request Approved.")
            transaction.status = 'approved'
            await session.commit()
            await context.bot.send_message(
                chat_id=transaction.user_id,
                text=f"✅ Your deposit request (ID: {request_id}) for {transaction.amount} MMK has been approved."
            )
            original_caption = re.sub(r'\n\n---\n.*', '', query.message.caption_html, flags=re.DOTALL)
            new_caption = f"{original_caption}\n\n---\n<b>Status:</b> ✅ Approved by {admin.mention_html()}"
            await query.edit_message_caption(caption=new_caption, parse_mode='HTML', reply_markup=None)
        else:
            await query.answer("You cannot approve this request.", show_alert=True)

async def reject_request(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handles the 'Reject' button click with detailed logging."""
    query = update.callback_query
    request_id = query.data.split(":")[1]
    admin = query.from_user
    logger.info(f"Admin {admin.id} initiated rejection for request {request_id}")
    
    async with context.bot_data["db_session_factory"]() as session:
        result = await session.execute(select(Transaction).filter_by(request_id=request_id))
        transaction = result.scalar_one_or_none()

        # Detailed check
        if transaction and transaction.status == 'locked' and transaction.admin_id == admin.id:
            logger.info(f"Request {request_id} is valid for rejection. Processing...")
            await query.answer("Request Rejected.")
            transaction.status = 'rejected'
            await session.commit()

            await context.bot.send_message(
                chat_id=transaction.user_id,
                text=f"❌ Your deposit request (ID: {request_id}) for {transaction.amount} MMK has been rejected. Please contact support."
            )
            
            original_caption = re.sub(r'\n\n---\n.*', '', query.message.caption_html, flags=re.DOTALL)
            new_caption = f"{original_caption}\n\n---\n<b>Status:</b> ❌ Rejected by {admin.mention_html()}"
            await query.edit_message_caption(caption=new_caption, parse_mode='HTML', reply_markup=None)
        else:
            # Log exactly why the check failed
            if not transaction:
                logger.warning(f"Reject failed: Transaction {request_id} not found in DB.")
            elif transaction.status != 'locked':
                logger.warning(f"Reject failed: Transaction {request_id} has status '{transaction.status}', not 'locked'.")
            elif transaction.admin_id != admin.id:
                logger.warning(f"Reject failed: Admin {admin.id} tried to reject, but it was locked by {transaction.admin_id}.")
            
            await query.answer("You cannot reject this request.", show_alert=True)


# --- FastAPI & Application Setup ---
@asynccontextmanager
async def lifespan(app: FastAPI):
    ASYNC_DATABASE_URL = DATABASE_URL.replace("postgresql://", "postgresql+asyncpg://")
    engine = create_async_engine(ASYNC_DATABASE_URL)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    db_session_factory = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    
    ptb_app = Application.builder().token(TELEGRAM_BOT_TOKEN).build()
    ptb_app.bot_data["db_session_factory"] = db_session_factory

    deposit_conv_handler = ConversationHandler(
        entry_points=[CallbackQueryHandler(deposit_start, pattern="^deposit_start$")],
        states={
            ASKING_ID: [MessageHandler(filters.TEXT & ~filters.COMMAND & filters.ChatType.PRIVATE, receive_xbet_id)],
            ASKING_AMOUNT: [MessageHandler(filters.TEXT & ~filters.COMMAND & filters.ChatType.PRIVATE, receive_amount)],
            ASKING_SCREENSHOT: [MessageHandler(filters.PHOTO & filters.ChatType.PRIVATE, receive_screenshot)],
        },
        fallbacks=[CommandHandler("cancel", cancel)],
        per_message=False
    )
    ptb_app.add_handler(CommandHandler("start", start, filters=filters.ChatType.PRIVATE))
    ptb_app.add_handler(deposit_conv_handler)
    ptb_app.add_handler(CallbackQueryHandler(lock_request, pattern=r"^lock_req:"))
    ptb_app.add_handler(CallbackQueryHandler(approve_request, pattern=r"^approve_req:"))
    ptb_app.add_handler(CallbackQueryHandler(reject_request, pattern=r"^reject_req:")) # Use the updated reject handler
    
    app.state.ptb_app = ptb_app
    await ptb_app.initialize()
    webhook_url = f"https://{PUBLIC_URL}/webhook"
    await ptb_app.bot.set_webhook(url=webhook_url, allowed_updates=Update.ALL_TYPES)
    await ptb_app.start()
    yield
    await ptb_app.stop()
    await ptb_app.shutdown()

app = FastAPI(lifespan=lifespan)

# --- Webhook Endpoint ---
@app.post("/webhook")
async def process_telegram_update(request: Request):
    ptb_app = request.app.state.ptb_app
    update = Update.de_json(await request.json(), ptb_app.bot)
    await ptb_app.process_update(update)
    return Response(status_code=200)

@app.get("/")
async def health_check():
    return {"status": "ok", "message": "Full admin workflow active."}