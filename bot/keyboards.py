from telegram import InlineKeyboardMarkup, InlineKeyboardButton


def owner_menu(is_admin: bool = False):
    rows = [
        [InlineKeyboardButton("📊 Мой статус", callback_data="status")],
        [InlineKeyboardButton("💳 Оплатить", callback_data="pay")],
        [InlineKeyboardButton("👉 Я оплатил", callback_data="paid")],
        [InlineKeyboardButton("📄 Реквизиты", callback_data="reqs")],
        [InlineKeyboardButton("ℹ️ Мои данные", callback_data="me")],
    ]

    if is_admin:
        rows.append([InlineKeyboardButton("🛠 Админ-панель", callback_data="admin")])

    return InlineKeyboardMarkup(rows)


def payment_menu():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("⚡ СБП (QR)", callback_data="pay_sbp")],
        [InlineKeyboardButton("🏦 ВТБ", callback_data="pay_vtb")],
        [InlineKeyboardButton("🅰️ Альфа-Банк", callback_data="pay_alfa")],
        [InlineKeyboardButton("🟡 Т-Банк", callback_data="pay_tbank")],
        [InlineKeyboardButton("⬅️ Назад", callback_data="back_menu")],
    ])


def admin_menu():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("📣 Массовые уведомления", callback_data="mass_notify")],
        [InlineKeyboardButton("🗺 Карта посёлка", callback_data="map")],
        [InlineKeyboardButton("📊 Веб-дашборд", url="/admin")],
        [InlineKeyboardButton("⬅️ В меню", callback_data="back_menu")],
    ])
