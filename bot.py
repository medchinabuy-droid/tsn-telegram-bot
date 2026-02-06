from telegram.ext import ApplicationBuilder
from config import BOT_TOKEN

tg_app = ApplicationBuilder().token(BOT_TOKEN).build()
