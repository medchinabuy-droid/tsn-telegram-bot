from telegram import Update, InlineKeyboardMarkup, InlineKeyboardButton
from telegram.ext import ContextTypes, CommandHandler, MessageHandler, filters

from services.google_sheets import (
    find_by_plot,
    find_by_tg,
    add_owner,
    update_field
)

def owner_menu():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("💳 Оплатить", callback_data="pay")],
        [InlineKeyboardButton("📊 Статус", callback_data="status")],
        [InlineKeyboardButton("📄 Реквизиты", callback_data="reqs")],
        [InlineKeyboardButton("🧾 Загрузить чек", callback_data="receipt")]
    ])

# ====== /start ======

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user

    owner = find_by_tg(user.id)
    if owner:
        await update.message.reply_text(
            f"С возвращением, {owner.get('ФИО')} 👋\nВыберите действие:",
            reply_markup=owner_menu()
        )
        return

    context.user_data.clear()
    context.user_data["await_plot"] = True
    await update.message.reply_text("Введите номер участка для привязки:")

# ====== ТЕКСТОВЫЕ СООБЩЕНИЯ ======

async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    text = update.message.text.strip()

    # 1. Ждём номер участка
    if context.user_data.get("await_plot"):
        plot = text
        owner = find_by_plot(plot)

        if not owner:
            context.user_data["reg_plot"] = plot
            context.user_data["await_plot"] = False
            context.user_data["await_fio"] = True
            await update.message.reply_text("❌ Участок не найден.\nВведите ФИО для регистрации:")
            return

        # Участок найден — привязываем Telegram
        update_field(plot, "Telegram_ID", user.id)
        update_field(plot, "username", user.username or "")

        # Проверяем недостающие поля
        missing = []
        if not owner.get("Телефон"):
            missing.append("phone")
        if not owner.get("Дата_рождения"):
            missing.append("birth")

        context.user_data.clear()

        if missing:
            context.user_data["plot"] = plot
            context.user_data["missing"] = missing

            if "phone" in missing:
                context.user_data["await_phone"] = True
                await update.message.reply_text("Укажите номер телефона:")
                return

        await update.message.reply_text(
            f"✅ Участок {plot} привязан к вашему Telegram.",
            reply_markup=owner_menu()
        )
        return

    # 2. Регистрация: ФИО
    if context.user_data.get("await_fio"):
        context.user_data["fio"] = text
        context.user_data["await_fio"] = False
        context.user_data["await_phone"] = True
        await update.message.reply_text("Введите номер телефона:")
        return

    # 3. Телефон
    if context.user_data.get("await_phone"):
        phone = text
        context.user_data["phone"] = phone
        context.user_data["await_phone"] = False

        # если это дополнение данных
        if "missing" in context.user_data:
            plot = context.user_data["plot"]
            update_field(plot, "Телефон", phone)

            if "birth" in context.user_data["missing"]:
                context.user_data["await_birth"] = True
                await update.message.reply_text("Введите дату рождения (ГГГГ-ММ-ДД):")
                return

            context.user_data.clear()
            await update.message.reply_text("✅ Данные обновлены.", reply_markup=owner_menu())
            return

        # если это регистрация
        context.user_data["await_birth"] = True
        await update.message.reply_text("Введите дату рождения (ГГГГ-ММ-ДД):")
        return

    # 4. Дата рождения
    if context.user_data.get("await_birth"):
        birth = text

        plot = context.user_data.get("reg_plot")
        fio = context.user_data.get("fio")
        phone = context.user_data.get("phone")

        if plot and fio and phone:
            add_owner(
                plot=plot,
                fio=fio,
                tg_id=user.id,
                username=user.username or "",
                phone=phone,
                birth=birth
            )
            context.user_data.clear()
            await update.message.reply_text("✅ Регистрация завершена. Добро пожаловать!", reply_markup=owner_menu())
            return

        # если это дополнение
        plot = context.user_data.get("plot")
        update_field(plot, "Дата_рождения", birth)
        context.user_data.clear()
        await update.message.reply_text("✅ Данные обновлены.", reply_markup=owner_menu())
        return

    # 5. Если пользователь пишет что-то не в сценарии
    await update.message.reply_text("Не понял сообщение 🤔\nНажмите /start чтобы начать.")

# ====== РЕГИСТРАЦИЯ ХЕНДЛЕРОВ ======

def register_handlers(app):
    app.add_handler(CommandHandler("start", start))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))
