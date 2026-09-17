"""Small, safe first-stage restaurant booking workflow."""
import re
from profile import get_profile

_pending: dict[str, dict] = {}


def _details(text: str) -> dict:
    details: dict = {}
    people = re.search(r"(?:ل|عدد\s*)?\s*(\d{1,2})\s*(?:شخص|أشخاص|اشخاص|person|people)", text, re.I)
    time = re.search(r"(?:الساعة|الساعة\s*)?\s*(\d{1,2}(?::\d{2})?)\s*(صباحاً|مساءً|مساء|am|pm)?", text, re.I)
    if people:
        details["people"] = people.group(1)
    else:
        word_people = {
            "شخصين": "2", "اثنين": "2", "اثنان": "2", "شخص": "1",
            "ثلاثة": "3", "ثلاث": "3", "اربعة": "4", "أربعة": "4",
            "خمسة": "5", "ستة": "6", "سبعة": "7", "ثمانية": "8",
        }
        for word, count in word_people.items():
            if word in text:
                details["people"] = count
                break
    if time and any(x in text.casefold() for x in ("ساعة", "الساعة", "am", "pm", "مساء", "صباح")):
        details["time"] = " ".join(x for x in time.groups() if x)
    if "غدا" in text or "غداً" in text:
        details["date"] = "غداً"
    elif "اليوم" in text:
        details["date"] = "اليوم"
    details["request"] = text
    return details


def _preview(booking: dict) -> str:
    details = booking.get("details", {})
    lines = ["هذه معاينة الحجز:", f"الطلب: {details.get('request', booking.get('request', ''))}"]
    if details.get("people"):
        lines.append(f"عدد الأشخاص: {details['people']}")
    if details.get("time"):
        lines.append(f"الوقت: {details['time']}")
    if details.get("date"):
        lines.append(f"التاريخ: {details['date']}")
    lines += [f"رقم المطعم: {booking['restaurant_phone']}", "", "إذا كانت التفاصيل صحيحة اكتب: أؤكد"]
    return "\n".join(lines)


def _missing(details: dict) -> list[str]:
    return [label for key, label in (("date", "التاريخ"), ("time", "الوقت"), ("people", "عدد الأشخاص"))
            if not details.get(key)]


def _phone(text: str) -> str:
    m = re.search(r"\+?\d[\d\s()-]{7,}\d", text)
    if not m:
        return ""
    phone = re.sub(r"[^\d+]", "", m.group(0))
    # Accept Turkish local mobile format and normalize it for Twilio.
    if phone.startswith("0090") and len(phone) == 14:
        phone = "+90" + phone[4:]
    elif phone.startswith("0") and len(phone) == 11:
        phone = "+90" + phone[1:]
    elif phone.startswith("90") and len(phone) == 12:
        phone = "+" + phone
    return phone


def handle(sender: str, text: str) -> str | None:
    """Return a workflow response, or None when this is not a booking request."""
    low = text.casefold().replace("أ", "ا").replace("إ", "ا").replace("آ", "ا")
    if sender in _pending:
        booking = _pending[sender]
        if any(word in low for word in ("أؤكد", "اؤكد", "اكد", "أوافق", "اوافق", "confirm", "نعم")):
            if not booking.get("restaurant_phone"):
                return "ممتاز. أرسل رقم هاتف المطعم بصيغة دولية لأجهّز الاتصال بعد تأكيدك النهائي."
            booking["confirmed"] = True
            return "تم تأكيد الطلب. سأبدأ الاتصال بالمطعم وأرسل لك النتيجة بعد انتهاء المكالمة."
        if any(word in low for word in ("إلغاء", "الغاء", "cancel")):
            _pending.pop(sender, None)
            return "تم إلغاء طلب الحجز."
        phone = _phone(text)
        if phone:
            booking["restaurant_phone"] = phone
            missing = _missing(booking.get("details", {}))
            if missing:
                return "أرسل من فضلك: " + "، ".join(missing) + "، ثم سأعرض المعاينة."
            return _preview(booking)
        if not booking.get("restaurant_phone"):
            details = booking.setdefault("details", {})
            details.update({k: v for k, v in _details(text).items() if k != "request"})
            missing = _missing(details)
            if missing:
                return "لإكمال الحجز أرسل: " + "، ".join(missing) + "، أو اكتب إلغاء."
            if booking.get("restaurant_phone"):
                return _preview(booking)
            return "أرسل رقم هاتف المطعم، أو اكتب إلغاء."
        return "لإكمال الحجز أرسل رقم هاتف المطعم، أو اكتب إلغاء."

    booking_words = (
        "احجز", "احجزلي", "احجز لي", "حجز", "حجوز", "مطعم", "مطاعم",
        "طاولة", "ترابيزة", "اريد حجز", "اريد ان احجز", "ساعدني احجز",
        "restaurant", "reservation", "book a table",
    )
    if any(word in low for word in booking_words):
        details = _details(text)
        _pending[sender] = {"request": text, "details": details}
        missing = _missing(details)
        extra = f" أرسل أيضاً: {'، '.join(missing)}." if missing else ""
        return ("سأساعدك في حجز المطعم. أرسل رقم هاتف المطعم، ثم سأعرض لك معاينة "
                "قبل أي اتصال. لن أتصل أو أحجز دون تأكيدك الصريح." + extra)
    return None


def take_confirmed(sender: str) -> dict | None:
    booking = _pending.get(sender)
    if not booking or not booking.get("confirmed") or not booking.get("restaurant_phone"):
        return None
    booking = dict(booking)
    booking["owner"] = sender
    booking["profile"] = get_profile(sender)
    _pending.pop(sender, None)
    return booking
