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
    user = update.effective_user
    photo_id = context.user_data.get('photo_id')
    xbet_id = context.user_data.get('xbet_id')
    amount = context.user_data.get('amount')
    
    async with context.bot_data["db_session_factory"]() as session:
        try:
            if context.user_data.get('mode') == 'update':
                # Handle resubmission
                original_request_id = context.user_data.get('original_request_id')
                result = await session.execute(
                    select(Transaction).filter_by(request_id=original_request_id)
                )
                transaction = result.scalar_one_or_none()
                if transaction:
                    transaction.status = "PENDING"
                    transaction.xbet_id_from_user = xbet_id or transaction.xbet_id_from_user
                    transaction.amount = amount or transaction.amount
                    transaction.photo_id = photo_id or transaction.photo_id
                    transaction.updated_at = datetime.utcnow()
            else:
                # Handle new submission
                transaction = Transaction(
                    request_id=generate_request_id(),
                    user_id=user.id,
                    xbet_id_from_user=xbet_id,
                    amount=amount,
                    photo_id=photo_id,
                    status="PENDING"
                )
                session.add(transaction)
            
            await session.commit()
            await session.refresh(transaction)

            # Send to admin group
            keyboard = [
                [
                    InlineKeyboardButton("🔒 Lock", callback_data=f"lock:{transaction.request_id}"),
                    InlineKeyboardButton("✅ Approve", callback_data=f"approve:{transaction.request_id}"),
                    InlineKeyboardButton("❌ Reject", callback_data=f"reject:{transaction.request_id}")
                ]
            ]
            reply_markup = InlineKeyboardMarkup(keyboard)
            
            await context.bot.send_photo(
                chat_id=ADMIN_DEPOSIT_GROUP_ID,
                photo=photo_id,
                caption=f"{'🔄 Resubmitted' if context.user_data.get('mode') == 'update' else '🆕 New'} Deposit Request\n"
                       f"Request ID: {transaction.request_id}\n"
                       f"User: {user.full_name}\n"
                       f"1xBet ID: {xbet_id}\n"
                       f"Amount: {amount}",
                reply_markup=reply_markup
            )

            await update.message.reply_text(
                "Your deposit request has been submitted. Please wait for confirmation."
            )
            
            context.user_data.clear()
            return ConversationHandler.END

        except Exception as e:
            logger.error(f"Error in finalize_submission: {e}")
            await update.message.reply_text(
                "Sorry, there was an error processing your request. Please try again."
            )
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
    query = update.callback_query
    await query.answer()
    _, reason_code, request_id = query.data.split(":")
    admin = query.from_user
    reasons = {
        "wrong_id": "Wrong 1xBet ID",
        "wrong_amount": "Wrong Amount", 
        "wrong_slip": "Wrong/Unclear Screenshot"
    }
    reason_text = reasons.get(reason_code, "Unknown")

    async with context.bot_data["db_session_factory"]() as session:
        # Get the transaction and user
        result = await session.execute(
            select(Transaction).filter_by(request_id=request_id)
            .options(selectinload(Transaction.user))
        )
        transaction = result.scalar_one_or_none()
        
        if not transaction:
            await query.edit_message_text("Error: Transaction not found")
            return ConversationHandler.END

        # Update transaction status
        transaction.status = "REJECTED"
        transaction.admin_id = admin.id
        transaction.rejection_reason = reason_text
        await session.commit()

        # Setup resubmission context
        context.user_data.clear()
        context.user_data['mode'] = 'update'
        context.user_data['original_request_id'] = request_id

        # Notify admin group about rejection
        admin_message = (
            f"🚫 Request {request_id} REJECTED\n"
            f"Reason: {reason_text}\n"
            f"By admin: {admin.full_name}"
        )
        await context.bot.edit_message_text(
            chat_id=ADMIN_DEPOSIT_GROUP_ID,
            message_id=query.message.message_id,
            text=admin_message
        )

        # Message the user and start resubmission flow
        user_message = f"Your deposit request was rejected.\nReason: {reason_text}\n\n"
        if reason_code == "wrong_id":
            user_message += "Please enter your correct 1xBet ID:"
            await context.bot.send_message(chat_id=transaction.user.telegram_id, text=user_message)
            return ASKING_ID
        elif reason_code == "wrong_amount":
            user_message += "Please enter the correct deposit amount:"
            await context.bot.send_message(chat_id=transaction.user.telegram_id, text=user_message)
            return ASKING_AMOUNT
        else:  # wrong_slip
            user_message += "Please send a clear screenshot of your transaction:"
            await context.bot.send_message(chat_id=transaction.user.telegram_id, text=user_message)
            return ASKING_SCREENSHOT
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