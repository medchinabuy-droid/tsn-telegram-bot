import os
import logging
import asyncio
from datetime import datetime
from telegram import Update
from telegram.ext import (
    Application, CommandHandler, MessageHandler,
    ContextTypes, filters
)
from apscheduler.schedulers.asyncio import AsyncIOScheduler

from openai import AsyncOpenAI
from PIL import Image
import pytesseract
import aiohttp
import io

# ---------------- CONFIG ----------------

BOT_TOKEN = os.getenv("BOT_TOKEN")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
MONTHLY_FEE = int(os.getenv("MONTHLY_FEE", "2000"))
ADMIN_IDS = [int(x) for x in os.getenv("ADMIN_IDS", "").split(",")]

logging.basicConfig(level=logging.INFO)

# ---------------- GPT ----------------

gpt = AsyncOpenAI(api_key=OPENAI_API_KEY)

async def ask_gpt(system: str, user: str) -> str:
    try:
        r = await gpt.chat.completions.create(
            model="gpt-4.1-mini",
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": user}
            ],
            temperature=0.2,
            max_tokens=800
        )
        return r.choices[0].message.content.strip()
    except Exception as e:
        logging.error(f"GPT ERROR: {e}")
        return "Ошибка GPT"

# ---------------- HELPERS ----------------

def is_admin(user_id: int) -> bool:
    return user_id in ADMIN_IDS

blocked_users = set()
payments = {}  # user_id → list of amounts

# ---------------- OCR ----------------

async def ocr_image(file_bytes: bytes) -> str:
    img = Image.open(io.BytesIO(file_bytes))
    text = pytesseract.image_to_string(img, lang="rus")
    return text

# ---------------- START ----------------

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "👋 Бот поселковых взносов ТСН\n"
        "📎 Пришлите чек для проверки"
    )

# ---------------- GPT ADMIN PANEL ----------------

async def gpt_admin(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        return

    query = " ".join(context.args)
    if not query:
        await update.message.reply_text("Напиши запрос после /gpt")
        return

    await update.message.reply_text("🧠 GPT анализирует...")
    answer = await ask_gpt(
        "Ты помощник председателя ТСН.",
        query
    )
    await update.message.reply_text(answer)

# ---------------- CHECK HANDLER ----------------
async def handle_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user

    if user.id in blocked_users:
        await update.message.reply_text("🚫 Вы заблокированы. Обратитесь к председателю.")
        return

    msg = await update.message.reply_text("🔍 Проверяю чек...")

    photo = update.message.photo[-1]
    file = await photo.get_file()
    file_bytes = await file.download_as_bytearray()

    # OCR
    ocr_text = await ocr_image(file_bytes)

    # GPT анализ чека
    gpt_result = await ask_gpt(
        "Ты бухгалтер ТСН. Проверяешь чек поселковых взносов.",
        f"""
Текст чека:
{ocr_text}

Определи:
1. Есть ли сумма
2. Корректна ли она
3. Есть ли ошибки

Ответь строго:
СТАТУС: принят / ошибка
СУММА: число или 0
ПРИЧИНА: текст
"""
    )

    await msg.edit_text("🧠 Анализ завершён")

    # Парсинг
    status = "ошибка"
    amount = 0

    for line in gpt_result.splitlines():
        if "СТАТУС" in line:
            status = line.split(":")[-1].strip()
        if "СУММА" in line:
            try:
                amount = int("".join(filter(str.isdigit, line)))
            except:
                amount = 0

    # Проверка суммы
    if amount < MONTHLY_FEE:
        status = "ошибка"
        gpt_result += "\n❗ Сумма меньше ежемесячного взноса"

    # Реакция
    if status.lower() != "принят":
        blocked_users.add(user.id)

        await update.message.reply_text(
            "❌ Чек отклонён\n\n"
            f"{gpt_result}\n\n"
            "🚫 Пользователь временно заблокирован"
        )
        return

    payments.setdefault(user.id, []).append(amount)

    await update.message.reply_text(
        f"✅ Чек принят\n💰 Сумма: {amount} ₽"
    )

# ---------------- GPT FORECAST ----------------

async def forecast(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        return

    data = "\n".join(
        f"user {uid}: {sum(amts)}"
        for uid, amts in payments.items()
    )

    result = await ask_gpt(
        "Ты аналитик ТСН. Прогнозируешь задолженности.",
        f"""
История платежей:
{data}

Спрогнозируй:
- кто уйдёт в долг
- примерные суммы
"""
    )

    await update.message.reply_text(result)

# ---------------- MONTHLY REPORT ----------------

async def monthly_report(context: ContextTypes.DEFAULT_TYPE):
    text = "\n".join(
        f"user {uid}: {sum(amts)} ₽"
        for uid, amts in payments.items()
    )

    report = await ask_gpt(
        "Ты председатель ТСН. Составь официальный отчёт.",
        text
    )

    for admin in ADMIN_IDS:
        await context.bot.send_message(admin, report)

# ---------------- MAIN ----------------

def main():
    app = Application.builder().token(BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("gpt", gpt_admin))
    app.add_handler(CommandHandler("forecast", forecast))
    app.add_handler(MessageHandler(filters.PHOTO, handle_photo))

    scheduler = AsyncIOScheduler()
    scheduler.add_job(monthly_report, "cron", day=1, hour=9, args=[app])
    scheduler.start()

    app.run_webhook(
        listen="0.0.0.0",
        port=int(os.getenv("PORT", "8000")),
        webhook_url=os.getenv("WEBHOOK_URL")
    )

if __name__ == "__main__":
    main()
