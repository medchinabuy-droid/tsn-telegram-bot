import json
from google.cloud import vision
from openai import OpenAI
from config import GOOGLE_CREDENTIALS_JSON, OPENAI_API_KEY

vision_client = vision.ImageAnnotatorClient.from_service_account_info(
    json.loads(GOOGLE_CREDENTIALS_JSON)
)

gpt_client = OpenAI(api_key=OPENAI_API_KEY)


def ocr_text(image_bytes: bytes) -> str:
    """
    Распознаём текст чека через Google Vision
    """
    image = vision.Image(content=image_bytes)
    resp = vision_client.text_detection(image=image)
    if resp.text_annotations:
        return resp.text_annotations[0].description
    return ""


async def gpt_parse_receipt(text: str) -> dict:
    """
    Разбор чека через GPT: сумма, дата, банк, confidence
    """
    prompt = f"""
Ты — сервис для разбора чеков.
Верни ТОЛЬКО валидный JSON без пояснений.

Текст чека:
{text}

Формат ответа:
{{"amount": 0.0, "date": "YYYY-MM-DD", "bank": "СБП|ВТБ|Альфа|Т-Банк|Другое", "confidence": 0.0}}
"""

    resp = gpt_client.chat.completions.create(
        model="gpt-4o-mini",
        messages=[{"role": "user", "content": prompt}],
        temperature=0.1
    )

    raw = resp.choices[0].message.content.strip()

    try:
        return json.loads(raw)
    except Exception:
        return {
            "amount": None,
            "date": None,
            "bank": "Другое",
            "confidence": 0.0,
            "raw": raw
        }
