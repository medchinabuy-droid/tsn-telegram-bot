import json
import datetime as dt
import gspread
from google.oauth2.service_account import Credentials
from config import GOOGLE_CREDENTIALS_JSON, SPREADSHEET_ID, GOOGLE_SCOPES

creds_info = json.loads(GOOGLE_CREDENTIALS_JSON)
creds = Credentials.from_service_account_info(creds_info, scopes=GOOGLE_SCOPES)
gc = gspread.authorize(creds)

sh = gc.open_by_key(SPREADSHEET_ID)
ws_main = sh.sheet1

REQUIRED_HEADERS = [
    "Участок", "ФИО", "Telegram_ID", "username", "Телефон",
    "День_оплаты", "Электро", "Сумма", "Дата", "Статус", "Роль",
    "Дата_напоминания", "Дата_рождения", "Дата_регистрации",
    "Последняя_оплата", "Комментарий_админа", "Активен"
]


def ensure_headers():
    headers = ws_main.row_values(1)
    updated = False

    for h in REQUIRED_HEADERS:
        if h not in headers:
            headers.append(h)
            updated = True

    if updated:
        ws_main.update("1:1", [headers])


# Вызываем при импорте
ensure_headers()


def get_all():
    return ws_main.get_all_records()


def find_by_plot(plot: str):
    plot = str(plot).strip()
    for r in get_all():
        if str(r.get("Участок", "")).strip() == plot:
            return r
    return None


def find_by_tg(user_id: int):
    uid = str(user_id)
    for r in get_all():
        if str(r.get("Telegram_ID", "")).strip() == uid:
            return r
    return None


def add_owner(plot: str, fio: str, tg_id: int, username: str, phone: str, birth: str):
    ws_main.append_row([
        plot,
        fio,
        str(tg_id),
        username,
        phone,
        "",                 # День_оплаты
        "",                 # Электро
        "",                 # Сумма
        "",                 # Дата
        "Не оплачено",      # Статус
        "user",             # Роль
        "",                 # Дата_напоминания
        birth,
        dt.date.today().isoformat(),
        "",                 # Последняя_оплата
        "",                 # Комментарий_админа
        "Да"                # Активен
    ])


def update_field(plot: str, field: str, value):
    ensure_headers()

    headers = ws_main.row_values(1)
    if field not in headers:
        raise ValueError(f"Колонка {field} не найдена в таблице")

    plot = str(plot).strip()
    cell = ws_main.find(plot)

    col = headers.index(field) + 1
    ws_main.update_cell(cell.row, col, value)
