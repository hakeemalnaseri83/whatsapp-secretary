"""LLM client that turns the caller's words into a friendly assistant reply.

Supports OpenAI (gpt-4o-mini) and Google Gemini (gemini-2.0-flash),
selected via AI_PROVIDER in .env. Always responds in the configured language
with the configured tone.
"""
import json
import logging

import httpx

from config import CONFIG

logging.basicConfig(level=logging.INFO)


def _system_prompt() -> str:
    lang_prompt = {
        "ar": "Respond always in Arabic (العربية).",
        "en": "Respond always in English.",
        "tr": "Respond always in Turkish (Türkçe).",
    }.get(CONFIG.language, "Respond always in Arabic.")

    tone_core = (
        "You are a warm, friendly personal assistant. Be courteous and concise."
        if CONFIG.tone != "formal"
        else "You are a professional, polite personal assistant. Be concise."
    )
    user_ctx = f"\nThe user's name is {CONFIG.user_name}." if CONFIG.user_name else ""

    return (
        f"{tone_core} You answer phone calls on behalf of the owner who is "
        f"currently unavailable. Greet politely, take the reason for the call or "
        f"a message, and end warmly. {lang_prompt} Keep the reply under 30 words.{user_ctx}"
    )


def generate_reply(caller_transcript: str, caller_number: str) -> str:
    """Returns the assistant's spoken reply for the given caller transcript."""
    fallback = {
        "ar": "شكرًا لك، سأنقل الرسالة لصاحب الرقم.",
        "en": "Thank you. I'll pass your message to the owner.",
        "tr": "Teşekkür ederim. Mesajınızı sahibine ileteceğim.",
    }.get(CONFIG.language, "Thank you.")

    if not CONFIG.has_llm:
        return fallback


def generate_booking_reply(transcript: str, booking: dict) -> tuple[str, str]:
    """Reply naturally during a restaurant call.

    Returns (spoken_reply, status), where status is continue, confirmed,
    not_available, or needs_review. The model may answer only from booking
    facts and must never invent availability.
    """
    details = booking.get("details", {})
    facts = {
        "name": booking.get("profile", {}).get("name") or "صاحب هذا الرقم",
        "date": details.get("date", "غير محدد"),
        "time": details.get("time", "غير محدد"),
        "people": details.get("people", "غير محدد"),
        "request": details.get("request", booking.get("request", "حجز طاولة")),
    }
    low = transcript.casefold()
    if any(word in low for word in ("غير متاح", "لا يوجد", "ممتلئ", "مرفوض", "لا يمكن", "not available")):
        return "شكرًا للتوضيح. سأبلغ صاحب الطلب بأن الموعد غير متاح.", "not_available"
    if any(word in low for word in ("تم الحجز", "تم التأكيد", "مؤكد", "نعم متاح", "نعم، متاح", "متاح لدينا", "available")):
        return "شكرًا، سجّلت أن الموعد متاح وتم تأكيد الحجز.", "confirmed"

    fallback = (f"اسم الحجز {facts['name']}. التفاصيل: {facts['date']}، الساعة {facts['time']}، "
                f"لعدد {facts['people']} من الأشخاص. ما الخيار المتاح لديكم؟")
    if not CONFIG.has_llm:
        return fallback, "continue"
    system = (
        "أنت موظف حجوزات يتحدث العربية في مكالمة هاتفية طبيعية. "
        "أجب باختصار وبأدب عن سؤال المطعم مستخدماً الحقائق فقط. "
        "يمكنك مناقشة الاسم والتاريخ والوقت وعدد الأشخاص والبدائل والملاحظات. "
        "لا تخترع توفرًا أو سعرًا. إذا قال المطعم إن الموعد متاح أو أكد الحجز، status=confirmed. "
        "إذا قال غير متاح، status=not_available. وإلا status=continue. "
        "أعد JSON فقط بالشكل: {\"reply\":\"...\",\"status\":\"continue|confirmed|not_available\"}."
    )
    user = json.dumps({"facts": facts, "restaurant_said": transcript,
                       "history": booking.get("conversation", [])[-4:]}, ensure_ascii=False)
    try:
        text = _gemini(system, user) if CONFIG.ai_provider == "gemini" else _openai(system, user)
        data = json.loads(text.strip().removeprefix("```json").removesuffix("```").strip())
        reply = str(data.get("reply", "")).strip()
        status = str(data.get("status", "continue")).strip()
        if reply and status in {"continue", "confirmed", "not_available"}:
            return reply, status
    except Exception as e:
        logging.warning("booking conversation reply failed: %r", e)
    return fallback, "continue"

    try:
        user_msg = (
            f"Caller ({caller_number or 'unknown'}) said: \"{caller_transcript}\". "
            "Give a short, warm final acknowledgement reply."
        )
        # Do not construct a dict of calls here: Python evaluates every value
        # eagerly, which previously called Gemini even when OpenAI was selected.
        if CONFIG.ai_provider == "gemini":
            return _gemini(_system_prompt(), user_msg)
        return _openai(_system_prompt(), user_msg)
    except Exception as e:
        logging.warning("LLM generate_reply failed: %r", e)
        if CONFIG.debug:
            return f"[LLM error] {e}"
        return fallback


def _openai(system: str, user: str) -> str:
    body = {
        "model": "gpt-4o-mini",
        "temperature": 0.7,
        "max_tokens": 100,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
    }
    r = httpx.post(
        "https://api.openai.com/v1/chat/completions",
        headers={"Authorization": f"Bearer {CONFIG.openai_key}"},
        json=body,
        timeout=30,
    )
    r.raise_for_status()
    return r.json()["choices"][0]["message"]["content"].strip()


def _gemini(system: str, user: str) -> str:
    url = (
        "https://generativelanguage.googleapis.com/v1beta/models/"
        f"gemini-2.0-flash:generateContent?key={CONFIG.gemini_key}"
    )
    body = {
        "contents": [{"role": "user", "parts": [{"text": f"{system}\n\n{user}"}]}],
        "generationConfig": {"temperature": 0.7, "maxOutputTokens": 100},
    }
    r = httpx.post(url, json=body, timeout=30)
    r.raise_for_status()
    parts = r.json()["candidates"][0]["content"]["parts"]
    return parts[0]["text"].strip()
