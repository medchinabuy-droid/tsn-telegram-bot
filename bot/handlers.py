from telegram import Update, InlineKeyboardMarkup, InlineKeyboardButton
from telegram.ext import (
    ContextTypes,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    filters
)

from services.google_sheets import (
    find_by_plot,
    find_by_tg,
    add_owner,
    update_field,
)

from services.payments import make_spb_qr, bank_deeplink
from services.ocr import ocr_text, gpt_parse_receipt

# ====== КЛАВИАТУРЫ ======

def owner_menu():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("💳 Оплатить", callback_data="pay")],
        [InlineKeyboardButton("📊 Статус", callback_data="status")],
        [InlineKeyboardButton("📄 Реквизиты", callback_data="reqs")],
        [InlineKeyboardButton("👉 Я оплатил", callback_data="paid")],
    ])

def payment_menu():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("⚡ СБП", callback_data="pay_sbp")],
        [InlineKeyboardButton("🏦 ВТБ", callback_data="pay_vtb")],
        [InlineKeyboardButton("🅰️ Альфа", callback_data="pay_alfa")],
        [InlineKeyboardButton("🟡 Т-Банк", callback_data="pay_tbank")],
        [InlineKeyboardButton("⬅️ Назад", callback_data="back")]
    ])

# ====== /start ======

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    owner = find_by_tg(user.id)

    if owner:
        await update.message.reply_text(
            f"С возвращением, {owner.get('ФИО')} 👋",
            reply_markup=owner_menu()
        )
        return

    context.user_data.clear()
    context.user_data["await_plot"] = True
    await update.message.reply_text("Введите номер участка:")

# ====== CALLBACK ======

async def callbacks(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()

    if q.data == "pay":
        await q.message.reply_text("Выберите банк:", reply_markup=payment_menu())
        return

    if q.data.startswith("pay_"):
        bank = q.data.replace("pay_", "")
        context.user_data["await_amount"] = True
        context.user_data["bank"] = bank
        await q.message.reply_text(f"Введите сумму для оплаты через {bank.upper()}:")
        return

    if q.data == "paid":
        context.user_data["await_receipt"] = True
        await q.message.reply_text("Пришлите фото чека 📸")
        return

# ====== ТЕКСТ ======

async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()

    if context.user_data.get("await_amount"):
        try:
            amount = float(text.replace(",", "."))
        except:
            await update.message.reply_text("Введите корректную сумму")
            return

        bank = context.user_data["bank"]
        context.user_data.clear()

        if bank == "sbp":
            qr_bytes = make_spb_qr(amount)
            await update.message.reply_photo(qr_bytes, caption=f"QR для оплаты {amount} ₽ через СБП")
        else:
            link = bank_deeplink(bank, amount)
            await update.message.reply_text(f"Ссылка для оплаты:\n{link}")

        return

    await update.message.reply_text("Нажмите /start")

# ====== ФОТО ЧЕКА ======

async def handle_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.user_data.get("await_receipt"):
        await update.message.reply_text("Сначала нажмите 👉 Я оплатил")
        return

    file = await update.message.photo[-1].get_file()
    image_bytes = await file.download_as_bytearray()

    text = ocr_text(bytes(image_bytes))
    parsed = await gpt_parse_receipt(text)

    context.user_data.clear()

    await update.message.reply_text(
        f"🧾 Чек распознан:\n"
        f"💰 Сумма: {parsed.get('amount')}\n"
        f"📅 Дата: {parsed.get('date')}\n"
        f"🏦 Банк: {parsed.get('bank')}\n"
        f"🤖 Уверенность: {parsed.get('confidence')}"
    )

# ====== РЕГИСТРАЦИЯ ======

def register_handlers(app):
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CallbackQueryHandler(callbacks))
    app.add_handler(MessageHandler(filters.PHOTO, handle_photo))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))
