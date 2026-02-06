from telegram import InlineKeyboardMarkup, InlineKeyboardButton

def owner_menu():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("📊 Мой статус", callback_data="status")],
        [InlineKeyboardButton("💳 Оплатить", callback_data="pay")],
        [InlineKeyboardButton("👉 Я оплатил", callback_data="paid")],
        [InlineKeyboardButton("📄 Реквизиты", callback_data="reqs")],
    ])

def admin_menu():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("📣 Массовые уведомления", callback_data="mass_notify")],
        [InlineKeyboardButton("🗺 Карта посёлка", callback_data="map")],
        [InlineKeyboardButton("📊 Веб-дашборд", url="/admin")],
    ])
