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

def ensure_headers():
    headers = ws_main.row_values(1)
    required = [
        "Участок", "ФИО", "Telegram_ID", "username", "Телефон", "День_оплаты",
        "Электро", "Сумма", "Дата", "Статус", "Роль", "Дата_напоминания",
        "Дата_рождения", "Дата_регистрации", "Последняя_оплата",
        "Комментарий_админа", "Активен"
    ]
    for h in required:
        if h not in headers:
            headers.append(h)
    ws_main.update("1:1", [headers])

def get_all():
    return ws_main.get_all_records()

def find_by_plot(plot: str):
    for r in get_all():
        if str(r.get("Участок")) == str(plot):
            return r
    return None

def find_by_tg(user_id: int):
    for r in get_all():
        if str(r.get("Telegram_ID")) == str(user_id):
            return r
    return None

def add_owner(plot: str, fio: str, tg_id: int, username: str, phone: str, birth: str):
    ws_main.append_row([
        plot, fio, tg_id, username, phone, "", "", "", "",
        "Не оплачено", "user", "", birth,
        dt.date.today().isoformat(), "", "", True
    ])

def update_field(plot: str, field: str, value):
    cell = ws_main.find(str(plot))
    headers = ws_main.row_values(1)
    col = headers.index(field) + 1
    ws_main.update_cell(cell.row, col, value)
