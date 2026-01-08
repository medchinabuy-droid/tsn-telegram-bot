import os
import json
import logging
from telegram import (
    Update,
    ReplyKeyboardMarkup,
    KeyboardButton,
)
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    ContextTypes,
    filters,
)
import gspread
from google.oauth2.service_account import Credentials

logging.basicConfig(level=logging.INFO)

# ---------- ENV ----------
BOT_TOKEN = os.getenv("BOT_TOKEN")
SPREADSHEET_ID = os.getenv("SPREADSHEET_ID")
ADMIN_IDS = list(map(int, os.getenv("ADMIN_IDS", "").split(",")))

# ---------- GOOGLE ----------
creds_dict = json.loads(os.getenv("GOOGLE_CREDENTIALS_JSON"))
creds = Credentials.from_service_account_info(
    creds_dict,
    scopes=["https://www.googleapis.com/auth/spreadsheets"],
)
gc = gspread.authorize(creds)
sh = gc.open_by_key(SPREADSHEET_ID)

sheet_users = sh.worksheet("Лист 1")
sheet_checks = sh.worksheet("Лист 2")
sheet_rekv = sh.worksheet("Реквизиты")

# ---------- UI ----------
MENU = ReplyKeyboardMarkup(
    [
        [KeyboardButton("📎 Загрузить чек")],
        [KeyboardButton("💳 Реквизиты")],
    ],
    resize_keyboard=True,
)

PHONE_HINT = "📱 Введите телефон в формате:\n+7XXXXXXXXXX"

# ---------- HELPERS ----------
def find_user_row(tg_id):
    col = sheet_users.col_values(3)
    for i, v in enumerate(col[1:], start=2):
        if str(v) == str(tg_id):
            return i
    return None


def user_data(row):
    headers = sheet_users.row_values(1)
    values = sheet_users.row_values(row)
    return dict(zip(headers, values))


# ---------- START ----------
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    row = find_user_row(user.id)

    if not row:
        context.user_data["step"] = "fio"
        await update.message.reply_text("👋 Добро пожаловать!\nВведите ФИО:")
        return

    data = user_data(row)

    if not data.get("Телефон"):
        context.user_data["step"] = "phone"
        await update.message.reply_text(PHONE_HINT)
        return

    if not data.get("Участок"):
        context.user_data["step"] = "house"
        await update.message.reply_text("🏠 Укажите номер дома:")
        return

    await update.message.reply_text(
        "✅ Данные найдены.\n\n"
        "ℹ️ Внизу есть меню.\n"
        "Чтобы открыть — нажмите на 📎 рядом с полем ввода 👇",
        reply_markup=MENU,
    )


# ---------- TEXT FLOW ----------
async def text_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()
    user = update.effective_user
    step = context.user_data.get("step")

    row = find_user_row(user.id)

    # --- REGISTRATION ---
    if step == "fio":
        context.user_data["fio"] = text
        context.user_data["step"] = "phone"
        await update.message.reply_text(PHONE_HINT)
        return

    if step == "phone":
        context.user_data["phone"] = text
        context.user_data["step"] = "house"
        await update.message.reply_text("🏠 Укажите номер дома:")
        return

    if step == "house":
        sheet_users.append_row([
            text,                         # Участок
            context.user_data["fio"],     # ФИО
            user.id,                      # Telegram_ID
            context.user_data["phone"],   # Телефон
        ])
        context.user_data.clear()
        await update.message.reply_text(
            "✅ Регистрация завершена!\n\n"
            "ℹ️ Внизу меню, нажмите 📎",
            reply_markup=MENU,
        )
        return

    # --- EXISTING USER FILL ---
    if row:
        data = user_data(row)

        if not data.get("Телефон"):
            sheet_users.update_cell(row, 4, text)
            await update.message.reply_text("✅ Телефон сохранён")
            return

        if not data.get("Участок"):
            sheet_users.update_cell(row, 1, text)
            await update.message.reply_text(
                "✅ Дом сохранён",
                reply_markup=MENU,
            )
            return

    # --- ADMIN ---
    if user.id in ADMIN_IDS and text.startswith("долг"):
        parts = text.split()
        if len(parts) != 2:
            return
        house = parts[1]
        for r in sheet_users.get_all_records():
            if str(r["Участок"]) == house:
                await update.message.reply_text(
                    f"🏠 Дом {house}\n"
                    f"💰 Сумма: {r.get('Сумма')}\n"
                    f"📅 Напоминание: {r.get('Дата_напоминания')}\n"
                    f"📌 Статус: {r.get('Статус')}"
                )
                return


# ---------- CHECK ----------
async def handle_check(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.message
    user = update.effective_user

    file = None
    if msg.photo:
        file = msg.photo[-1]
    elif msg.document:
        file = msg.document
    else:
        return

    unique_id = file.file_unique_id
    if unique_id in sheet_checks.col_values(11):
        await msg.reply_text("⚠️ Этот чек уже был загружен")
        return

    row = find_user_row(user.id)
    data = user_data(row)

    tg_file = await context.bot.get_file(file.file_id)

    sheet_checks.append_row([
        user.id,
        user.username,
        data.get("ФИО"),
        data.get("Участок"),
        data.get("Телефон"),
        tg_file.file_path,
        "",
        "",
        "",
        "нет",
        unique_id,
    ])

    await msg.reply_text("✅ Чек принят")


# ---------- REKV ----------
async def send_rekv(update: Update, context: ContextTypes.DEFAULT_TYPE):
    r = sheet_rekv.get_all_records()[0]

    await update.message.reply_text(
        f"🏦 Банк: {r['Банк']}\n"
        f"БИК: {r['БИК']}\n"
        f"Счёт: {r['Счёт получателя']}\n"
        f"Получатель: {r['Получатель']}\n"
        f"ИНН: {r['ИНН']}"
    )

    if r.get("QR_оплата"):
        await update.message.reply_photo(r["QR_оплата"])


# ---------- MAIN ----------
def main():
    app = Application.builder().token(BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(MessageHandler(filters.Regex("^📎 Загрузить чек$"), lambda u, c: u.message.reply_text("📎 Нажмите на скрепку и отправьте чек")))
    app.add_handler(MessageHandler(filters.Regex("^💳 Реквизиты$"), send_rekv))
    app.add_handler(MessageHandler(filters.PHOTO | filters.Document.ALL, handle_check))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, text_handler))

    app.run_webhook(
        listen="0.0.0.0",
        port=int(os.getenv("PORT", 10000)),
        webhook_url=os.getenv("RENDER_EXTERNAL_URL"),
    )


if __name__ == "__main__":
    main()
