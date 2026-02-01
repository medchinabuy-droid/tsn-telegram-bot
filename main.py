# ====== helpers ======
def log_event(event, user, details=""):
    SHEET_LOGS.append_row([
        datetime.now().strftime("%d.%m.%Y %H:%M:%S"),
        event,
        user.id if user else "",
        user.username if user else "",
        details
    ])

def main_keyboard(is_admin=False):
    kb = [["💳 Реквизиты", "📊 Статус"], ["ℹ️ Информация"]]
    if is_admin:
        kb.append(["🛠 Админ", "📈 Статистика"])
    return ReplyKeyboardMarkup(kb, resize_keyboard=True)

def parse_rekv():
    r = SHEET_REKV.get_all_records()[0]
    return (
        f"{r['Получатель']}\n"
        f"{r['ИНН']}\n"
        f"{r['Счёт получателя']}\n"
        f"{r['Банк']} {r['БИК']}\n"
        f"{r['Назначение платежа']}"
    )

def ocr_image(file_bytes):
    image = vision.Image(content=file_bytes)
    response = vision_client.text_detection(image=image)
    return response.full_text_annotation.text

def save_check(plot, filename, content):
    folder_metadata = {"name": f"Участок_{plot}", "mimeType": "application/vnd.google-apps.folder", "parents": [DRIVE_FOLDER_ID]}
    folder = drive_service.files().create(body=folder_metadata, fields="id").execute()
    media = MediaIoBaseUpload(BytesIO(content), mimetype="image/jpeg")
    file = drive_service.files().create(body={"name": filename, "parents": [folder["id"]]}, media_body=media, fields="webViewLink").execute()
    return file["webViewLink"]

def generate_pdf_report(stats):
    path = "/tmp/report.pdf"
    c = canvas.Canvas(path, pagesize=A4)
    c.drawString(50, 800, "Отчёт ТСН по задолженностям")
    y = 760
    for line in stats:
        c.drawString(50, y, line)
        y -= 20
    c.save()
    return path

# ====== handlers ======
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Бот ТСН ИСКОНА ПАРК запущен", reply_markup=main_keyboard(update.effective_user.id in ADMIN_IDS))

async def rekv(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = parse_rekv()
    qr = qrcode.make(text)
    bio = BytesIO()
    qr.save(bio, format="PNG")
    bio.seek(0)
    await update.message.reply_photo(bio, caption=text)

async def handle_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    file = await update.message.photo[-1].get_file()
    data = await file.download_as_bytearray()
    text = ocr_image(bytes(data))
    SHEET_CHECKS.append_row([update.effective_user.id, text, datetime.now().strftime("%d.%m.%Y")])
    await update.message.reply_text("📸 Чек принят и распознан. Платёж на проверке.")

async def text_router(update: Update, context: ContextTypes.DEFAULT_TYPE):
    t = update.message.text
    if t == "💳 Реквизиты":
        await rekv(update, context)
    elif t == "📊 Статус":
        await update.message.reply_text("Ваш статус получен.")
    elif t == "ℹ️ Информация":
        await update.message.reply_text("Информация по ТСН.")
    elif t == "📈 Статистика":
        stats = ["Всего участков: 100", "Должники: 23"]
        pdf = generate_pdf_report(stats)
        await update.message.reply_document(open(pdf, "rb"))
    else:
        await update.message.reply_text("Команда не распознана")

# ====== webhook ======
@app.post(f"/webhook/{WEBHOOK_SECRET}")
async def webhook(req: Request):
    data = await req.json()
    update = Update.de_json(data, application.bot)
    await application.process_update(update)
    return {"ok": True}

@app.on_event("startup")
async def startup():
    application.add_handler(CommandHandler("start", start))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, text_router))
    application.add_handler(MessageHandler(filters.PHOTO, handle_photo))

    await application.initialize()
    await application.start()
    await application.bot.set_webhook(f"{WEBHOOK_URL}/webhook/{WEBHOOK_SECRET}")
    logger.info("🚀 Бот запущен")

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=PORT)
