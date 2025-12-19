import time
import gspread
from google.oauth2.service_account import Credentials
from telegram import Update
from telegram.ext import ApplicationBuilder, CommandHandler, ContextTypes

# ======================================================
# ПАУЗА ДЛЯ RENDER (ОЧЕНЬ ВАЖНО, УБИРАЕТ invalid_grant)
# ======================================================
time.sleep(20)

# ================= НАСТРОЙКИ =================
BOT_TOKEN = "ВСТАВЬ_СЮДА_ТОКЕН_БОТА"

CREDENTIALS_FILE = "telegram-bot-481523-ab2d76ed43b1.json"

SPREADSHEET_NAME = "ИМЯ_ТВОЕЙ_GOOGLE_ТАБЛИЦЫ"
SHEET_NAME = "Лист1"

SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive"
]

# ================= GOOGLE SHEETS =================
creds = Credentials.from_service_account_file(
    CREDENTIALS_FILE,
    scopes=SCOPES
)

gc = gspread.authorize(creds)
sheet = gc.open(SPREADSHEET_NAME).worksheet(SHEET_NAME)

# ================= TELEGRAM =================
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user

    await update.message.reply_text(
        f"Бот работает ✅\n\n"
        f"ФИО: {user.full_name}\n"
        f"Telegram ID: {user.id}"
    )

def main():
    app = ApplicationBuilder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.run_polling()

if __name__ == "__main__":
    main()
