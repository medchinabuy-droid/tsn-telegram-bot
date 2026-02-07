import io
import qrcode

# Реквизиты можно вынести в config.py
RECEIVER_NAME = "ТСН"
SBP_STATIC_QR = "https://qr.nspk.ru/AS1A000000000000000000000000000"  # заглушка

def make_spb_qr(amount: float) -> bytes:
    """
    Генерирует локальный QR-код СБП (PNG) с суммой
    """
    payload = f"ST00012|Name={RECEIVER_NAME}|BankName=СБП|Sum={int(amount * 100)}"
    qr = qrcode.make(payload)
    buf = io.BytesIO()
    qr.save(buf, format="PNG")
    return buf.getvalue()


def bank_deeplink(bank: str, amount: float) -> str:
    """
    Deeplink для мобильных приложений банков
    """
    links = {
        "sbp": f"{SBP_STATIC_QR}?amount={amount}",
        "vtb": f"vtbapp://pay?amount={amount}",
        "alfa": f"alfabank://pay?amount={amount}",
        "tbank": f"tinkoff://pay?amount={amount}",
    }
    return links.get(bank, "")
