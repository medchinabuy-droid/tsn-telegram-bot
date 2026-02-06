import json
from google.cloud import vision
from openai import OpenAI
from config import GOOGLE_CREDENTIALS_JSON, OPENAI_API_KEY

vision_client = vision.ImageAnnotatorClient.from_service_account_info(
    json.loads(GOOGLE_CREDENTIALS_JSON)
)
gpt_client = OpenAI(api_key=OPENAI_API_KEY)

def ocr_text(image_bytes: bytes) -> str:
    image = vision.Image(content=image_bytes)
    resp = vision_client.text_detection(image=image)
    return resp.text_annotations[0].description if resp.text_annotations else ""

async def gpt_parse_receipt(text: str) -> dict:
    prompt = f"""
Распознай чек и верни JSON:
{text}

Формат:
{{"amount": число, "date": "YYYY-MM-DD", "bank": "СБП|ВТБ|Альфа|Т-Банк|Другое", "confidence": 0.0-1.0}}
"""
    resp = gpt_client.chat.completions.create(
        model="gpt-4o-mini",
        messages=[{"role": "user", "content": prompt}],
        temperature=0.2
    )
    return json.loads(resp.choices[0].message.content)
