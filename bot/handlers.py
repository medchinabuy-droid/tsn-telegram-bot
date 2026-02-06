from telegram import Update, InlineKeyboardMarkup, InlineKeyboardButton
from telegram.ext import ContextTypes
from services.google_sheets import find_by_plot, find_by_tg, add_owner, update_field

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Введите номер участка:")

async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    plot = update.message.text.strip()

    owner = find_by_plot(plot)
    if not owner:
        context.user_data["reg_plot"] = plot
        await update.message.reply_text("❌ Участок не найден. Введите ФИО для регистрации:")
        context.user_data["await_fio"] = True
        return

    update_field(plot, "Telegram_ID", user.id)
    update_field(plot, "username", user.username or "")
    await update.message.reply_text(f"Участок {plot} привязан к вашему Telegram ✅")

async def reg_flow(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if context.user_data.get("await_fio"):
        context.user_data["fio"] = update.message.text
        context.user_data["await_phone"] = True
        context.user_data["await_fio"] = False
        await update.message.reply_text("Введите телефон:")
        return

    if context.user_data.get("await_phone"):
        context.user_data["phone"] = update.message.text
        context.user_data["await_birth"] = True
        context.user_data["await_phone"] = False
        await update.message.reply_text("Введите дату рождения (YYYY-MM-DD):")
        return

    if context.user_data.get("await_birth"):
        add_owner(
            plot=context.user_data["reg_plot"],
            fio=context.user_data["fio"],
            tg_id=update.effective_user.id,
            username=update.effective_user.username or "",
            phone=context.user_data["phone"],
            birth=update.message.text
        )
        context.user_data.clear()
        await update.message.reply_text("✅ Регистрация завершена. Добро пожаловать!")
