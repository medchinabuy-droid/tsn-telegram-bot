from telegram import Update, InputFile
from telegram.ext import ContextTypes
from keyboards import owner_menu, admin_menu
from services.google_sheets import get_all_rows, update_owner_field
# и т.д – вся логика start, callbacks, photo_handler
