import os
import logging
import base64
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from telegram import (
    Update,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
)
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    ContextTypes,
    CallbackQueryHandler,
    filters,
)

from openai import OpenAI

# ================= НАСТРОЙКИ =================

BOT_TOKEN = os.getenv("BOT_TOKEN")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")

ADMIN_IDS = {123456789}  # <-- ID админов

TZ = ZoneInfo("Europe/Moscow")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)

client = OpenAI(api_key=OPENAI_API_KEY)

# ================= ХРАНИЛИЩЕ (ВРЕМЕННО) =================
# В проде — БД или Google Sheets

USERS = {}        # user_id -> dict
CHECKS = []       # список чеков
BLOCKED = set()   # заблокированные пользователи


# ================= УТИЛИТЫ =================

def now():
    return datetime.now(TZ)


def is_admin(user_id: int) -> bool:
    return user_id in ADMIN_IDS


def get_user(update: Update):
    u = update.effective_user
    return {
        "id": u.id,
        "username": u.username,
        "first_name": u.first_name,
        "last_name": u.last_name,
    }


def ensure_user(user):
    if user["id"] not in USERS:
        USERS[user["id"]] = {
            "fio": f'{user["first_name"] or ""} {user["last_name"] or ""}'.strip(),
            "phone": None,
            "username": user["username"],
            "debt": 0.0,
            "blocked": False,
            "created": now(),
        }


# ================= GPT =================

async def gpt_text(prompt: str) -> str:
    resp = client.chat.completions.create(
        model="gpt-4o-mini",
        messages=[
            {"role": "system", "content": "Ты помощник ТСН. Отвечай кратко и по делу."},
            {"role": "user", "content": prompt},
        ],
    )
    return resp.choices[0].message.content


async def gpt_vision(image_bytes: bytes, prompt: str) -> str:
    image_b64 = base64.b64encode(image_bytes).decode()

    resp = client.chat.completions.create(
        model="gpt-4o",
        messages=[
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": prompt},
                    {
                        "type": "image_url",
                        "image_url": {
                            "url": f"data:image/jpeg;base64,{image_b64}"
                        },
                    },
                ],
            }
        ],
    )
    return resp.choices[0].message.content


# ================= КОМАНДЫ =================

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = get_user(update)
    ensure_user(user)

    if user["id"] in BLOCKED:
        await update.message.reply_text("🚫 Вы заблокированы.")
        return

    await update.message.reply_text(
        "👋 Добро пожаловать в бот ТСН\n\n"
        "📸 Отправьте фото чека\n"
        "📊 /debt — ваша задолженность"
    )


async def debt(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = get_user(update)
    ensure_user(user)

    data = USERS[user["id"]]
    await update.message.reply_text(
        f"💸 Ваша задолженность: {data['debt']} ₽"
    )


# ================= ЧЕКИ =================

async def handle_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = get_user(update)
    ensure_user(user)

    if user["id"] in BLOCKED:
        return

    photo = update.message.photo[-1]
    file = await photo.get_file()
    image_bytes = await file.download_as_bytearray()

    prompt = (
        "Это фото чека. "
        "Определи сумму платежа в рублях. "
        "Ответь строго числом. Если не чек — напиши NO."
    )

    try:
        result = await gpt_vision(image_bytes, prompt)
    except Exception as e:
        logger.exception(e)
        await update.message.reply_text("❌ Ошибка обработки чека.")
        return

    if "NO" in result.upper():
        await reject_check(user["id"], "Не чек")
        await update.message.reply_text("❌ Это не чек.")
        return

    try:
        amount = float(result.replace(",", "."))
    except ValueError:
        await reject_check(user["id"], "Не распознана сумма")
        await update.message.reply_text("❌ Не удалось распознать сумму.")
        return

    CHECKS.append({
        "user_id": user["id"],
        "amount": amount,
        "date": now(),
        "status": "accepted",
    })

    USERS[user["id"]]["debt"] -= amount

    await update.message.reply_text(f"✅ Чек принят. Сумма: {amount} ₽")


async def reject_check(user_id: int, reason: str):
    CHECKS.append({
        "user_id": user_id,
        "amount": 0,
        "date": now(),
        "status": "rejected",
        "reason": reason,
    })

    # авто-блок при 3 отказах
    rejected = [
        c for c in CHECKS
        if c["user_id"] == user_id and c["status"] == "rejected"
    ]

    if len(rejected) >= 3:
        BLOCKED.add(user_id)
        USERS[user_id]["blocked"] = True


# ================= GPT АДМИН =================

async def gpt_admin(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        return

    prompt = "Сформируй краткий отчёт по задолженностям ТСН."
    text = await gpt_text(prompt)

    await update.message.reply_text(text)


# ================= ПРОГНОЗ =================

async def gpt_forecast(context: ContextTypes.DEFAULT_TYPE):
    total_debt = sum(u["debt"] for u in USERS.values())

    prompt = (
        f"Общая задолженность ТСН: {total_debt} ₽. "
        "Спрогнозируй ситуацию на следующий месяц."
    )

    text = await gpt_text(prompt)

    for admin_id in ADMIN_IDS:
        await context.bot.send_message(admin_id, f"📈 Прогноз:\n{text}")
# ================= УВЕДОМЛЕНИЯ =================

async def remind_debt(context: ContextTypes.DEFAULT_TYPE):
    for user_id, data in USERS.items():
        if data["debt"] > 0 and not data["blocked"]:
            try:
                await context.bot.send_message(
                    chat_id=user_id,
                    text=(
                        f"🔔 Напоминание\n\n"
                        f"У вас задолженность: {data['debt']} ₽\n"
                        f"Пожалуйста, оплатите взнос."
                    )
                )
            except Exception:
                pass


# ================= МЕСЯЧНЫЙ ОТЧЁТ =================

async def monthly_report(context: ContextTypes.DEFAULT_TYPE):
    total_debt = sum(u["debt"] for u in USERS.values())
    total_users = len(USERS)
    blocked = len(BLOCKED)

    prompt = (
        "Сформируй официальный отчёт председателю ТСН.\n\n"
        f"Всего пользователей: {total_users}\n"
        f"Общая задолженность: {total_debt} ₽\n"
        f"Заблокировано пользователей: {blocked}\n\n"
        "Сделай выводы и рекомендации."
    )

    report = await gpt_text(prompt)

    for admin_id in ADMIN_IDS:
        await context.bot.send_message(
            admin_id,
            f"📊 МЕСЯЧНЫЙ ОТЧЁТ ТСН\n\n{report}"
        )


# ================= АДМИН ПАНЕЛЬ =================

async def admin_panel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        return

    text = (
        "🛠 АДМИН ПАНЕЛЬ\n\n"
        f"👥 Пользователей: {len(USERS)}\n"
        f"🚫 Заблокировано: {len(BLOCKED)}\n"
        f"💸 Общая задолженность: {sum(u['debt'] for u in USERS.values())} ₽\n\n"
        "/gpt_report — GPT отчёт\n"
        "/users — список пользователей"
    )

    await update.message.reply_text(text)


async def users_list(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        return

    lines = []
    for uid, u in USERS.items():
        lines.append(
            f"{u['fio']} | @{u['username']} | {u['debt']} ₽ | "
            f"{'🚫' if u['blocked'] else '✅'}"
        )

    text = "👥 Пользователи:\n\n" + "\n".join(lines)
    await update.message.reply_text(text[:4000])


# ================= ЗАПУСК =================

def main():
    application = Application.builder().token(BOT_TOKEN).build()

    # команды
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("debt", debt))

    application.add_handler(CommandHandler("admin", admin_panel))
    application.add_handler(CommandHandler("users", users_list))
    application.add_handler(CommandHandler("gpt_report", gpt_admin))

    # фото чеков
    application.add_handler(
        MessageHandler(filters.PHOTO, handle_photo)
    )

    # JOB QUEUE
    jq = application.job_queue

    jq.run_daily(
        remind_debt,
        time=datetime.strptime("10:00", "%H:%M").time(),
        name="debt_reminder"
    )

    jq.run_monthly(
        monthly_report,
        when=1,
        time=datetime.strptime("12:00", "%H:%M").time(),
        name="monthly_report"
    )

    application.run_polling()


if __name__ == "__main__":
    main()
