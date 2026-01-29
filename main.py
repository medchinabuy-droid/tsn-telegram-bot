import os
import base64
from datetime import datetime

from telegram import Update
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    ContextTypes,
    filters,
)

from openai import OpenAI

# ================= НАСТРОЙКИ =================

BOT_TOKEN = os.getenv("BOT_TOKEN")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")

ADMIN_IDS = {123456789}  # ← замени на свой Telegram ID
MONTHLY_FEE = 3000

client = OpenAI(api_key=OPENAI_API_KEY)

# ================= ХРАНИЛИЩЕ =================
# ⚠️ В ПРОДЕ заменить на БД

USERS = {}
BLOCKED = set()

# ================= GPT =================

async def gpt_text(prompt: str) -> str:
    resp = client.chat.completions.create(
        model="gpt-4o-mini",
        messages=[{"role": "user", "content": prompt}],
    )
    return resp.choices[0].message.content.strip()


async def gpt_vision(image_bytes: bytes) -> str:
    b64 = base64.b64encode(image_bytes).decode()
    resp = client.chat.completions.create(
        model="gpt-4o",
        messages=[{
            "role": "user",
            "content": [
                {"type": "text", "text": "Извлеки сумму оплаты из чека. Ответь только числом."},
                {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{b64}"}},
            ],
        }],
    )
    return resp.choices[0].message.content.strip()


# ================= УТИЛИТЫ =================

def is_admin(user_id: int) -> bool:
    return user_id in ADMIN_IDS


def get_user(uid, tg_user):
    if uid not in USERS:
        USERS[uid] = {
            "fio": tg_user.full_name,
            "username": tg_user.username or "",
            "debt": MONTHLY_FEE,
            "fails": 0,
            "blocked": False,
            "payments": [],
        }
    return USERS[uid]


# ================= КОМАНДЫ =================

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = get_user(update.effective_user.id, update.effective_user)

    if user["blocked"]:
        await update.message.reply_text("🚫 Вы заблокированы.")
        return

    await update.message.reply_text(
        "👋 Добро пожаловать в бот ТСН.\n\n"
        "📸 Отправьте фото чека\n"
        "💰 /debt — задолженность"
    )


async def debt(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = get_user(update.effective_user.id, update.effective_user)
    await update.message.reply_text(f"💸 Ваша задолженность: {user['debt']} ₽")


# ================= ОБРАБОТКА ЧЕКА =================

async def handle_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    user = get_user(uid, update.effective_user)

    if user["blocked"]:
        await update.message.reply_text("🚫 Вы заблокированы.")
        return

    photo = update.message.photo[-1]
    file = await photo.get_file()
    image_bytes = await file.download_as_bytearray()

    await update.message.reply_text("🔍 Проверяю чек...")

    try:
        amount_text = await gpt_vision(bytes(image_bytes))
        amount = int("".join(filter(str.isdigit, amount_text)))
    except Exception:
        await update.message.reply_text("❌ Не удалось распознать сумму.")
        return

    if amount <= 0:
        user["fails"] += 1
        await update.message.reply_text("❌ Некорректная сумма.")

    elif amount < MONTHLY_FEE:
        user["fails"] += 1
        await update.message.reply_text(f"⚠️ Недостаточная сумма: {amount} ₽")

    else:
        user["debt"] = max(0, user["debt"] - amount)
        user["payments"].append({"amount": amount, "date": datetime.now().isoformat()})
        user["fails"] = 0
        await update.message.reply_text("✅ Оплата принята")

    if user["fails"] >= 3:
        user["blocked"] = True
        BLOCKED.add(uid)
        await update.message.reply_text("🚫 Вы заблокированы (3 отказа)")


# ================= GPT АДМИНКА =================

async def gpt_admin(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        return

    total_debt = sum(u["debt"] for u in USERS.values())
    prompt = (
        "Сделай краткий управленческий отчёт ТСН:\n"
        f"Пользователей: {len(USERS)}\n"
        f"Задолженность: {total_debt} ₽\n"
        f"Заблокировано: {len(BLOCKED)}"
    )

    report = await gpt_text(prompt)
    await update.message.reply_text(report)


async def forecast(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        return

    data = "\n".join(
        f"{u['fio']} — долг {u['debt']} ₽"
        for u in USERS.values()
    )

    prompt = (
        "Проанализируй задолженности ТСН и спрогнозируй риски.\n\n"
        + data
    )

    result = await gpt_text(prompt)
    await update.message.reply_text(result)


# ================= ЗАПУСК =================

def main():
    app = Application.builder().token(BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("debt", debt))
    app.add_handler(CommandHandler("gpt_report", gpt_admin))
    app.add_handler(CommandHandler("forecast", forecast))

    app.add_handler(MessageHandler(filters.PHOTO, handle_photo))

    app.run_polling()


if __name__ == "__main__":
    main()
