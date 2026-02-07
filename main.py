import uvicorn
from fastapi import FastAPI, Request
from telegram import Update
from telegram.ext import (
    ApplicationBuilder, CommandHandler, MessageHandler, filters
)

from config import BOT_TOKEN, WEBHOOK_URL, WEBHOOK_SECRET, PORT
from bot.handlers import register_handlers
from web.dashboard import router as dashboard_router

app = FastAPI()

tg_app = ApplicationBuilder().token(BOT_TOKEN).build()

tg_app.add_handler(CommandHandler("start", start))
tg_app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, reg_flow))
tg_app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))

@app.on_event("startup")
async def on_startup():
    await tg_app.initialize()
    await tg_app.start()
    await tg_app.bot.set_webhook(WEBHOOK_URL, secret_token=WEBHOOK_SECRET)

@app.post("/webhook")
async def webhook(request: Request):
    data = await request.json()
    update = Update.de_json(data, tg_app.bot)
    await tg_app.process_update(update)
    return {"ok": True}

app.include_router(dashboard_router, prefix="/admin")

if __name__ == "__main__":
    uvicorn.run("main:app", host="0.0.0.0", port=PORT)

