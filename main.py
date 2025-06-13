from fastapi import FastAPI
import logging

# --- Basic Setup ---
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# --- Create The Simplest Possible App ---
app = FastAPI()

@app.get("/")
def health_check():
    """
    A simple endpoint that just returns "ok".
    This is to test if the server can run at all.
    """
    logger.info("Minimal App's Health Check was hit successfully!")
    return {"status": "ok", "message": "The minimal test application is running."}