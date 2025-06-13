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


# --- THIS IS THE NEW, CORRECTED FINALIZER FUNCTION ---
async def finalize_submission(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """This function finalizes both new and updated submissions."""
    user = update.effective_user
    photo_id = context.user_data.get('photo_id')
    xbet_id = context.user_data.get('xbet_id')
    amount = context.user_data.get('amount')

    # Get a database session
    session = context.bot_data["db_session_factory"]()

    try:
        # For updates, find and update the existing transaction. For new, create one.
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
        else:  # New submission
            request_id = generate_request_id()
            transaction = Transaction(
                user_id=user.id, request_id=request_id, type='DEPOSIT',
                amount=float(amount if amount else 0), status='pending',
                xbet_id_from_user=xbet_id, photo_file_id=photo_id
            )
            session.add(transaction)
            logger.info(f"Saving new transaction {request_id}")
        
        await session.commit()
        await session.refresh(transaction)

        # Prepare the message content
        caption_title = "--- <b>Resubmitted Deposit Request</b> ---\n" if context.user_data.get('mode') == 'update' else "--- <b>New Deposit Request</b> ---\n"
        admin_caption = (
            caption_title +
            f"<b>Request ID:</b> <code>{request_id}</code>\n<b>User:</b> {user.mention_html()} ({user.id})\n"
            f"<b>1xBet ID:</b> {transaction.xbet_id_from_user}\n<b>Amount:</b> {transaction.amount} MMK"
        )
        keyboard = [[InlineKeyboardButton("🔒 Lock & Take", callback_data=f"lock_req:{request_id}")]]
        reply_markup = InlineKeyboardMarkup(keyboard)

        # If this is an update and we know where the original admin message is, EDIT it.
        if context.user_data.get('mode') == 'update' and transaction.admin_chat_id and transaction.admin_message_id:
            try:
                # For a resubmission, we need to send a new photo with the updated caption
                # because Telegram API does not allow editing a photo, only the caption.
                # So we delete the old message and send a new one.
                await context.bot.delete_message(chat_id=transaction.admin_chat_id, message_id=transaction.admin_message_id)
                logger.info(f"Deleted old admin message for {request_id}")

                admin_message = await context.bot.send_photo(
                    chat_id=ADMIN_DEPOSIT_GROUP_ID, photo=transaction.photo_file_id,
                    caption=admin_caption, parse_mode='HTML', reply_markup=reply_markup
                )
                transaction.admin_message_id = admin_message.message_id
                logger.info(f"Sent new admin message for resubmitted request {request_id}")

            except Exception as e:
                logger.error(f"Could not edit/delete old message for {request_id}, sending new one. Error: {e}")
                admin_message = await context.bot.send_photo(
                    chat_id=ADMIN_DEPOSIT_GROUP_ID, photo=transaction.photo_file_id,
                    caption=admin_caption, parse_mode='HTML', reply_markup=reply_markup
                )
                transaction.admin_chat_id = admin_message.chat_id
                transaction.admin_message_id = admin_message.message_id
        
        # Otherwise (for all new requests), send a new photo message.
        else:
            if ADMIN_DEPOSIT_GROUP_ID:
                admin_message = await context.bot.send_photo(
                    chat_id=ADMIN_DEPOSIT_GROUP_ID, photo=transaction.photo_file_id,
                    caption=admin_caption, parse_mode='HTML', reply_markup=reply_markup
                )
                transaction.admin_chat_id = admin_message.chat_id
                transaction.admin_message_id = admin_message.message_id

        await session.commit()
        await context.bot.send_message(chat_id=user.id, text="Thank you! Your request has been submitted and is being reviewed.")

    finally:
        await session.close()
        context.user_data.clear()
        return ConversationHandler.END


# --- All other handlers and setup code remain the same ---
# (I am including the full code below for you to copy and paste)

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
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

async def deposit_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query; await query.answer()
    context.user_data.clear(); context.user_data['mode'] = 'new'
    await query.edit_message_text(text="Please enter your 1xBet User ID."); return ASKING_ID

async def receive_xbet_id(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    context.user_data['xbet_id'] = update.message.text
    if context.user_data.get('mode') == 'update':
        # If the user was asked for a new slip, we already have their ID from the DB
        # We need the photo before we can finalize
        if 'photo_id' not in context.user_data:
            await update.message.reply_text("ID has been updated. Please send the screenshot again to confirm.")
            return ASKING_SCREENSHOT
        return await finalize_submission(update, context)
    await update.message.reply_text("Thank you. Please enter the deposit amount."); return ASKING_AMOUNT

async def receive_amount(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    context.user_data['amount'] = update.message.text
    if context.user_data.get('mode') == 'update':
        # If the user was asked for a new slip, we already have their amount from the DB
        # We need the photo before we can finalize
        if 'photo_id' not in context.user_data:
            await update.message.reply_text("Amount has been updated. Please send the screenshot again to confirm.")
            return ASKING_SCREENSHOT
        return await finalize_submission(update, context)
    bank_details = "Bank: KBZ Bank\nAccount Name: U Aung\nAccount Number: 9988776655"
    await update.message.reply_text(f"Please transfer to:\n\n{bank_details}\n\nThen send a screenshot."); return ASKING_SCREENSHOT

async def receive_screenshot(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if not update.message.photo:
        await update.message.reply_text("That is not a photo. Please send a screenshot."); return ASKING_SCREENSHOT
    context.user_data['photo_id'] = update.message.photo[-1].file_id
    return await finalize_submission(update, context)

async def lock_request(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
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

async def approve_request(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
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

async def reject_request_options(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
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
        
        # We must delete the old message because we can't edit a photo, only the caption.
        # Deleting and sending a new one is the cleanest user experience.
        try:
            await context.bot.delete_message(chat_id=transaction.admin_chat_id, message_id=transaction.admin_message_id)
        except Exception as e:
            logger.warning(f"Could not delete old admin message {transaction.admin_message_id}. Error: {e}")
        
        await context.bot.send_message(
            chat_id=transaction.admin_chat_id,
            text=f"Request <code>{request_id}</code> was returned to user by {admin.mention_html()} for correction.\n<b>Reason:</b> {reason_text}",
            parse_mode='HTML'
        )

        context.user_data.clear(); context.user_data['mode'] = 'update'; context.user_data['request_id'] = request_id
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
async def lifespan(app: FastAPI):
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