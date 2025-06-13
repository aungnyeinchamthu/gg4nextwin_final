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

# --- Basic Setup & Environment Variables ---
logging.basicConfig(format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO)
logger = logging.getLogger(__name__)

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
DATABASE_URL = os.getenv("DATABASE_URL")
PUBLIC_URL = os.getenv("PUBLIC_URL")
ADMIN_DEPOSIT_GROUP_ID = os.getenv("ADMIN_DEPOSIT_GROUP_ID")

if not DATABASE_URL or not TELEGRAM_BOT_TOKEN or not PUBLIC_URL:
    raise ValueError("One or more critical environment variables are not set!")

# --- Conversation States ---
ASKING_ID, ASKING_AMOUNT, ASKING_SCREENSHOT = range(3)


# --- Utility & Helper Functions ---
def generate_request_id(prefix='DEP-', length=6):
    return prefix + ''.join(random.choices(string.ascii_uppercase + string.digits, k=length))

async def finalize_submission(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """This new function finalizes both new and updated submissions."""
    user = update.effective_user
    photo_id = context.user_data.get('photo_id')
    xbet_id = context.user_data.get('xbet_id')
    amount = context.user_data.get('amount')

    async with context.bot_data["db_session_factory"]() as session:
        if context.user_data.get('mode') == 'update':
            request_id = context.user_data.get('request_id')
            result = await session.execute(select(Transaction).filter_by(request_id=request_id))
            transaction = result.scalar_one_or_none()
            if transaction:
                transaction.status = 'pending'
                if xbet_id: transaction.xbet_id_from_user = xbet_id
                if amount: transaction.amount = float(amount)
                if photo_id: transaction.photo_file_id = photo_id
                logger.info(f"Updating transaction {request_id}")
        else: # New submission
            request_id = generate_request_id()
            transaction = Transaction(
                user_id=user.id, request_id=request_id, type='DEPOSIT',
                amount=float(amount if amount else 0), status='pending',
                xbet_id_from_user=xbet_id, photo_file_id=photo_id
            )
            session.add(transaction)
            logger.info(f"Saving new transaction {request_id}")
        await session.commit()

    admin_caption = (
        f"--- <b>New/Updated Deposit Request</b> ---\n"
        f"<b>Request ID:</b> <code>{request_id}</code>\n<b>User:</b> {user.mention_html()} ({user.id})\n"
        f"<b>1xBet ID:</b> {transaction.xbet_id_from_user}\n<b>Amount:</b> {transaction.amount} MMK"
    )
    keyboard = [[InlineKeyboardButton("🔒 Lock & Take", callback_data=f"lock_req:{request_id}")]]
    reply_markup = InlineKeyboardMarkup(keyboard)

    if ADMIN_DEPOSIT_GROUP_ID:
        admin_message = await context.bot.send_photo(
            chat_id=ADMIN_DEPOSIT_GROUP_ID, photo=photo_id,
            caption=admin_caption, parse_mode='HTML', reply_markup=reply_markup
        )
        async with context.bot_data["db_session_factory"]() as session:
            result = await session.execute(select(Transaction).filter_by(request_id=request_id))
            tx_to_update = result.scalar_one_or_none()
            if tx_to_update:
                tx_to_update.admin_chat_id = admin_message.chat_id
                tx_to_update.admin_message_id = admin_message.message_id
                await session.commit()

    await context.bot.send_message(chat_id=user.id, text="Thank you! Your request has been submitted and is being reviewed.")
    context.user_data.clear()
    return ConversationHandler.END


# --- Main Handlers ---
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    # ... (code remains the same)
    user = update.effective_user
    async with context.bot_data["db_session_factory"]() as session:
        result = await session.execute(select(User).filter_by(user_id=user.id))
        if not result.scalar_one_or_none():
            session.add(User(user_id=user.id, telegram_username=user.username))
            await session.commit()
    keyboard = [[InlineKeyboardButton("💰 Deposit", callback_data="deposit_start")]]
    reply_markup = InlineKeyboardMarkup(keyboard)
    await update.message.reply_html(rf"Hello {user.mention_html()}! Please choose an option:", reply_markup=reply_markup)

async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    await update.message.reply_text("Operation cancelled."); context.user_data.clear(); return ConversationHandler.END


# --- Deposit Conversation Handlers ---
async def deposit_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query; await query.answer()
    context.user_data.clear(); context.user_data['mode'] = 'new'
    await query.edit_message_text(text="Please enter your 1xBet User ID."); return ASKING_ID

async def receive_xbet_id(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    context.user_data['xbet_id'] = update.message.text
    if context.user_data.get('mode') == 'update':
        return await finalize_submission(update, context) # Go straight to finalizer
    await update.message.reply_text("Thank you. Please enter the deposit amount."); return ASKING_AMOUNT

async def receive_amount(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    context.user_data['amount'] = update.message.text
    if context.user_data.get('mode') == 'update':
        return await finalize_submission(update, context) # Go straight to finalizer
    bank_details = "Bank: KBZ Bank\nAccount Name: U Aung\nAccount Number: 9988776655"
    await update.message.reply_text(f"Please transfer to:\n\n{bank_details}\n\nThen send a screenshot."); return ASKING_SCREENSHOT

async def receive_screenshot(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if not update.message.photo:
        await update.message.reply_text("That is not a photo. Please send a screenshot."); return ASKING_SCREENSHOT
    context.user_data['photo_id'] = update.message.photo[-1].file_id
    return await finalize_submission(update, context)


# --- Admin Action Handlers ---
async def lock_request(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None: # (code remains the same)
    query = update.callback_query; request_id = query.data.split(":")[1]; admin = query.from_user
    async with context.bot_data["db_session_factory"]() as session:
        result = await session.execute(select(Transaction).filter_by(request_id=request_id))
        transaction = result.scalar_one_or_none()
        if transaction and transaction.status == 'pending':
            await query.answer("Request locked."); transaction.status = 'locked'; transaction.admin_id = admin.id; await session.commit()
            keyboard = [[InlineKeyboardButton("✅ Approve", callback_data=f"approve_req:{request_id}"), InlineKeyboardButton("❌ Reject", callback_data=f"reject_req:{request_id}")]]; reply_markup = InlineKeyboardMarkup(keyboard)
            new_caption = f"{query.message.caption_html}\n\n---\n<b>Status:</b> Locked by {admin.mention_html()}"
            await query.edit_message_caption(caption=new_caption, parse_mode='HTML', reply_markup=reply_markup)
        else: await query.answer("Already handled.", show_alert=True)

async def approve_request(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None: # (code remains the same)
    query = update.callback_query; request_id = query.data.split(":")[1]; admin = query.from_user
    async with context.bot_data["db_session_factory"]() as session:
        result = await session.execute(select(Transaction).filter_by(request_id=request_id))
        transaction = result.scalar_one_or_none()
        if transaction and transaction.status == 'locked' and transaction.admin_id == admin.id:
            await query.answer("Approved."); transaction.status = 'approved'; await session.commit()
            await context.bot.send_message(chat_id=transaction.user_id, text=f"✅ Your deposit request (ID: {request_id}) approved.")
            original_caption = re.sub(r'\n\n---\n.*', '', query.message.caption_html, flags=re.DOTALL)
            new_caption = f"{original_caption}\n\n---\n<b>Status:</b> ✅ Approved by {admin.mention_html()}"
            await query.edit_message_caption(caption=new_caption, parse_mode='HTML', reply_markup=None)
        else: await query.answer("Cannot approve.", show_alert=True)

async def reject_request_options(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None: # (code remains the same)
    query = update.callback_query; request_id = query.data.split(":")[1]; admin_id = query.from_user.id
    async with context.bot_data["db_session_factory"]() as session:
        result = await session.execute(select(Transaction).filter_by(request_id=request_id))
        transaction = result.scalar_one_or_none()
        if not (transaction and transaction.status == 'locked' and transaction.admin_id == admin_id):
            await query.answer("Cannot reject.", show_alert=True); return
    await query.answer()
    keyboard = [[InlineKeyboardButton("Wrong ID", callback_data=f"resubmit:wrong_id:{request_id}")], [InlineKeyboardButton("Wrong Amount", callback_data=f"resubmit:wrong_amount:{request_id}")], [InlineKeyboardButton("Wrong Slip", callback_data=f"resubmit:wrong_slip:{request_id}")]]; reply_markup = InlineKeyboardMarkup(keyboard)
    await query.edit_message_reply_markup(reply_markup=reply_markup)

async def request_resubmission(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query; await query.answer()
    _, reason_code, request_id = query.data.split(":")
    admin = query.from_user
    reasons = {"wrong_id": "Wrong 1xBet ID", "wrong_amount": "Wrong Amount", "wrong_slip": "Wrong/Unclear Screenshot"}
    reason_text = reasons.get(reason_code, "Unknown")
    async with context.bot_data["db_session_factory"]() as session:
        result = await session.execute(select(Transaction).filter_by(request_id=request_id))
        transaction = result.scalar_one_or_none()
        if not transaction: await query.answer("Txn not found.", show_alert=True); return ConversationHandler.END
        original_caption = re.sub(r'\n\n---\n.*', '', query.message.caption_html, flags=re.DOTALL)
        new_caption = f"{original_caption}\n\n---\n<b>Status:</b> ↩️ Returned to user by {admin.mention_html()} for correction."
        await query.edit_message_caption(caption=new_caption, parse_mode='HTML', reply_markup=None)
        context.user_data.clear(); context.user_data['mode'] = 'update'; context.user_data['request_id'] = request_id
        
        # Pre-fill data using the reliable database record
        if reason_code != 'wrong_id': context.user_data['xbet_id'] = transaction.xbet_id_from_user
        if reason_code != 'wrong_amount': context.user_data['amount'] = transaction.amount
        if reason_code != 'wrong_slip': context.user_data['photo_id'] = transaction.photo_file_id
        
        prompt_text = f"Your deposit was returned.\n<b>Reason:</b> {reason_text}.\nPlease submit the correct "
        next_state = ConversationHandler.END
        if reason_code == 'wrong_id':
            prompt_text += "1xBet ID."; next_state = ASKING_ID
        elif reason_code == 'wrong_amount':
            prompt_text += "amount."; next_state = ASKING_AMOUNT
        elif reason_code == 'wrong_slip':
            prompt_text += "screenshot."; next_state = ASKING_SCREENSHOT
        
        await context.bot.send_message(chat_id=transaction.user_id, text=prompt_text, parse_mode='HTML')
        return next_state


# --- FastAPI & Application Setup ---
@asynccontextmanager
async def lifespan(app: FastAPI): # (code remains the same)
    ASYNC_DATABASE_URL = DATABASE_URL.replace("postgresql://", "postgresql+asyncpg://")
    engine = create_async_engine(ASYNC_DATABASE_URL)
    async with engine.begin() as conn: await conn.run_sync(Base.metadata.create_all)
    db_session_factory = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    ptb_app = Application.builder().token(TELEGRAM_BOT_TOKEN).build()
    ptb_app.bot_data["db_session_factory"] = db_session_factory
    deposit_conv_handler = ConversationHandler(
        entry_points=[CallbackQueryHandler(deposit_start, pattern="^deposit_start$"), CallbackQueryHandler(request_resubmission, pattern=r"^resubmit:")],
        states={
            ASKING_ID: [MessageHandler(filters.TEXT & ~filters.COMMAND & filters.ChatType.PRIVATE, receive_xbet_id)],
            ASKING_AMOUNT: [MessageHandler(filters.TEXT & ~filters.COMMAND & filters.ChatType.PRIVATE, receive_amount)],
            ASKING_SCREENSHOT: [MessageHandler(filters.PHOTO & filters.ChatType.PRIVATE, receive_screenshot)],
        },
        fallbacks=[CommandHandler("cancel", cancel)], per_message=False, conversation_timeout=600
    )
    ptb_app.add_handler(CommandHandler("start", start, filters=filters.ChatType.PRIVATE))
    ptb_app.add_handler(deposit_conv_handler)
    ptb_app.add_handler(CallbackQueryHandler(lock_request, pattern=r"^lock_req:"))
    ptb_app.add_handler(CallbackQueryHandler(approve_request, pattern=r"^approve_req:"))
    ptb_app.add_handler(CallbackQueryHandler(reject_request_options, pattern=r"^reject_req:"))
    app.state.ptb_app = ptb_app
    await ptb_app.initialize(); webhook_url = f"https://{PUBLIC_URL}/webhook"; await ptb_app.bot.set_webhook(url=webhook_url, allowed_updates=Update.ALL_TYPES); await ptb_app.start()
    yield
    await ptb_app.stop(); await ptb_app.shutdown()

app = FastAPI(lifespan=lifespan)
@app.post("/webhook")
async def process_telegram_update(request: Request):
    ptb_app = request.app.state.ptb_app; update = Update.de_json(await request.json(), ptb_app.bot); await ptb_app.process_update(update); return Response(status_code=200)
@app.get("/")
async def health_check(): return {"status": "ok", "message": "Full resubmission workflow active."}