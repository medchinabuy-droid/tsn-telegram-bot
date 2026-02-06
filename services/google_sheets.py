import gspread
from google.oauth2.service_account import Credentials
from config import GOOGLE_CREDENTIALS_JSON, SPREADSHEET_ID

scopes = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive",
]

creds = Credentials.from_service_account_info(GOOGLE_CREDENTIALS_JSON, scopes=scopes)
gc = gspread.authorize(creds)
sh = gc.open_by_key(SPREADSHEET_ID)

ws_main = sh.sheet1
ws_req = sh.worksheet("Реквизиты")

def get_all_rows():
    return ws_main.get_all_records()

def update_owner_field(plot, field, value):
    cell = ws_main.find(str(plot))
    col = ws_main.find(field).col
    ws_main.update_cell(cell.row, col, value)

def get_requisites():
    return {r["Ключ"]: r["Значение"] for r in ws_req.get_all_records()}
