import os
import base64
import asyncpg
from datetime import datetime, time

from telegram import Update
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    ContextTypes,
    filters,
)

from openai import OpenAI
from reportlab.lib.pagesizes import A4
from reportlab.pdfgen import canvas
from openpyxl import Workbook

# ================= CONFIG =================

BOT_TOKEN = os.getenv("BOT_TOKEN")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
DATABASE_URL = os.getenv("DATABASE_URL")
WEBHOOK_URL = os.getenv("WEBHOOK_URL")
PORT = int(os.getenv("PORT", "10000"))

ADMIN_IDS = {123456789}
MONTHLY_FEE = 3000

client = OpenAI(api_key=OPENAI_API_KEY)
db_pool: asyncpg.Pool | None = None

# ================= DB =================

async def init_db():
    global db_pool
    db_pool = await asyncpg.create_pool(DATABASE_URL)

    async with db_pool.acquire() as con:
        await con.execute("""
        CREATE TABLE IF NOT EXISTS users (
            id BIGINT PRIMARY KEY,
            fio TEXT,
            username TEXT,
            debt INT,
            fails INT DEFAULT 0,
            blocked BOOLEAN DEFAULT FALSE
        );
        """)

        await con.execute("""
        CREATE TABLE IF NOT EXISTS payments (
            id SERIAL PRIMARY KEY,
            user_id BIGINT,
            amount INT,
            created_at TIMESTAMP DEFAULT NOW()
        );
        """)

# ================= GPT =================

async def gpt_text(prompt: str) -> str:
    r = client.chat.completions.create(
        model="gpt-4o-mini",
        messages=[{"role": "user", "content": prompt}],
    )
    return r.choices[0].message.content.strip()


async def gpt_vision(image: bytes) -> int:
    b64 = base64.b64encode(image).decode()
    r = client.chat.completions.create(
        model="gpt-4o",
        messages=[{
            "role": "user",
            "content": [
                {"type": "text", "text": "Найди сумму оплаты в чеке. Ответь ТОЛЬКО числом."},
                {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{b64}"}},
            ],
        }],
    )
    text = r.choices[0].message.content
    digits = "".join(filter(str.isdigit, text))
    return int(digits) if digits else 0

# ================= USERS =================

async def get_user(tg_user):
    async with db_pool.acquire() as con:
        user = await con.fetchrow("SELECT * FROM users WHERE id=$1", tg_user.id)
        if not user:
            await con.execute(
                "INSERT INTO users VALUES ($1,$2,$3,$4,0,FALSE)",
                tg_user.id,
                tg_user.full_name,
                tg_user.username or "",
                MONTHLY_FEE,
            )
            return await con.fetchrow("SELECT * FROM users WHERE id=$1", tg_user.id)
        return user

# ================= HANDLERS =================

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = await get_user(update.effective_user)
    if user["blocked"]:
        await update.message.reply_text("🚫 Вы заблокированы")
        return
    await update.message.reply_text("📸 Отправьте фото чека\n💰 /debt")

async def debt(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = await get_user(update.effective_user)
    await update.message.reply_text(f"💸 Задолженность: {user['debt']} ₽")

async def handle_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = await get_user(update.effective_user)
    if user["blocked"]:
        return

    photo = update.message.photo[-1]
    file = await photo.get_file()
    img = await file.download_as_bytearray()

    await update.message.reply_text("🔍 Проверка чека...")

    amount = await gpt_vision(bytes(img))

    async with db_pool.acquire() as con:
        if amount < MONTHLY_FEE:
            await con.execute(
                "UPDATE users SET fails=fails+1 WHERE id=$1",
                user["id"]
            )
            fails = user["fails"] + 1
            if fails >= 3:
                await con.execute("UPDATE users SET blocked=TRUE WHERE id=$1", user["id"])
                await update.message.reply_text("🚫 Заблокированы (3 отказа)")
            else:
                await update.message.reply_text("❌ Недостаточная сумма")
        else:
            await con.execute(
                "UPDATE users SET debt=GREATEST(debt-$1,0), fails=0 WHERE id=$2",
                amount, user["id"]
            )
            await con.execute(
                "INSERT INTO payments (user_id, amount) VALUES ($1,$2)",
                user["id"], amount
            )
            await update.message.reply_text("✅ Оплата принята")

# ================= ANALYTICS =================

async def forecast(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id not in ADMIN_IDS:
        return

    async with db_pool.acquire() as con:
        rows = await con.fetch("SELECT fio,debt FROM users")

    text = "\n".join(f"{r['fio']} — {r['debt']} ₽" for r in rows)
    report = await gpt_text("Проанализируй задолженности:\n" + text)
    await update.message.reply_text(report)

# ================= REPORTS =================

async def monthly_report(context: ContextTypes.DEFAULT_TYPE):
    async with db_pool.acquire() as con:
        rows = await con.fetch("""
        SELECT u.fio, p.amount, p.created_at
        FROM payments p JOIN users u ON u.id=p.user_id
        """)

    # PDF
    pdf_path = "/tmp/report.pdf"
    c = canvas.Canvas(pdf_path, pagesize=A4)
    y = 800
    for r in rows:
        c.drawString(40, y, f"{r['fio']} — {r['amount']} ₽ — {r['created_at']}")
        y -= 20
    c.save()

    # Excel
    xls_path = "/tmp/report.xlsx"
    wb = Workbook()
    ws = wb.active
    ws.append(["ФИО", "Сумма", "Дата"])
    for r in rows:
        ws.append([r["fio"], r["amount"], str(r["created_at"])])
    wb.save(xls_path)

# ================= MAIN =================

async def post_init(app: Application):
    await init_db()
    app.job_queue.run_daily(
        monthly_report,
        time=time(hour=9),
        name="monthly_report"
    )

def main():
    app = (
        Application.builder()
        .token(BOT_TOKEN)
        .post_init(post_init)
        .build()
    )

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("debt", debt))
    app.add_handler(CommandHandler("forecast", forecast))
    app.add_handler(MessageHandler(filters.PHOTO, handle_photo))

    app.run_webhook(
        listen="0.0.0.0",
        port=PORT,
        webhook_url=f"{WEBHOOK_URL}/telegram",
    )

if __name__ == "__main__":
    main()
