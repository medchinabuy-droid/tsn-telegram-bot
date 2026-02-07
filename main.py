import uvicorn
from fastapi import FastAPI, Request
from telegram import Update
from telegram.ext import ApplicationBuilder

from config import BOT_TOKEN, WEBHOOK_URL, WEBHOOK_SECRET, PORT
from bot.handlers import register_handlers
from web.dashboard import router as dashboard_router

app = FastAPI()

# Telegram app
tg_app = ApplicationBuilder().token(BOT_TOKEN).build()

# Регистрируем ВСЕ хендлеры централизованно
register_handlers(tg_app)


@app.on_event("startup")
async def on_startup():
    await tg_app.initialize()
    await tg_app.start()
    await tg_app.bot.set_webhook(
        url=WEBHOOK_URL,
        secret_token=WEBHOOK_SECRET
    )
    print("🚀 Webhook установлен:", WEBHOOK_URL)


@app.post("/webhook")
async def webhook(request: Request):
    data = await request.json()
    update = Update.de_json(data, tg_app.bot)
    await tg_app.process_update(update)
    return {"ok": True}


# Админка
app.include_router(dashboard_router, prefix="/admin")


if __name__ == "__main__":
    uvicorn.run("main:app", host="0.0.0.0", port=PORT)
