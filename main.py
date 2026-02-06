from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, HTMLResponse
import uvicorn

from bot import tg_app
from web.admin import router as admin_router
from services.scheduler import scheduler
from config import PORT, WEBHOOK_URL, WEBHOOK_SECRET
from telegram import Update

app = FastAPI()
app.include_router(admin_router)

@app.on_event("startup")
async def startup():
    scheduler.start()
    await tg_app.initialize()
    await tg_app.start()
    await tg_app.bot.set_webhook(WEBHOOK_URL, secret_token=WEBHOOK_SECRET)

@app.post("/webhook")
async def webhook(request: Request):
    data = await request.json()
    update = Update.de_json(data, tg_app.bot)
    await tg_app.process_update(update)
    return JSONResponse({"ok": True})

if __name__ == "__main__":
    uvicorn.run("main:app", host="0.0.0.0", port=PORT)
