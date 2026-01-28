import os
import json
import logging
import io
import requests
from datetime import datetime, time
import pytz

from telegram import Update, ReplyKeyboardMarkup
from telegram.ext import (
    Application, CommandHandler, MessageHandler,
    ContextTypes, filters
)

import gspread
from google.oauth2.service_account import Credentials
from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseUpload

# =====================================================
# 🔧 НАСТРОЙКИ
# =====================================================
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

TZ = pytz.timezone("Europe/Moscow")

BOT_TOKEN = os.getenv("BOT_TOKEN")
SPREADSHEET_ID = os.getenv("SPREADSHEET_ID")
DRIVE_FOLDER_ID = os.getenv("DRIVE_FOLDER_ID")
ADMIN_IDS = [int(x) for x in os.getenv("ADMIN_IDS", "").split(",") if x]

# =====================================================
# 📊 GOOGLE API
# =====================================================
creds_info = json.loads(os.getenv("GOOGLE_CREDENTIALS_JSON"))
creds = Credentials.from_service_account_info(
    creds_info,
    scopes=[
        "https://www.googleapis.com/auth/spreadsheets",
        "https://www.googleapis.com/auth/drive"
    ]
)

gc = gspread.authorize(creds)
drive = build("drive", "v3", credentials=creds)
sh = gc.open_by_key(SPREADSHEET_ID)

sheet_users = sh.worksheet("Лист 1")
sheet_checks = sh.worksheet("Лист 2")
sheet_logs = sh.worksheet("Лист 3")
sheet_reqs = sh.worksheet("Реквизиты")

# =====================================================
# 🧭 МЕНЮ
# =====================================================
USER_MENU = ReplyKeyboardMarkup(
    [["📎 Загрузить чек", "💳 Реквизиты"]],
    resize_keyboard=True
)

ADMIN_MENU = ReplyKeyboardMarkup(
    [["🛠 Админ-панель"], ["📎 Загрузить чек", "💳 Реквизиты"]],
    resize_keyboard=True
)

ADMIN_PANEL = ReplyKeyboardMarkup(
    [
        ["🔍 Долг по участку"],
        ["📣 Уведомление"],
        ["📊 Статистика"],
        ["⬅️ Назад"]
    ],
    resize_keyboard=True
)

# =====================================================
# 🛠 ВСПОМОГАТЕЛЬНЫЕ
# =====================================================
def is_admin(uid):
    return uid in ADMIN_IDS

def log_event(event, uid="", username="", house="", details="", error=""):
    try:
        sheet_logs.append_row([
            datetime.now(TZ).strftime("%Y-%m-%d %H:%M:%S"),
            event, uid, username, house, details, error
        ])
    except Exception as e:
        logger.warning(f"LOG ERROR: {e}")

def find_user(uid):
    for r in sheet_users.get_all_records():
        if str(r.get("Telegram_ID")) == str(uid):
            return r
    return None

def find_user_row(uid):
    ids = sheet_users.col_values(3)
    for i, v in enumerate(ids, start=2):
        if v == str(uid):
            return i
    return None

def is_duplicate(file_uid):
    return file_uid in sheet_checks.col_values(13)

def upload_to_drive(data, name, mime):
    media = MediaIoBaseUpload(io.BytesIO(data), mimetype=mime)
    f = drive.files().create(
        body={"name": name, "parents": [DRIVE_FOLDER_ID]},
        media_body=media,
        fields="id"
    ).execute()
    return f"https://drive.google.com/uc?id={f['id']}"

def download_qr_as_bytes(url):
    r = requests.get(url, timeout=20)
    r.raise_for_status()
    return r.content

# =====================================================
# 🚀 START
# =====================================================
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    user = find_user(uid)

    fio = user.get("ФИО") if user else None
    greeting = (
        f"Здравствуйте, *{fio}*!\n\n"
        if fio else "Здравствуйте!\n\n"
    )

    text = (
        greeting +
        "Вы находитесь в *официальном боте ТСН «Искона-Парк»*.\n\n"
        "ℹ️ Здесь вы можете:\n"
        "• получать уведомления о *поселковых взносах*\n"
        "• загрузить чек об оплате\n"
        "• посмотреть реквизиты для оплаты\n\n"
        "Пожалуйста, используйте кнопки ниже 👇"
    )

    await update.message.reply_text(
        text,
        parse_mode="Markdown",
        reply_markup=ADMIN_MENU if is_admin(uid) else USER_MENU
    )

# =====================================================
# 📝 TEXT HANDLER
# =====================================================
async def text_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text
    uid = update.effective_user.id

    if text == "/start":
        await start(update, context)
        return

    if text == "🛠 Админ-панель" and is_admin(uid):
        await update.message.reply_text("Админ-панель:", reply_markup=ADMIN_PANEL)
        return

    if text == "⬅️ Назад":
        await update.message.reply_text(
            "Главное меню:",
            reply_markup=ADMIN_MENU if is_admin(uid) else USER_MENU
        )
        return

    if text == "💳 Реквизиты":
        r = sheet_reqs.row_values(2)
        await update.message.reply_text(
            f"💳 *Реквизиты для оплаты поселковых взносов*\n\n"
            f"🏦 Банк: {r[0]}\n"
            f"🔢 БИК: {r[1]}\n"
            f"💼 Счёт: {r[2]}\n"
            f"👤 Получатель: {r[3]}\n"
            f"🧾 ИНН: {r[4]}",
            parse_mode="Markdown"
        )
        if r[5]:
            await update.message.reply_photo(download_qr_as_bytes(r[5]))
        return

    if text == "📎 Загрузить чек":
        context.user_data["wait_check"] = True
        await update.message.reply_text(
            "📎 Отправьте *фото или PDF чека*.\n"
            "После загрузки вы получите подтверждение.",
            parse_mode="Markdown"
        )
        return

    if text == "📊 Статистика" and is_admin(uid):
        logs = sheet_logs.get_all_records()
        blocked = {r["Username"] for r in logs if r["Тип"] == "blocked"}
        await update.message.reply_text(
            f"📊 *Статистика*\n\n"
            f"👥 Пользователей: {len(sheet_users.get_all_records())}\n"
            f"⛔️ Заблокировали бота: {len(blocked)}\n\n"
            f"{', '.join(blocked) if blocked else '—'}",
            parse_mode="Markdown"
        )
        return

# =====================================================
# 📎 FILE HANDLER
# =====================================================
async def file_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.user_data.get("wait_check"):
        return

    msg = update.message
    file = msg.photo[-1] if msg.photo else msg.document

    if is_duplicate(file.file_unique_id):
        await msg.reply_text("⚠️ Этот чек уже был загружен.")
        context.user_data.clear()
        return

    tg_file = await file.get_file()
    data = await tg_file.download_as_bytearray()

    link = upload_to_drive(
        data,
        f"check_{update.effective_user.id}_{datetime.now().strftime('%Y%m%d_%H%M%S')}",
        file.mime_type
    )

    user = find_user(update.effective_user.id)

    sheet_checks.append_row([
        update.effective_user.id,
        update.effective_user.username or "",
        user.get("ФИО") if user else "",
        user.get("Участок") if user else "",
        user.get("Телефон") if user else "",
        link,
        "",
        datetime.now(TZ).strftime("%Y-%m-%d %H:%M:%S"),
        "", "", "", "",
        file.file_unique_id,
        "новый"
    ])

    context.user_data.clear()
    await msg.reply_text("✅ Чек успешно загружен.")

# =====================================================
# ⏰ УВЕДОМЛЕНИЯ 5 / 3 / 1
# =====================================================
async def monthly_notify(context: ContextTypes.DEFAULT_TYPE):
    today = datetime.now(TZ).day

    for r in sheet_users.get_all_records():
        try:
            pay_day = int(r.get("День_оплаты") or 0)
            debt = float(str(r.get("Сумма") or "0").replace(",", "."))

            if debt <= 0 or not r.get("Telegram_ID"):
                continue

            days_left = pay_day - today
            fio = r.get("ФИО")

            texts = {
                5: f"⏰ Уважаемый(ая) *{fio}*,\n\n"
                   f"Через 5 дней необходимо оплатить поселковые взносы.",
                3: f"⚠️ Уважаемый(ая) *{fio}*,\n\n"
                   f"До оплаты поселковых взносов осталось 3 дня.",
                1: f"❗️ Уважаемый(ая) *{fio}*,\n\n"
                   f"Завтра крайний срок оплаты поселковых взносов."
            }

            if days_left in texts:
                await context.bot.send_message(
                    int(r["Telegram_ID"]),
                    texts[days_left],
                    parse_mode="Markdown"
                )

        except Exception as e:
            log_event("blocked", r.get("Telegram_ID"), r.get("username"), error=str(e))

# =====================================================
# 🚦 MAIN
# =====================================================
def main():
    app = Application.builder().token(BOT_TOKEN).build()

    app.job_queue.run_daily(
        monthly_notify,
        time=time(hour=18, minute=0, tzinfo=TZ)
    )

    app.add_handler(CommandHandler("start", start))
    app.add_handler(MessageHandler(filters.PHOTO | filters.Document.ALL, file_handler))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, text_handler))

    app.run_webhook(
        listen="0.0.0.0",
        port=int(os.getenv("PORT", 10000)),
        webhook_url="https://tsn-telegram-bot.onrender.com"
    )

if __name__ == "__main__":
    main()
# =====================================================
# 📎 FILE HANDLER — ЗАГРУЗКА ЧЕКОВ
# =====================================================
async def file_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.user_data.get("wait_check"):
        return

    msg = update.message
    file = msg.photo[-1] if msg.photo else msg.document
    uid = update.effective_user.id

    # --- Проверка дубля ---
    if is_duplicate(file.file_unique_id):
        await msg.reply_text(
            "⚠️ Этот чек уже был загружен ранее.\n"
            "Если это ошибка — обратитесь к администратору."
        )
        log_event("duplicate_check", uid, update.effective_user.username)
        context.user_data.clear()
        return

    # --- Скачать файл ---
    tg_file = await file.get_file()
    data = await tg_file.download_as_bytearray()

    # --- Загрузка в Google Drive ---
    link = upload_to_drive(
        data,
        f"check_{uid}_{datetime.now().strftime('%Y%m%d_%H%M%S')}",
        file.mime_type
    )

    # --- Данные пользователя ---
    row = find_user_row(uid)
    fio = house = phone = ""
    if row:
        fio = sheet_users.cell(row, 2).value or ""
        house = sheet_users.cell(row, 1).value or ""
        phone = sheet_users.cell(row, 5).value or ""

    # --- GPT-анализ чека (OCR / логика) ---
    gpt_result = ask_gpt(
        "Ты бухгалтер ТСН. Проанализируй чек оплаты поселковых взносов.",
        "Чек загружен. Определи, корректен ли платёж."
    )

    status = "новый"
    if "ошибка" in gpt_result.lower():
        status = "отклонён"

    # --- Запись в Лист 2 ---
    sheet_checks.append_row([
        uid,
        update.effective_user.username or "",
        fio,
        house,
        phone,
        link,
        "",
        datetime.now(TZ).strftime("%Y-%m-%d %H:%M:%S"),
        "",
        "",
        gpt_result,
        "",
        file.file_unique_id,
        status
    ])

    log_event("check_uploaded", uid, update.effective_user.username, house)

    context.user_data.clear()
    await msg.reply_text(
        "✅ Чек принят и отправлен на проверку.\n"
        "ℹ️ После подтверждения долг будет закрыт.",
        reply_markup=ADMIN_MENU if is_admin(uid) else USER_MENU
    )

# =====================================================
# ⏰ УВЕДОМЛЕНИЯ ЗА 5 / 3 / 1 ДЕНЬ
# =====================================================
async def monthly_notify(context: ContextTypes.DEFAULT_TYPE):
    today = datetime.now(TZ).date()

    for r in sheet_users.get_all_records():
        try:
            pay_day = int(r.get("День_оплаты") or 0)
            if pay_day <= 0:
                continue

            debt = float(str(r.get("Сумма") or "0").replace(",", "."))
            if debt <= 0:
                continue

            delta = pay_day - today.day
            if delta not in (5, 3, 1):
                continue

            uid = int(r["Telegram_ID"])
            fio = r.get("ФИО")

            tone = {
                5: "ℹ️ Напоминаем",
                3: "⚠️ Обращаем внимание",
                1: "❗️Срочно"
            }[delta]

            await context.bot.send_message(
                uid,
                f"{tone}, {fio}!\n\n"
                f"У вас есть задолженность по поселковым взносам.\n"
                f"📅 До даты оплаты осталось {delta} дн.\n\n"
                "💳 Оплатите и загрузите чек в бота."
            )

            log_event("auto_notify", uid, r.get("username"), r.get("Участок"))

        except Exception as e:
            log_event(
                "blocked",
                r.get("Telegram_ID"),
                r.get("username"),
                r.get("Участок"),
                error=str(e)
            )

# =====================================================
# 📊 GPT-МЕСЯЧНЫЙ ОТЧЁТ ПРЕДСЕДАТЕЛЮ
# =====================================================
async def monthly_gpt_report(context: ContextTypes.DEFAULT_TYPE):
    users = sheet_users.get_all_records()
    checks = sheet_checks.get_all_records()

    summary = ask_gpt(
        "Ты председатель ТСН. Сформируй официальный отчёт.",
        f"""
Всего жителей: {len(users)}
Чеков загружено: {len(checks)}
Должников: {len([u for u in users if float(str(u.get("Сумма") or "0").replace(",", ".")) > 0])}
"""
    )

    for admin_id in ADMIN_IDS:
        await context.bot.send_message(
            admin_id,
            "📄 Месячный GPT-отчёт ТСН\n\n" + summary
        )

# =====================================================
# 🚦 MAIN
# =====================================================
def main():
    app = Application.builder().token(BOT_TOKEN).build()

    # --- Уведомления каждый день в 18:00 ---
    app.job_queue.run_daily(
        monthly_notify,
        time=time(hour=18, minute=0, tzinfo=TZ)
    )

    # --- GPT отчёт 1 числа ---
    app.job_queue.run_monthly(
        monthly_gpt_report,
        when=time(hour=10, minute=0, tzinfo=TZ),
        day=1
    )

    app.add_handler(CommandHandler("start", start))
    app.add_handler(MessageHandler(filters.PHOTO | filters.Document.ALL, file_handler))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, text_handler))

    app.run_webhook(
        listen="0.0.0.0",
        port=int(os.getenv("PORT", 10000)),
        webhook_url="https://tsn-telegram-bot.onrender.com"
    )

if __name__ == "__main__":
    main()
