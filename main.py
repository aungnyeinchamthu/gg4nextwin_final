import os
import logging
import html
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request, Response
import uvicorn
import json

from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.orm import sessionmaker

from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes

# --- Import our model and base ---
from database import Base

# --- Basic Setup ---
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO
)
logger = logging.getLogger(__name__)

# --- Load Environment Variables ---
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
DATABASE_URL = os.getenv("DATABASE_URL")
PUBLIC_URL = os.getenv("PUBLIC_URL")

if not DATABASE_URL or not TELEGRAM_BOT_TOKEN or not PUBLIC_URL:
    raise ValueError("One or more critical environment variables are not set!")


# --- A Simple Start Handler ---
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Sends a simple welcome message."""
    await update.message.reply_html(
        "<b>Success!</b>\n\nThe bot is now fully stable and operational."
        "\n\nWe can now continue building the full features."
    )


# --- FastAPI & Application Setup ---
@asynccontextmanager
async def lifespan(app: FastAPI):
    """Handles bot startup and shutdown."""
    # Correctly format the database URL for the async driver
    ASYNC_DATABASE_URL = DATABASE_URL.replace("postgresql://", "postgresql+asyncpg://")
    
    # Create DB engine and tables
    engine = create_async_engine(ASYNC_DATABASE_URL)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    
    # Build the PTB application and add handlers
    ptb_app = Application.builder().token(TELEGRAM_BOT_TOKEN).build()
    ptb_app.add_handler(CommandHandler("start", start))
    
    # Store the app instance to be used by the webhook
    app.state.ptb_app = ptb_app

    # --- THE CRITICAL FIX IS HERE ---
    # 1. Initialize the bot application first
    await ptb_app.initialize()
    
    # 2. Now set the webhook
    webhook_url = f"https://{PUBLIC_URL}/webhook"
    await ptb_app.bot.set_webhook(url=webhook_url, allowed_updates=Update.ALL_TYPES)
    
    # 3. Now start the application's background tasks
    await ptb_app.start()
    
    yield
    
    # Shutdown sequence
    await ptb_app.stop()
    await ptb_app.shutdown()

app = FastAPI(lifespan=lifespan)


# --- Webhook Endpoint ---
@app.post("/webhook")
async def process_telegram_update(request: Request):
    """Processes updates from Telegram."""
    ptb_app = request.app.state.ptb_app
    update = Update.de_json(await request.json(), ptb_app.bot)
    await ptb_app.process_update(update)
    return Response(status_code=200)

@app.get("/")
async def health_check():
    """Health check for Railway."""
    return {"status": "ok", "message": "Application is stable and running."}