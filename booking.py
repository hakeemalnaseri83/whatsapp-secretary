"""Small, safe first-stage restaurant booking workflow."""
import re

_pending: dict[str, dict] = {}


def _details(text: str) -> dict:
    details: dict = {}
    people = re.search(r"(?:ل|عدد\s*)?\s*(\d{1,2})\s*(?:شخص|أشخاص|اشخاص|person|people)", text, re.I)
    time = re.search(r"(?:الساعة|الساعة\s*)?\s*(\d{1,2}(?::\d{2})?)\s*(صباحاً|مساءً|مساء|am|pm)?", text, re.I)
    if people:
        details["people"] = people.group(1)
    if time and any(x in text.casefold() for x in ("ساعة", "الساعة", "am", "pm", "مساء", "صباح")):
        details["time"] = " ".join(x for x in time.groups() if x)
    details["request"] = text
    return details


def _preview(booking: dict) -> str:
    details = booking.get("details", {})
    lines = ["هذه معاينة الحجز:", f"الطلب: {details.get('request', booking.get('request', ''))}"]
    if details.get("people"):
        lines.append(f"عدد الأشخاص: {details['people']}")
    if details.get("time"):
        lines.append(f"الوقت: {details['time']}")
    lines += [f"رقم المطعم: {booking['restaurant_phone']}", "", "إذا كانت التفاصيل صحيحة اكتب: أؤكد"]
    return "\n".join(lines)


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
            booking["confirmed"] = True
            return "تم تأكيد الطلب. سأبدأ الاتصال بالمطعم وأرسل لك النتيجة بعد انتهاء المكالمة."
        if any(word in low for word in ("إلغاء", "الغاء", "cancel")):
            _pending.pop(sender, None)
            return "تم إلغاء طلب الحجز."
        if not booking.get("restaurant_phone"):
            phone = _phone(text)
            if phone:
                booking["restaurant_phone"] = phone
                return _preview(booking)
        return "لإكمال الحجز أرسل رقم هاتف المطعم، أو اكتب إلغاء."

    if any(word in low for word in ("احجز", "حجز", "مطعم", "restaurant", "reservation")):
        _pending[sender] = {"request": text, "details": _details(text)}
        return ("سأساعدك في حجز المطعم. أرسل رقم هاتف المطعم، ثم سأعرض لك معاينة "
                "قبل أي اتصال. لن أتصل أو أحجز دون تأكيدك الصريح.")
    return None


def take_confirmed(sender: str) -> dict | None:
    booking = _pending.get(sender)
    if not booking or not booking.get("confirmed") or not booking.get("restaurant_phone"):
        return None
    booking = dict(booking)
    booking["owner"] = sender
    _pending.pop(sender, None)
    return booking
