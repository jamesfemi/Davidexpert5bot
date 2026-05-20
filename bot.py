import logging
import os
import tempfile
import asyncio
from typing import Dict, List, Optional
from contextlib import asynccontextmanager

from telegram import Update
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    filters,
    ConversationHandler,
    ContextTypes,
)
from telegram.request import HTTPXRequest

from PIL import Image, ImageDraw, ImageFont
import qrcode
from pyzbar.pyzbar import decode
from starlette.applications import Starlette
from starlette.responses import JSONResponse
from starlette.routing import Route
import uvicorn

# Enable logging
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO
)
logger = logging.getLogger(__name__)

# Conversation states
WAITING_FOR_IMAGES = 1

# Temporary storage for user sessions (in production, use a database)
user_sessions: Dict[int, List[str]] = {}

# ------------------------- Helper Functions -------------------------

def generate_text_image(text: str) -> str:
    """Convert text to an image and return the image file path."""
    img = Image.new('RGB', (800, 400), color='white')
    draw = ImageDraw.Draw(img)
    try:
        font = ImageFont.truetype("arial.ttf", 24)
    except:
        font = ImageFont.load_default()

    # Wrap text to fit inside the image
    lines = []
    words = text.split()
    line = ""
    for word in words:
        test_line = f"{line} {word}".strip()
        bbox = draw.textbbox((0, 0), test_line, font=font)
        if bbox[2] - bbox[0] <= 760:  # 800 - 40 margin
            line = test_line
        else:
            lines.append(line)
            line = word
    lines.append(line)

    y = 50
    for line in lines:
        draw.text((40, y), line, fill='black', font=font)
        y += 30

    temp_file = tempfile.NamedTemporaryFile(delete=False, suffix='.png')
    img.save(temp_file.name, 'PNG')
    return temp_file.name

def generate_qr_from_link(link: str) -> str:
    """Generate QR code from a link and return the image file path."""
    qr = qrcode.QRCode(version=1, box_size=10, border=4)
    qr.add_data(link)
    qr.make(fit=True)
    img = qr.make_image(fill_color="black", back_color="white")
    temp_file = tempfile.NamedTemporaryFile(delete=False, suffix='.png')
    img.save(temp_file.name)
    return temp_file.name

def decode_qr_from_image(image_path: str) -> Optional[str]:
    """Decode QR code from an image and return the decoded data."""
    img = Image.open(image_path)
    decoded_objects = decode(img)
    if decoded_objects:
        return decoded_objects[0].data.decode('utf-8')
    return None

def images_to_pdf(image_paths: List[str], output_path: str) -> str:
    """Convert a list of images into a single PDF."""
    images = []
    for path in image_paths:
        img = Image.open(path)
        img = img.convert('RGB')
        images.append(img)
    if images:
        images[0].save(output_path, save_all=True, append_images=images[1:])
    return output_path

# ------------------------- Bot Handlers -------------------------

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Send a welcome message with the menu."""
    menu_text = (
        "🛠️ *Multi-Utility Bot*\n\n"
        "I can perform the following tasks:\n\n"
        "📸 *Images to PDF*\n"
        "   Send me images one by one, then use /done\n\n"
        "📝 *Text to Image*\n"
        "   Use /text2image <your text>\n\n"
        "🔗 *Link to QR Code*\n"
        "   Use /qr <your link>\n\n"
        "🖼️ *Image to QR Code*\n"
        "   Use /img2qr (send an image with a QR code)\n\n"
        "Use /cancel to abort any operation."
    )
    await update.message.reply_text(menu_text, parse_mode='Markdown')

async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Cancel any ongoing conversation and clear session."""
    user_id = update.effective_user.id
    if user_id in user_sessions:
        # Clean up temporary files if any
        for path in user_sessions[user_id]:
            if os.path.exists(path):
                os.unlink(path)
        del user_sessions[user_id]
    await update.message.reply_text("Operation cancelled. Use /start to see the menu.")
    return ConversationHandler.END

async def images_to_pdf_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Start the conversation: ask user to send images."""
    user_id = update.effective_user.id
    user_sessions[user_id] = []  # list to store temp image paths
    await update.message.reply_text(
        "📸 Please send me the images you want to include in the PDF.\n"
        "Send them one by one. When you are done, type /done.\n"
        "Use /cancel to abort."
    )
    return WAITING_FOR_IMAGES

async def receive_image(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Save each received image to a temporary file."""
    user_id = update.effective_user.id
    if user_id not in user_sessions:
        user_sessions[user_id] = []

    photo = update.message.photo[-1]  # get highest resolution
    file = await photo.get_file()
    temp_file = tempfile.NamedTemporaryFile(delete=False, suffix='.jpg')
    await file.download_to_drive(temp_file.name)
    user_sessions[user_id].append(temp_file.name)

    await update.message.reply_text(
        f"Image received! ({len(user_sessions[user_id])} so far)\n"
        "Send another image or /done to create PDF."
    )
    return WAITING_FOR_IMAGES

async def create_pdf(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Generate PDF from collected images and send it to user."""
    user_id = update.effective_user.id
    if user_id not in user_sessions or not user_sessions[user_id]:
        await update.message.reply_text("No images received. Please send at least one image first.")
        return WAITING_FOR_IMAGES

    image_paths = user_sessions[user_id]
    output_pdf = tempfile.NamedTemporaryFile(delete=False, suffix='.pdf').name

    try:
        images_to_pdf(image_paths, output_pdf)
        with open(output_pdf, 'rb') as f:
            await update.message.reply_document(
                document=f,
                filename="converted.pdf",
                caption=f"✅ PDF created from {len(image_paths)} image(s)."
            )
    except Exception as e:
        logger.error(f"PDF creation error: {e}")
        await update.message.reply_text("Sorry, failed to create PDF. Please try again.")
    finally:
        # Clean up temporary image files
        for path in image_paths:
            if os.path.exists(path):
                os.unlink(path)
        if os.path.exists(output_pdf):
            os.unlink(output_pdf)
        del user_sessions[user_id]

    return ConversationHandler.END

async def text_to_image(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Generate an image from the provided text."""
    text = ' '.join(context.args)
    if not text:
        await update.message.reply_text("Usage: /text2image <your text>")
        return

    await update.message.reply_text("✍️ Generating image...")
    try:
        img_path = generate_text_image(text)
        with open(img_path, 'rb') as img_file:
            await update.message.reply_photo(photo=img_file, caption="✅ Here's your image.")
        os.unlink(img_path)
    except Exception as e:
        logger.error(f"Text2Image error: {e}")
        await update.message.reply_text("Failed to generate image. Please try again.")

async def link_to_qr(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Generate a QR code from the provided link."""
    link = ' '.join(context.args)
    if not link:
        await update.message.reply_text("Usage: /qr <your link>")
        return

    await update.message.reply_text("🔲 Generating QR code...")
    try:
        qr_path = generate_qr_from_link(link)
        with open(qr_path, 'rb') as qr_file:
            await update.message.reply_photo(photo=qr_file, caption="✅ QR code generated.")
        os.unlink(qr_path)
    except Exception as e:
        logger.error(f"QR generation error: {e}")
        await update.message.reply_text("Failed to generate QR code.")

async def image_to_qr(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Prompt user to send an image containing a QR code."""
    await update.message.reply_text(
        "🖼️ Please send me an image that contains a QR code.\n"
        "I will read it and give you the decoded information."
    )

async def decode_qr_from_photo(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Decode QR code from the received image."""
    photo = update.message.photo[-1]
    file = await photo.get_file()
    temp_file = tempfile.NamedTemporaryFile(delete=False, suffix='.jpg')
    await file.download_to_drive(temp_file.name)

    await update.message.reply_text("🔍 Decoding QR code...")
    try:
        decoded_data = decode_qr_from_image(temp_file.name)
        if decoded_data:
            await update.message.reply_text(
                f"✅ QR code content:\n\n`{decoded_data}`",
                parse_mode='Markdown'
            )
        else:
            await update.message.reply_text("❌ No QR code found in the image. Please send a clear QR code image.")
    except Exception as e:
        logger.error(f"QR decode error: {e}")
        await update.message.reply_text("Failed to decode QR code. Make sure the image is clear.")
    finally:
        os.unlink(temp_file.name)

# ------------------------- Webhook Setup -------------------------

async def healthcheck(request):
    """Health check endpoint for Render."""
    return JSONResponse({"status": "ok"})

async def setup_webhook(app: Application):
    """Set up the webhook for the bot."""
    # Wait for the application to be ready
    await app.initialize()
    await app.start()
    
    # Get the webhook URL from environment variable
    webhook_url = os.environ.get("RENDER_EXTERNAL_URL")
    if not webhook_url:
        logger.error("RENDER_EXTERNAL_URL not set. Webhook cannot be configured.")
        return
    
    webhook_path = f"/webhook/{app.bot.token}"
    full_webhook_url = f"{webhook_url}{webhook_path}"
    
    # Set the webhook
    await app.bot.set_webhook(full_webhook_url)
    logger.info(f"Webhook set to {full_webhook_url}")

async def shutdown_webhook(app: Application):
    """Shutdown webhook and clean up."""
    await app.bot.delete_webhook()
    await app.shutdown()

@asynccontextmanager
async def lifespan(app: Starlette):
    """Lifespan context manager for Starlette app."""
    # Startup
    webhook_url = os.environ.get("RENDER_EXTERNAL_URL")
    if webhook_url:
        await telegram_app.bot.set_webhook(f"{webhook_url}/webhook")
        logger.info(f"Webhook set to {webhook_url}/webhook")
    else:
        logger.warning("RENDER_EXTERNAL_URL not set, webhook not configured")
    yield
    # Shutdown
    await telegram_app.bot.delete_webhook()
    await telegram_app.shutdown()

# ------------------------- Main -------------------------

# Create the Telegram application
telegram_app = None

async def main():
    global telegram_app
    
    # Get token from environment variable
    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    if not token:
        logger.error("No TELEGRAM_BOT_TOKEN found. Set it as an environment variable.")
        return

    # Create the Application with a custom HTTP client for better compatibility
    http_client = HTTPXRequest(http_version="1.1")
    telegram_app = Application.builder().token(token).http_client(http_client).build()
    
    # Add conversation handler for Images->PDF
    conv_handler = ConversationHandler(
        entry_points=[CommandHandler('start_pdf', images_to_pdf_start)],
        states={
            WAITING_FOR_IMAGES: [
                MessageHandler(filters.PHOTO, receive_image),
                CommandHandler('done', create_pdf),
            ],
        },
        fallbacks=[CommandHandler('cancel', cancel)],
    )
    telegram_app.add_handler(conv_handler)
    
    # Add other command handlers
    telegram_app.add_handler(CommandHandler('start', start))
    telegram_app.add_handler(CommandHandler('cancel', cancel))
    telegram_app.add_handler(CommandHandler('text2image', text_to_image))
    telegram_app.add_handler(CommandHandler('qr', link_to_qr))
    telegram_app.add_handler(CommandHandler('img2qr', image_to_qr))
    telegram_app.add_handler(MessageHandler(filters.PHOTO & ~filters.COMMAND, decode_qr_from_photo))
    
    # Initialize the application
    await telegram_app.initialize()
    
    # Set up webhook if running on Render
    if os.environ.get("RENDER"):
        webhook_url = os.environ.get("RENDER_EXTERNAL_URL")
        if webhook_url:
            await telegram_app.bot.set_webhook(f"{webhook_url}/webhook")
            logger.info(f"Webhook set to {webhook_url}/webhook")
        else:
            logger.warning("RENDER_EXTERNAL_URL not set, webhook not configured")
        
        # Create Starlette app for handling webhooks and health checks
        starlette_app = Starlette(
            routes=[
                Route("/healthcheck", healthcheck, methods=["GET"]),
                Route("/webhook", telegram_app.webhook_endpoint, methods=["POST"]),
            ],
            lifespan=lifespan,
        )
        
        # Run the Starlette app with uvicorn
        port = int(os.environ.get("PORT", 8080))
        config = uvicorn.Config(starlette_app, host="0.0.0.0", port=port, log_level="info")
        server = uvicorn.Server(config)
        await server.serve()
    else:
        # Run in polling mode for local development
        logger.info("Running in polling mode for local development")
        await telegram_app.start()
        await telegram_app.updater.start_polling()
        # Keep the bot running
        await asyncio.Event().wait()

if __name__ == '__main__':
    asyncio.run(main())
