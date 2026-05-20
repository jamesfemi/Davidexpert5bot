import logging
import os
import tempfile
from typing import Dict, List

from telegram import Update
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    filters,
    ConversationHandler,
    CallbackContext,
)

from PIL import Image, ImageDraw, ImageFont
import qrcode
from pyzbar.pyzbar import decode

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
    
    lines = []
    words = text.split()
    line = ""
    for word in words:
        test_line = f"{line} {word}".strip()
        bbox = draw.textbbox((0, 0), test_line, font=font)
        if bbox[2] - bbox[0] <= 760:
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

def decode_qr_from_image(image_path: str) -> str:
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

async def start(update: Update, context: CallbackContext) -> None:
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

async def cancel(update: Update, context: CallbackContext) -> int:
    user_id = update.effective_user.id
    if user_id in user_sessions:
        for path in user_sessions[user_id]:
            if os.path.exists(path):
                os.unlink(path)
        del user_sessions[user_id]
    await update.message.reply_text("Operation cancelled. Use /start to see the menu.")
    return ConversationHandler.END

async def images_to_pdf_start(update: Update, context: CallbackContext) -> int:
    user_id = update.effective_user.id
    user_sessions[user_id] = []
    await update.message.reply_text(
        "📸 Please send me the images you want to include in the PDF.\n"
        "Send them one by one. When you are done, type /done.\n"
        "Use /cancel to abort."
    )
    return WAITING_FOR_IMAGES

async def receive_image(update: Update, context: CallbackContext) -> int:
    user_id = update.effective_user.id
    if user_id not in user_sessions:
        user_sessions[user_id] = []
    
    photo = update.message.photo[-1]
    file = await photo.get_file()
    temp_file = tempfile.NamedTemporaryFile(delete=False, suffix='.jpg')
    await file.download_to_drive(temp_file.name)
    user_sessions[user_id].append(temp_file.name)
    
    await update.message.reply_text(
        f"Image received! ({len(user_sessions[user_id])} so far)\n"
        "Send another image or /done to create PDF."
    )
    return WAITING_FOR_IMAGES

async def create_pdf(update: Update, context: CallbackContext) -> int:
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
        for path in image_paths:
            if os.path.exists(path):
                os.unlink(path)
        if os.path.exists(output_pdf):
            os.unlink(output_pdf)
        del user_sessions[user_id]
    
    return ConversationHandler.END

async def text_to_image(update: Update, context: CallbackContext) -> None:
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

async def link_to_qr(update: Update, context: CallbackContext) -> None:
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

async def image_to_qr(update: Update, context: CallbackContext) -> None:
    await update.message.reply_text(
        "🖼️ Please send me an image that contains a QR code.\n"
        "I will read it and give you the decoded information."
    )

async def decode_qr_from_photo(update: Update, context: CallbackContext) -> None:
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
            await update.message.reply_text("❌ No QR code found in the image.")
    except Exception as e:
        logger.error(f"QR decode error: {e}")
        await update.message.reply_text("Failed to decode QR code.")
    finally:
        os.unlink(temp_file.name)

# ------------------------- Main -------------------------

def main():
    # Get token from environment variable (set on Render)
    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    if not token:
        logger.error("No TELEGRAM_BOT_TOKEN found. Set it as an environment variable.")
        return
    
    app = Application.builder().token(token).build()
    
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
    app.add_handler(conv_handler)
    
    app.add_handler(CommandHandler('start', start))
    app.add_handler(CommandHandler('cancel', cancel))
    app.add_handler(CommandHandler('text2image', text_to_image))
    app.add_handler(CommandHandler('qr', link_to_qr))
    app.add_handler(CommandHandler('img2qr', image_to_qr))
    app.add_handler(MessageHandler(filters.PHOTO & ~filters.COMMAND, decode_qr_from_photo))
    
    # Start polling (keeps the bot running)
    print("Bot is running...")
    app.run_polling()

if __name__ == '__main__':
    main()
