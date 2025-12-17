import os
import logging
from datetime import datetime
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    ApplicationBuilder,
    ContextTypes,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    filters,
)

# ================= НАСТРОЙКИ =================
BOT_TOKEN = os.getenv("BOT_TOKEN")  # токен берётся из Render
MONTHLY_FEE = 6000

SPREADSHEET_ID = os.getenv("SPREADSHEET_ID")

# ================= ЛОГИ =================
logging.basicConfig(level=logging.INFO)

# ================= КНОПКИ =================
def main_menu():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🧾 Отправить платёжку", callback_data="send_payment")],
        [InlineKeyboardButton("📄 Реквизиты ТСН", callback_data="requisites")],
        [InlineKeyboardButton("📅 Моя дата оплаты", callback_data="my_date")],
    ])

# ================= /start =================
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "Здравствуйте! Это бот ТСН для оплаты взносов.",
        reply_markup=main_menu()
    )

# ================= КНОПКИ =================
async def buttons(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    if query.data == "send_payment":
        context.user_data["awaiting_payment"] = True
        await query.message.reply_text("Пожалуйста, отправьте PDF или фото платёжки")

    elif query.data == "requisites":
        await query.message.reply_text(
            "📄 Реквизиты ТСН:\n"
            "ИНН: XXXXXXXX\n"
            "Р/с: XXXXXXXXXXXXX\n"
            "Банк: XXXXX\n\n"
            "QR-код будет отправлен отдельно"
        )

    elif query.data == "my_date":
        await query.message.reply_text(
            "Ваша дата оплаты указана в реестре ТСН."
        )

# ================= ПРИЁМ ПЛАТЁЖКИ =================
async def handle_document(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.user_data.get("awaiting_payment"):
        return

    # Получаем файл
    if update.message.document:
        file = await update.message.document.get_file()
    else:
        file = await update.message.photo[-1].get_file()

    os.makedirs("payments", exist_ok=True)
    file_path = f"payments/{update.message.from_user.id}_{datetime.now().timestamp()}"
    await file.download_to_drive(file_path)

    # ====== ЗАГЛУШКА OCR (ПОТОМ СДЕЛАЕМ НАСТОЯЩИЙ) ======
    recognized_sum = 12000  # пример

    if recognized_sum > MONTHLY_FEE:
        months = recognized_sum // MONTHLY_FEE
        keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton(f"За {months} месяца", callback_data=f"months_{months}")],
            [InlineKeyboardButton("Указать вручную", callback_data="months_manual")]
        ])
        await update.message.reply_text(
            f"Обнаружена сумма {recognized_sum} ₽.\nЗа какие месяцы оплата?",
            reply_markup=keyboard
        )
    else:
        await update.message.reply_text(
            "Платёж принят и отправлен на сверку бухгалтеру."
        )

    context.user_data["awaiting_payment"] = False

# ================= МЕСЯЦЫ ====

