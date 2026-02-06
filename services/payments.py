import io
import qrcode

def make_spb_qr(amount: float) -> bytes:
    payload = f"ST00012|Name=ТСН|BankName=СБП|Sum={int(amount*100)}"
    qr = qrcode.make(payload)
    buf = io.BytesIO()
    qr.save(buf, format="PNG")
    return buf.getvalue()

def bank_deeplink(bank: str, amount: float) -> str:
    links = {
        "sbp": f"https://qr.nspk.ru/AS1A000000000000000000000000000?amount={amount}",
        "vtb": f"vtbapp://pay?amount={amount}",
        "alfa": f"alfabank://pay?amount={amount}",
        "tbank": f"tinkoff://pay?amount={amount}",
    }
    return links.get(bank, "")
