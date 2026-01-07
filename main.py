import os
import json
import logging
import asyncio
from http.server import BaseHTTPRequestHandler, HTTPServer

from telegram import Update
from telegram.ext import (
    Application,
    CommandHandler,
    ContextTypes,
)

# =========================
# ЛОГИ
# =========================
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s"
)
logger = logging.getLogger(__name__)

# =========================
# ENV
# =========================
BOT_TOKEN = os.getenv("BOT_TOKEN")
WEBHOOK_URL = os.getenv("WEBHOOK_URL")  # https://xxxx.onrender.com/webhook
PORT = int(os.getenv("PORT", "10000"))
GOOGLE_JSON = os.getenv("GOOGLE_SERVICE_ACCOUNT_JSON")

if not BOT_TOKEN:
    raise RuntimeError("❌ BOT_TOKEN не задан")

if not WEBHOOK_URL:
    raise RuntimeError("❌ WEBHOOK_URL не задан")

# =========================
# TELEGRAM HANDLERS
# =========================
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("✅ Бот запущен и работает")

async def ping(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("🏓 pong")

# =========================
# GOOGLE CHECK (не падаем)
# =========================
def check_google_creds():
    if not GOOGLE_JSON:
        logger.error("❌ GOOGLE_SERVICE_ACCOUNT_JSON отсутствует")
        return None
    try:
        return json.loads(GOOGLE_JSON)
    except Exception as e:
        logger.error(f"❌ Ошибка GOOGLE_SERVICE_ACCOUNT_JSON: {e}")
        return None

# =========================
# HTTP SERVER (Render требует порт)
# =========================
class WebhookHandler(BaseHTTPRequestHandler):
    def do_POST(self):
        if self.path != "/webhook":
            self.send_response(404)
            self.end_headers()
            return

        length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(length)

        asyncio.run(application.update_queue.put(Update.de_json(
            json.loads(body), application.bot
        )))

        self.send_response(200)
        self.end_headers()

    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"OK")

# =========================
# MAIN
# =========================
async def main():
    global application

    check_google_creds()

    application = Application.builder().token(BOT_TOKEN).build()

    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("ping", ping))

    await application.bot.set_webhook(WEBHOOK_URL)
    logger.info(f"🌍 Webhook установлен: {WEBHOOK_URL}")

    await application.initialize()
    await application.start()

    server = HTTPServer(("0.0.0.0", PORT), WebhookHandler)
    logger.info(f"🚀 HTTP сервер запущен на порту {PORT}")
    server.serve_forever()

# =========================
# ENTRY
# =========================
if __name__ == "__main__":
    asyncio.run(main())
