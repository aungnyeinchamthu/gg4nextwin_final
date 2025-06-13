import os
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request, Response
import uvicorn
import json

from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
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
# For this final test, we are hardcoding the URL. Remember to secure this later.
DATABASE_URL = "postgresql://postgres:fUvnhBOYsgxpUcOZFELemOekEtSsMAxU@hopper.proxy.rlwy.net:20186/railway"


# --- A Simple Start Handler ---
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Sends a simple welcome message."""
    await update.message.reply_html("Final Test: The bot is now stable and responding!")


# --- FastAPI & Application Setup ---
@asynccontextmanager
async def lifespan(app: FastAPI):
    """
    Handles startup and shutdown events.
    This new version creates the DB engine after the app is ready.
    """
    # Create DB engine and tables
    engine = create_async_engine(DATABASE_URL)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    
    # Setup PTB application
    ptb_app = Application.builder().token(TELEGRAM_BOT_TOKEN).build()
    ptb_app.add_handler(CommandHandler("start", start))
    
    # Store the app instance to be used by the webhook
    app.state.ptb_app = ptb_app

    # Set the webhook
    webhook_url = f"https://{os.getenv('PUBLIC_URL')}/webhook"
    await ptb_app.bot.set_webhook(url=webhook_url, allowed_updates=Update.ALL_TYPES)
    
    await ptb_app.start()
    yield
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