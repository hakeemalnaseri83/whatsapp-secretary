"""LLM client that turns the caller's words into a friendly assistant reply.

Supports OpenAI (gpt-4o-mini) and Google Gemini (gemini-2.0-flash),
selected via AI_PROVIDER in .env. Always responds in the configured language
with the configured tone.
"""
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

    try:
        user_msg = (
            f"Caller ({caller_number or 'unknown'}) said: \"{caller_transcript}\". "
            "Give a short, warm final acknowledgement reply."
        )
        return {
            "openai": _openai(_system_prompt(), user_msg),
            "gemini": _gemini(_system_prompt(), user_msg),
        }[CONFIG.ai_provider]
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
