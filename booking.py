"""Small, safe first-stage restaurant booking workflow."""
import re

_pending: dict[str, dict] = {}


def _phone(text: str) -> str:
    m = re.search(r"\+?\d[\d\s()-]{7,}\d", text)
    return re.sub(r"[^\d+]", "", m.group(0)) if m else ""


def handle(sender: str, text: str) -> str | None:
    """Return a workflow response, or None when this is not a booking request."""
    low = text.casefold()
    if sender in _pending:
        booking = _pending[sender]
        if any(word in low for word in ("أؤكد", "اكد", "أوافق", "confirm", "نعم")):
            if not booking.get("restaurant_phone"):
                return "ممتاز. أرسل رقم هاتف المطعم بصيغة دولية لأجهّز الاتصال بعد تأكيدك النهائي."
            return "تم تأكيد الطلب. سأبدأ الاتصال بالمطعم وأرسل لك النتيجة بعد انتهاء المكالمة."
        if any(word in low for word in ("إلغاء", "الغاء", "cancel")):
            _pending.pop(sender, None)
            return "تم إلغاء طلب الحجز."
        if not booking.get("restaurant_phone"):
            phone = _phone(text)
            if phone:
                booking["restaurant_phone"] = phone
                return ("هذه معاينة الحجز:\n"
                        f"المطعم: {booking['request']}\n"
                        f"رقم المطعم: {phone}\n\n"
                        "إذا كانت التفاصيل صحيحة اكتب: أؤكد")
        return "لإكمال الحجز أرسل رقم هاتف المطعم، أو اكتب إلغاء."

    if any(word in low for word in ("احجز", "حجز", "مطعم", "restaurant", "reservation")):
        _pending[sender] = {"request": text}
        return ("سأساعدك في حجز المطعم. أرسل رقم هاتف المطعم، ثم سأعرض لك معاينة "
                "قبل أي اتصال. لن أتصل أو أحجز دون تأكيدك الصريح.")
    return None
