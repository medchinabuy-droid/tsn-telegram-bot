# ===============================
# IMPORTS
# ===============================
import os
import json
import logging
import datetime
import pytz
from telegram import (
    Update,
    ReplyKeyboardMarkup,
    InlineKeyboardMarkup,
    InlineKeyboardButton,
)
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    ContextTypes,
    filters,
)
from apscheduler.schedulers.asyncio import AsyncIOScheduler

# ===============================
# LOGGING
# ===============================
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# ===============================
# ENV
# ===============================
BOT_TOKEN = os.getenv("BOT_TOKEN")
ADMIN_IDS = list(map(int, os.getenv("ADMIN_IDS", "").split(",")))

MOSCOW_TZ = pytz.timezone("Europe/Moscow")

# ===============================
# MEMORY STORAGE (OK FOR START)
# ===============================
USERS = {}           # user_id -> {fio, plot}
CHECKS = {}          # check_id -> data
BLOCKED = set()
STATS = {
    "users": 0,
    "notifications": 0,
    "blocked": 0,
}

# ===============================
# KEYBOARDS
# ===============================
def user_keyboard(user_id: int):
    buttons = [
        ["💳 Реквизиты", "📎 Загрузить чек"],
        ["🔍 Долг по участку"],
    ]
    if user_id in ADMIN_IDS:
        buttons.append(["🛠 Админ-панель"])
    return ReplyKeyboardMarkup(buttons, resize_keyboard=True)

def admin_keyboard():
    return ReplyKeyboardMarkup(
        [
            ["📣 Уведомление"],
            ["📊 Статистика"],
        ],
        resize_keyboard=True,
    )

# ===============================
# START / REGISTRATION
# ===============================
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if user_id not in USERS:
        context.user_data["state"] = "wait_fio"
        await update.message.reply_text(
            "👋 Добро пожаловать!\nВведите ФИО:",
        )
    else:
        await update.message.reply_text(
            "🏡 Главное меню",
            reply_markup=user_keyboard(user_id),
        )

async def save_fio(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    fio = update.message.text.strip()
    USERS[user_id] = {"fio": fio}
    STATS["users"] = len(USERS)

    context.user_data.pop("state", None)

    await update.message.reply_text(
        f"✅ Спасибо, {fio}!\nВыберите действие:",
        reply_markup=user_keyboard(user_id),
    )

# ===============================
# STATIC COMMANDS
# ===============================
async def requisites(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "💳 Реквизиты:\n\n"
        "Банк: ВТБ\n"
        "Счёт: 4070 **** ****\n"
        "Получатель: ТСН ИСКОНА-ПАРК"
    )

async def ask_debt(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["state"] = "wait_plot_debt"
    await update.message.reply_text("Введите номер участка:")

async def show_debt(update: Update, context: ContextTypes.DEFAULT_TYPE):
    plot = update.message.text.strip()
    await update.message.reply_text(
        f"🔍 Участок {plot}\nЗадолженность: 0 ₽"
    )

# ===============================
# ADMIN PANEL
# ===============================
async def admin_panel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id not in ADMIN_IDS:
        return
    await update.message.reply_text(
        "🛠 Админ-панель",
        reply_markup=admin_keyboard(),
    )

# ===============================
# NOTIFICATIONS
# ===============================
async def notify_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id not in ADMIN_IDS:
        return
    context.user_data["state"] = "wait_notify_target"
    await update.message.reply_text(
        "📣 Уведомление\n\n"
        "Введите:\n"
        "ALL — всем\n"
        "SELF — себе\n"
        "или номер участка"
    )

async def notify_send(update: Update, context: ContextTypes.DEFAULT_TYPE):
    target = update.message.text.strip().upper()
    sent = 0

    for uid in USERS:
        if target == "ALL":
            await context.bot.send_message(uid, "📣 Уведомление от ТСН")
            sent += 1
        elif target == "SELF" and uid == update.effective_user.id:
            await context.bot.send_message(uid, "📣 Тестовое уведомление")
            sent += 1

    STATS["notifications"] += sent
    await update.message.reply_text(f"📨 Отправлено: {sent}")

# ===============================
# STATS
# ===============================
async def stats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "📊 Статистика\n\n"
        f"👥 Пользователей: {STATS['users']}\n"
        f"📨 Уведомлений: {STATS['notifications']}\n"
        f"⛔ Заблокировали: {len(BLOCKED)}"
    )

# ===============================
# TEXT ROUTER (IMPORTANT)
# ===============================
async def text_router(update: Update, context: ContextTypes.DEFAULT_TYPE):
    state = context.user_data.get("state")

    if state == "wait_fio":
        await save_fio(update, context)
        return

    if state == "wait_plot_debt":
        context.user_data.pop("state", None)
        await show_debt(update, context)
        return

    if state == "wait_notify_target":
        context.user_data.pop("state", None)
        await notify_send(update, context)
        return

    await update.message.reply_text(
        "❓ Используйте кнопки меню",
        reply_markup=user_keyboard(update.effective_user.id),
    )

# ===============================
# MAIN
# ===============================
def main():
    app = Application.builder().token(BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(MessageHandler(filters.Regex("^🚀 Начать$"), start))
    app.add_handler(MessageHandler(filters.Regex("^💳 Реквизиты$"), requisites))
    app.add_handler(MessageHandler(filters.Regex("^📎 Загрузить чек$"), requisites))
    app.add_handler(MessageHandler(filters.Regex("^🔍 Долг по участку$"), ask_debt))
    app.add_handler(MessageHandler(filters.Regex("^🛠 Админ-панель$"), admin_panel))
    app.add_handler(MessageHandler(filters.Regex("^📣 Уведомление$"), notify_start))
    app.add_handler(MessageHandler(filters.Regex("^📊 Статистика$"), stats))

    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, text_router))

    app.run_webhook(
        listen="0.0.0.0",
        port=int(os.getenv("PORT", 10000)),
        webhook_url=os.getenv("WEBHOOK_URL"),
    )

if __name__ == "__main__":
    main()
# ==========================================================
# ЧЕКИ / ЗАГРУЗКА ФАЙЛОВ
# ==========================================================

async def upload_check(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id

    if user_id not in USERS:
        await update.message.reply_text(
            "❗ Сначала нажмите 🚀 Начать и пройдите регистрацию"
        )
        return

    await update.message.reply_text(
        "📎 Отправьте фото или PDF чека\n"
        "После загрузки чек уйдёт на проверку админу"
    )
    context.user_data["state"] = "wait_check"


async def receive_check(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id

    if not update.message.document and not update.message.photo:
        await update.message.reply_text("❗ Отправьте именно файл или фото")
        return

    # уникальный id чека
    check_id = f"{user_id}_{int(datetime.datetime.now().timestamp())}"

    CHECKS[check_id] = {
        "user_id": user_id,
        "status": "pending",
        "date": datetime.datetime.now(MOSCOW_TZ),
    }

    # уведомление админу
    for admin in ADMIN_IDS:
        await context.bot.send_message(
            admin,
            f"🔐 Новый чек\n"
            f"От: {USERS[user_id]['fio']}\n"
            f"ID: {check_id}",
            reply_markup=InlineKeyboardMarkup([
                [
                    InlineKeyboardButton("✅ Подтвердить", callback_data=f"ok_{check_id}"),
                    InlineKeyboardButton("❌ Отклонить", callback_data=f"no_{check_id}")
                ]
            ])
        )

    context.user_data.pop("state", None)

    await update.message.reply_text(
        "✅ Чек получен\n"
        "Ожидайте подтверждения администратора"
    )


# ==========================================================
# INLINE-ПОДТВЕРЖДЕНИЕ ЧЕКОВ
# ==========================================================

async def check_decision(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    if query.from_user.id not in ADMIN_IDS:
        return

    action, check_id = query.data.split("_", 1)

    if check_id not in CHECKS:
        await query.edit_message_text("❌ Чек не найден")
        return

    check = CHECKS[check_id]
    user_id = check["user_id"]

    if action == "ok":
        check["status"] = "approved"
        await context.bot.send_message(
            user_id,
            "✅ Ваш чек подтверждён\n"
            "Долг автоматически закрыт"
        )
        await query.edit_message_text("✅ Чек подтверждён")
    else:
        check["status"] = "rejected"
        await context.bot.send_message(
            user_id,
            "❌ Чек отклонён\n"
            "Пожалуйста, загрузите корректный чек"
        )
        await query.edit_message_text("❌ Чек отклонён")


# ==========================================================
# АВТО-УВЕДОМЛЕНИЯ В 18:00 МСК
# ==========================================================

async def auto_notifications():
    text = (
        "⏰ Напоминание\n\n"
        "Просим проверить оплату и при необходимости "
        "загрузить чек через бота."
    )

    for uid in USERS:
        try:
            await app.bot.send_message(uid, text)
            STATS["notifications"] += 1
        except:
            BLOCKED.add(uid)


def schedule_notifications(scheduler):
    scheduler.add_job(
        auto_notifications,
        trigger="cron",
        hour=18,
        minute=0,
        timezone=MOSCOW_TZ,
    )


# ==========================================================
# АНТИ-СПАМ
# ==========================================================

LAST_MESSAGE = {}

async def anti_spam(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    now = datetime.datetime.now().timestamp()

    last = LAST_MESSAGE.get(user_id, 0)
    LAST_MESSAGE[user_id] = now

    if now - last < 1:
        await update.message.reply_text("⏳ Пожалуйста, не спамьте")
        return True
    return False


# ==========================================================
# ДОБАВЛЕНИЕ ХЕНДЛЕРОВ (ВАЖНО)
# ==========================================================

def add_part2_handlers(app: Application):
    app.add_handler(MessageHandler(filters.Regex("^📎 Загрузить чек$"), upload_check))
    app.add_handler(
        MessageHandler(
            (filters.Document.ALL | filters.PHOTO),
            receive_check
        )
    )
    app.add_handler(CallbackQueryHandler(check_decision))


# ==========================================================
# ПАТЧ В MAIN (НЕ УДАЛЯЙ СТАРОЕ)
# ==========================================================

# В функции main(), ПЕРЕД app.run_webhook(...)
# добавь ЭТИ СТРОКИ:

"""
    scheduler = AsyncIOScheduler()
    schedule_notifications(scheduler)
    scheduler.start()

    add_part2_handlers(app)
"""
