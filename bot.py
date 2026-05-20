import os
import logging
from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes
from fastapi import FastAPI, Request
import uvicorn

# --- Setup ---
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Get the bot token from environment variables
TELEGRAM_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
if not TELEGRAM_TOKEN:
    logger.error("TELEGRAM_BOT_TOKEN not set. Exiting.")
    exit(1)

# Create the Telegram Application
telegram_app = Application.builder().token(TELEGRAM_TOKEN).build()

# --- Basic Bot Handler ---
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Send a message when the /start command is issued."""
    await update.message.reply_text(
        "🛠️ Hi! I'm your Multi-Utility Bot.\n\n"
        "I can perform the following tasks:\n\n"
        "📸 *Images to PDF*\n"
        "   Send me images one by one, then use /done\n\n"
        "📝 *Text to Image*\n"
        "   Use /text2image <your text>\n\n"
        "🔗 *Link to QR Code*\n"
        "   Use /qr <your link>\n\n"
        "🖼️ *Image to QR Code*\n"
        "   Use /img2qr (send an image with a QR code)\n\n"
        "Use /cancel to abort any operation.",
        parse_mode='Markdown'
    )

telegram_app.add_handler(CommandHandler("start", start))
# ... (add all your other command handlers here: text2image, qr, etc.)

# --- Webhook Setup ---
async def setup_webhook():
    """Set the webhook for the bot."""
    webhook_url = os.environ.get("RENDER_EXTERNAL_URL")
    if not webhook_url:
        logger.error("RENDER_EXTERNAL_URL not set.")
        return

    # Construct the full webhook URL that Telegram will call
    full_webhook_url = f"{webhook_url}/webhook"
    await telegram_app.bot.set_webhook(full_webhook_url)
    logger.info(f"Webhook set to {full_webhook_url}")

# --- FastAPI App for Render ---
fastapi_app = FastAPI()

@fastapi_app.post("/webhook")
async def webhook(request: Request):
    """Handle incoming Telegram updates."""
    request_body = await request.json()
    # Process the update using the Telegram app
    await telegram_app.process_update(Update.de_json(request_body, telegram_app.bot))
    return {"status": "ok"}

@fastapi_app.get("/healthcheck")
async def healthcheck():
    """Health check endpoint to keep Render happy."""
    return {"status": "ok"}

# --- Main Entry Point ---
async def main():
    await telegram_app.initialize()
    await setup_webhook() # Set the webhook when the bot starts

    # Start the FastAPI web server
    port = int(os.environ.get("PORT", 8080))
    config = uvicorn.Config(fastapi_app, host="0.0.0.0", port=port, log_level="info")
    server = uvicorn.Server(config)
    await server.serve()

if __name__ == "__main__":
    import asyncio
    asyncio.run(main())
