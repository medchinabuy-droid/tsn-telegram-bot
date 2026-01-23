import os
import json
import logging
import io
from datetime import datetime, timedelta, time
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
# 🔐 GOOGLE
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
    [["🚀 Начать"], ["📎 Загрузить чек", "💳 Реквизиты"]],
    resize_keyboard=True
)

ADMIN_MENU = ReplyKeyboardMarkup(
    [["🚀 Начать"], ["🛠 Админ-панель"], ["📎 Загрузить чек", "💳 Реквизиты"]],
    resize_keyboard=True
)

ADMIN_PANEL = ReplyKeyboardMarkup(
    [["🔍 Долг по участку"], ["📣 Уведомление"], ["📊 Статистика"], ["⬅️ Назад"]],
    resize_keyboard=True
)

# =====================================================
# 🧩 ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ
# =====================================================
def is_admin(uid: int) -> bool:
    return uid in ADMIN_IDS


def log_event(event, uid="", username="", house="", details="", error=""):
    try:
        sheet_logs.append_row([
            datetime.now(TZ).strftime("%Y-%m-%d %H:%M:%S"),
            event,
            uid,
            username,
            house,
            details,
            error
        ])
    except Exception as e:
        logger.warning(f"LOG SKIPPED: {e}")


def upload_to_drive(data: bytes, name: str, mime: str) -> str:
    media = MediaIoBaseUpload(io.BytesIO(data), mimetype=mime, resumable=False)
    file = drive.files().create(
        body={"name": name, "parents": [DRIVE_FOLDER_ID]},
        media_body=media,
        fields="id"
    ).execute()
    return f"https://drive.google.com/uc?id={file['id']}"


def download_from_drive(url: str) -> bytes:
    file_id = url.split("id=")[-1]
    request = drive.files().get_media(fileId=file_id)
    fh = io.BytesIO()
    downloader = MediaIoBaseUpload(fh, mimetype="image/png")
    return fh.getvalue()


def is_duplicate(file_uid: str) -> bool:
    return file_uid in sheet_checks.col_values(13)

# =====================================================
# ▶️ START
# =====================================================
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.clear()
    uid = update.effective_user.id
    menu = ADMIN_MENU if is_admin(uid) else USER_MENU
    await update.message.reply_text(
        "👋 Добро пожаловать!\nВыберите действие:",
        reply_markup=menu
    )
# =====================================================
# 🔔 ТЕКСТЫ УВЕДОМЛЕНИЙ
# =====================================================
def notify_text(fio: str, level: str) -> str:
    base = (
        f"Уважаемый(ая) {fio}!\n\n"
        "Просим Вас оплатить поселковые сборы в ТСН «Искона-Парк».\n"
        "У Вас имеется задолженность.\n\n"
        "С уважением,\n"
        "Правление ТСН"
    )

    if level == "soft":
        return "⏳ Напоминание\n\n" + base
    if level == "medium":
        return "⚠️ Важно\n\n" + base
    if level == "hard":
        return "❗ Срочно\n\n" + base

    return base


# =====================================================
# ⏰ АВТОУВЕДОМЛЕНИЯ
# =====================================================
async def monthly_notify(context: ContextTypes.DEFAULT_TYPE):
    today = datetime.now(TZ).date()

    for r in sheet_users.get_all_records():
        try:
            uid = int(r.get("Telegram_ID"))
            pay_day = int(r.get("День_оплаты") or 0)
            debt = float(str(r.get("Сумма") or "0").replace(",", "."))
            fio = r.get("ФИО", "")

            if pay_day == 0 or debt <= 0:
                continue

            delta = pay_day - today.day

            if delta == 3:
                text = notify_text(fio, "soft")
            elif delta == 1:
                text = notify_text(fio, "medium")
            elif delta == 0:
                text = notify_text(fio, "hard")
            else:
                continue

            await context.bot.send_message(uid, text)
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
# 🧾 РЕКВИЗИТЫ + QR
# =====================================================
async def send_reqs(update: Update):
    r = sheet_reqs.row_values(2)

    await update.message.reply_text(
        f"💳 Реквизиты ТСН\n\n"
        f"🏦 Банк: {r[0]}\n"
        f"🔢 БИК: {r[1]}\n"
        f"💼 Счёт: {r[2]}\n"
        f"👤 Получатель: {r[3]}\n"
        f"🧾 ИНН: {r[4]}"
    )

    if len(r) > 5 and r[5]:
        try:
            await update.message.reply_photo(r[5], caption="📷 QR для оплаты")
        except Exception:
            await update.message.reply_text("⚠️ QR временно недоступен")


# =====================================================
# 📎 ЗАГРУЗКА ЧЕКА
# =====================================================
async def file_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.user_data.get("wait_check"):
        return

    file = update.message.photo[-1] if update.message.photo else update.message.document
    file_uid = file.file_unique_id

    if is_duplicate(file_uid):
        await update.message.reply_text(
            "❌ Такой чек уже был загружен ранее.\n"
            "Если это ошибка — обратитесь в правление."
        )
        return

    tg_file = await file.get_file()
    data = await tg_file.download_as_bytearray()

    link = upload_to_drive(
        data,
        f"check_{update.effective_user.id}_{datetime.now().strftime('%Y%m%d_%H%M%S')}",
        file.mime_type
    )

    sheet_checks.append_row([
        update.effective_user.id,
        update.effective_user.username,
        "",
        "",
        "",
        link,
        "",
        datetime.now(TZ).strftime("%Y-%m-%d %H:%M:%S"),
        "",
        "",
        "",
        "НЕТ",
        file_uid,
        "новый"
    ])

    context.user_data.clear()
    await update.message.reply_text(
        "✅ Чек успешно получен и сохранён.\n"
        "Благодарим за оплату!"
    )


# =====================================================
# 💬 ТЕКСТОВЫЕ КОМАНДЫ
# =====================================================
async def text_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()
    uid = update.effective_user.id

    if text == "🚀 Начать":
        await start(update, context)
        return

    if text == "💳 Реквизиты":
        await send_reqs(update)
        return

    if text == "📎 Загрузить чек":
        context.user_data["wait_check"] = True
        await update.message.reply_text(
            "📎 Отправьте фото или PDF чека.\n"
            "После загрузки вы получите подтверждение."
        )
        return

    if text == "🛠 Админ-панель" and is_admin(uid):
        await update.message.reply_text("🛠 Админ-панель", reply_markup=ADMIN_PANEL)
        return

    if text == "⬅️ Назад":
        await update.message.reply_text("Меню", reply_markup=ADMIN_MENU)
        return

    # -------- ДОЛГ ПО УЧАСТКУ --------
    if text == "🔍 Долг по участку" and is_admin(uid):
        context.user_data["wait_house"] = True
        await update.message.reply_text("Введите номер участка:")
        return

    if context.user_data.get("wait_house"):
        context.user_data.clear()
        for r in sheet_users.get_all_records():
            if str(r.get("Участок")) == text:
                await update.message.reply_text(
                    f"🏡 Участок №{text}\n\n"
                    f"👤 ФИО: {r.get('ФИО')}\n"
                    f"📞 Телефон: {r.get('Телефон')}\n"
                    f"💰 Долг: {r.get('Сумма')} ₽\n"
                    f"📌 Статус: {r.get('Статус')}\n"
                    f"🤖 Username: @{r.get('username')}",
                    reply_markup=ADMIN_PANEL
                )
                return
        await update.message.reply_text("❌ Участок не найден", reply_markup=ADMIN_PANEL)

    # -------- СТАТИСТИКА --------
    if text == "📊 Статистика" and is_admin(uid):
        users = sheet_users.get_all_records()
        checks = sheet_checks.get_all_records()

        debt_sum = sum(
            float(str(u.get("Сумма") or "0").replace(",", "."))
            for u in users
            if str(u.get("Статус")).upper() != "ОПЛАЧЕНО"
        )

        await update.message.reply_text(
            "📊 Статистика ТСН\n\n"
            f"🏘 Всего участков: {len(users)}\n"
            f"💸 Должников: {len([u for u in users if str(u.get('Статус')).upper() != 'ОПЛАЧЕНО'])}\n"
            f"💰 Общий долг: {debt_sum:.2f} ₽\n"
            f"📎 Загружено чеков: {len(checks)}",
            reply_markup=ADMIN_PANEL
        )


# =====================================================
# ▶️ MAIN
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
