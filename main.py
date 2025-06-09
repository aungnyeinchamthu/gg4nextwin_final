import os
import asyncio
import logging
from contextlib import asynccontextmanager
from fastapi import FastAPI, Request, Response
import uvicorn

from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes

# --- Basic Setup ---
# Enable logging to see errors and information
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO
)
logger = logging.getLogger(__name__)

# Load the bot token from Railway's environment variables
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")


# --- Telegram Command Handlers ---
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Sends a message when the command /start is issued."""
    user = update.effective_user
    logger.info(f"User {user.username} ({user.id}) started the bot.")
    await update.message.reply_html(
        rf"Hello {user.mention_html()}! The bot is correctly initialized and running."
    )


# --- FastAPI & PTB Application Setup ---
# Build the python-telegram-bot application
ptb_app = Application.builder().token(TELEGRAM_BOT_TOKEN).build()
ptb_app.add_handler(CommandHandler("start", start))


# Define the lifespan manager for FastAPI
@asynccontextmanager
async def lifespan(app: FastAPI):
    """Handles startup and shutdown events for the application."""
    logger.info("Server startup: Initializing bot application...")
    await ptb_app.initialize()  # Initializes the bot application
    await ptb_app.start()       # Starts the background tasks of the bot

    yield  # The application runs while the server is up

    logger.info("Server shutdown: Stopping bot application...")
    await ptb_app.stop()        # Stops the background tasks
    await ptb_app.shutdown()    # Shuts down the bot application


# Create the FastAPI app with the defined lifespan manager
app = FastAPI(lifespan=lifespan)


@app.post(f"/webhook/{TELEGRAM_BOT_TOKEN}")
async def process_telegram_update(request: Request):
    """
    This is the single webhook endpoint that receives updates from Telegram.
    It passes the update to the python-telegram-bot library for processing.
    """
    try:
        json_data = await request.json()
        update = Update.de_json(json_data, ptb_app.bot)
        await ptb_app.process_update(update)
    except Exception as e:
        logger.error("Error processing update:", exc_info=True)
    
    return Response(status_code=200)


@app.get("/")
def health_check():
    """A simple endpoint to confirm the web server is running."""
    return {"status": "ok"}