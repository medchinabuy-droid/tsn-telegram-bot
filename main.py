import os
import json
import logging
import io
import requests
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
# 🔧 НАСТРОЙКИ И ЛОГИ
# =====================================================
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

TZ = pytz.timezone("Europe/Moscow")

BOT_TOKEN = os.getenv("BOT_TOKEN")
SPREADSHEET_ID = os.getenv("SPREADSHEET_ID")
DRIVE_FOLDER_ID = os.getenv("DRIVE_FOLDER_ID")
ADMIN_IDS = [int(x) for x in os.getenv("ADMIN_IDS", "").split(",") if x]

# =====================================================
# 📊 GOOGLE SHEETS / DRIVE
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
    [
        ["🚀 Начать"],
        ["📎 Загрузить чек", "💳 Реквизиты"]
    ],
    resize_keyboard=True
)

ADMIN_MENU = ReplyKeyboardMarkup(
    [
        ["🚀 Начать"],
        ["🛠 Админ-панель"],
        ["📎 Загрузить чек", "💳 Реквизиты"]
    ],
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
# 🛠 ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ
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


def find_user_row(uid):
    ids = sheet_users.col_values(3)
    for i, v in enumerate(ids, start=2):
        if v == str(uid):
            return i
    return None


def is_duplicate(file_uid):
    return file_uid in sheet_checks.col_values(13)


def upload_to_drive(data: bytes, name: str, mime: str) -> str:
    media = MediaIoBaseUpload(io.BytesIO(data), mimetype=mime, resumable=False)
    f = drive.files().create(
        body={"name": name, "parents": [DRIVE_FOLDER_ID]},
        media_body=media,
        fields="id"
    ).execute()
    return f"https://drive.google.com/uc?id={f['id']}"


def download_qr_as_bytes(url: str) -> bytes:
    r = requests.get(url, timeout=20)
    r.raise_for_status()
    return r.content


# =====================================================
# 🚀 START
# =====================================================
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.clear()
    uid = update.effective_user.id

    menu = ADMIN_MENU if is_admin(uid) else USER_MENU

    await update.message.reply_text(
        "👋 *Добро пожаловать!*\n\n"
        "Вы находитесь в официальном боте ТСН «Искона-Парк».\n\n"
        "ℹ️ Здесь вы можете:\n"
        "• получать уведомления о поселковых взносах\n"
        "• загрузить чек об оплате\n"
        "• посмотреть реквизиты для оплаты\n\n"
        "Пожалуйста, используйте кнопки ниже 👇",
        reply_markup=menu,
        parse_mode="Markdown"
    )


# =====================================================
# 📝 TEXT HANDLER (ВСЯ ЛОГИКА КНОПОК)
# =====================================================
async def text_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()
    uid = update.effective_user.id

    # ---------- START ----------
    if text == "🚀 Начать":
        await start(update, context)
        return

    # ---------- АДМИН-ПАНЕЛЬ ----------
    if text == "🛠 Админ-панель" and is_admin(uid):
        await update.message.reply_text(
            "🛠 *Административная панель*\n\n"
            "Выберите нужное действие:",
            reply_markup=ADMIN_PANEL,
            parse_mode="Markdown"
        )
        return

    if text == "⬅️ Назад":
        await update.message.reply_text(
            "⬇️ Возврат в главное меню",
            reply_markup=ADMIN_MENU if is_admin(uid) else USER_MENU
        )
        return

    # ---------- ДОЛГ ПО УЧАСТКУ ----------
    if text == "🔍 Долг по участку" and is_admin(uid):
        context.user_data["wait_house"] = True
        await update.message.reply_text(
            "🏠 *Введите номер участка*\n\n"
            "Я покажу:\n"
            "• сумму долга\n"
            "• телефон\n"
            "• username\n"
            "• статус бота",
            parse_mode="Markdown"
        )
        return

    if context.user_data.get("wait_house") and is_admin(uid):
        context.user_data.pop("wait_house")

        for r in sheet_users.get_all_records():
            if str(r.get("Участок")) == text:
                status_bot = "❌ Заблокировал бота" if not r.get("Telegram_ID") else "✅ Бот активен"

                await update.message.reply_text(
                    f"🏠 *Участок:* {text}\n"
                    f"👤 *ФИО:* {r.get('ФИО')}\n"
                    f"📱 *Телефон:* {r.get('Телефон')}\n"
                    f"💰 *Задолженность:* {r.get('Сумма')} ₽\n"
                    f"📎 *Username:* @{r.get('username')}\n"
                    f"🤖 *Статус:* {status_bot}",
                    parse_mode="Markdown",
                    reply_markup=ADMIN_PANEL
                )
                return

        await update.message.reply_text(
            "❌ Участок не найден.",
            reply_markup=ADMIN_PANEL
        )
        return

    # ---------- РУЧНОЕ УВЕДОМЛЕНИЕ ----------
    if text == "📣 Уведомление" and is_admin(uid):
        context.user_data["notify_house"] = True
        await update.message.reply_text(
            "📣 *Введите номер участка*\n\n"
            "Уведомление будет отправлено ТОЛЬКО этому участку.",
            parse_mode="Markdown"
        )
        return

    if context.user_data.get("notify_house") and is_admin(uid):
        context.user_data.pop("notify_house")
        sent = False

        for r in sheet_users.get_all_records():
            if str(r.get("Участок")) == text and r.get("Telegram_ID"):
                try:
                    await context.bot.send_message(
                        int(r["Telegram_ID"]),
                        "📣 *Уведомление ТСН «Искона-Парк»*\n\n"
                        "Уважаемый(ая)!\n\n"
                        "Просим Вас обратить внимание на информацию от Правления ТСН.\n\n"
                        "С уважением,\n"
                        "Правление ТСН",
                        parse_mode="Markdown"
                    )
                    log_event("manual_notify", r["Telegram_ID"], r.get("username"), text)
                    sent = True
                except Exception as e:
                    log_event("blocked", r.get("Telegram_ID"), r.get("username"), text, error=str(e))

        await update.message.reply_text(
            "✅ Уведомление отправлено." if sent else "❌ Не удалось отправить уведомление.",
            reply_markup=ADMIN_PANEL
        )
        return
# =====================================================
# 📎 ЗАГРУЗКА ЧЕКОВ
# =====================================================
async def file_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.user_data.get("wait_check"):
        return

    msg = update.message
    file = msg.photo[-1] if msg.photo else msg.document

    # --- проверка на дубль ---
    if is_duplicate(file.file_unique_id):
        await msg.reply_text(
            "⚠️ *Этот чек уже был загружен ранее.*\n\n"
            "Если это ошибка — обратитесь к администратору.",
            parse_mode="Markdown"
        )
        log_event(
            "duplicate_check",
            update.effective_user.id,
            update.effective_user.username,
            details="Попытка загрузки дубля чека"
        )
        context.user_data.clear()
        return

    # --- скачивание ---
    tg_file = await file.get_file()
    data = await tg_file.download_as_bytearray()

    # --- загрузка в Drive ---
    link = upload_to_drive(
        data,
        f"check_{update.effective_user.id}_{datetime.now().strftime('%Y%m%d_%H%M%S')}",
        file.mime_type
    )

    uid = update.effective_user.id
    row = find_user_row(uid)

    fio = house = phone = ""
    if row:
        fio = sheet_users.cell(row, 2).value
        house = sheet_users.cell(row, 1).value
        phone = sheet_users.cell(row, 5).value

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
        "",
        "",
        file.file_unique_id,
        "новый"
    ])

    log_event(
        "check_uploaded",
        uid,
        update.effective_user.username,
        house,
        details="Чек загружен"
    )

    context.user_data.clear()
    await msg.reply_text(
        "✅ *Чек успешно принят!*\n\n"
        "После проверки администратором задолженность будет закрыта.",
        parse_mode="Markdown",
        reply_markup=ADMIN_MENU if is_admin(uid) else USER_MENU
    )


# =====================================================
# 💳 РЕКВИЗИТЫ
# =====================================================
async def send_reqs(update: Update):
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
        qr_bytes = download_qr_as_bytes(r[5])
        await update.message.reply_photo(qr_bytes)


# =====================================================
# ⏰ АВТО-УВЕДОМЛЕНИЯ (5 / 3 / 1 ДЕНЬ)
# =====================================================
async def monthly_notify(context: ContextTypes.DEFAULT_TYPE):
    today = datetime.now(TZ).date()

    for r in sheet_users.get_all_records():
        try:
            pay_day = int(r.get("День_оплаты") or 0)
            debt = float(str(r.get("Сумма") or "0").replace(",", "."))

            if pay_day <= 0 or debt <= 0 or not r.get("Telegram_ID"):
                continue

            days_left = pay_day - today.day

            if days_left == 5:
                text = (
                    "⏰ *Напоминание о поселковом взносе*\n\n"
                    "Через 5 дней наступает срок оплаты.\n"
                    "Пожалуйста, запланируйте оплату."
                )
            elif days_left == 3:
                text = (
                    "⚠️ *Важно!*\n\n"
                    "До даты оплаты поселкового взноса осталось 3 дня.\n"
                    "Просим произвести оплату своевременно."
                )
            elif days_left == 1:
                text = (
                    "❗️ *СРОЧНО*\n\n"
                    "Завтра крайний срок оплаты поселкового взноса.\n"
                    "Во избежание задолженности оплатите сегодня."
                )
            else:
                continue

            await context.bot.send_message(
                int(r["Telegram_ID"]),
                text,
                parse_mode="Markdown"
            )

            log_event(
                "auto_notify",
                r["Telegram_ID"],
                r.get("username"),
                r.get("Участок"),
                details=f"Уведомление за {days_left} день"
            )

        except Exception as e:
            log_event(
                "blocked",
                r.get("Telegram_ID"),
                r.get("username"),
                r.get("Участок"),
                error=str(e)
            )


# =====================================================
# 📊 СТАТИСТИКА
# =====================================================
async def send_stats(update: Update):
    users = sheet_users.get_all_records()
    total = len(users)

    blocked = []
    for r in sheet_logs.get_all_records():
        if r.get("Тип") == "blocked":
            blocked.append(str(r.get("Username")))

    await update.message.reply_text(
        "📊 *Статистика бота*\n\n"
        f"👥 Всего пользователей: {total}\n"
        f"⛔️ Заблокировали бота: {len(set(blocked))}\n\n"
        f"⛔️ Список: {', '.join(set(blocked)) if blocked else '—'}",
        parse_mode="Markdown"
    )


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
