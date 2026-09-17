"""WhatsApp "secretary" via Whapi.Cloud (official API format).

Receives WhatsApp messages (text and voice notes) and replies with the AI
assistant - as text or as a voice note (TTS), in the configured language.

  POST /   -> Whapi webhook (register this URL in Whapi, POST)
  GET  /health

Official endpoints (Whapi docs):
  send text : POST https://gate.whapi.cloud/messages/text    {to, body}
  send audio: POST https://gate.whapi.cloud/messages/audio   {to, media, mime_type}
  get media : GET  https://gate.whapi.cloud/media/{id}
"""
import base64
import io
import logging
import hmac
import os

import httpx
from fastapi import FastAPI, Request, HTTPException

from config import CONFIG
from llm import generate_reply
from booking import handle as handle_booking, take_confirmed
from main import start_booking_call

logging.basicConfig(level=logging.INFO)
app = FastAPI(title="WhatsApp AI Secretary (Whapi)")

# Whapi base hosts - try each until one resolves/works on the host
WHAPI_BASES = ["https://api.whapi.cloud", "https://gate.whapi.cloud", "https://whapi.cloud"]


def _headers() -> dict:
    return {"Authorization": f"Bearer {CONFIG.whapi_token}"}


def _call(method: str, path: str, json_body=None, timeout: int = 40):
    """Tries every Whapi base host; returns the first successful response."""
    last: Exception = Exception("no whapi base attempted")
    for base in WHAPI_BASES:
        try:
            r = httpx.request(method, f"{base}{path}", json=json_body,
                              headers=_headers(), timeout=timeout)
            if r.status_code < 300:
                return r
            last = Exception(f"HTTP {r.status_code} from {base}: {r.text[:120]}")
        except Exception as e:  # DNS / connect errors -> try next host
            last = e
    raise last


def _send_text(to: str, text: str) -> None:
    try:
        r = _call("POST", "/messages/text", json_body={"to": str(to), "body": text})
        logging.info("sent text to %s -> %s", to, r.status_code)
    except Exception as e:
        logging.warning("send_text failed: %s", e)


def _transcribe(media_id: str) -> str:
    """Downloads a voice note and transcribes it via OpenAI Whisper (needs key)."""
    if not CONFIG.openai_key:
        return ""
    try:
        r = _call("GET", f"/media/{media_id}", timeout=120)
        files = {"file": ("voice.ogg", io.BytesIO(r.content), "audio/ogg"),
                 "model": (None, "whisper-1")}
        t = httpx.post("https://api.openai.com/v1/audio/transcriptions",
                       headers={"Authorization": f"Bearer {CONFIG.openai_key}"},
                       files=files, timeout=120)
        t.raise_for_status()
        return t.json().get("text", "").strip()
    except Exception as e:
        logging.warning("transcribe failed: %s", e)
        return ""


def _synthesize(text: str) -> bytes | None:
    """TTS via OpenAI (gpt-4o-mini-tts) -> mp3 bytes."""
    if not CONFIG.openai_key:
        return None
    try:
        r = httpx.post(
            "https://api.openai.com/v1/audio/speech",
            headers={"Authorization": f"Bearer {CONFIG.openai_key}"},
            json={"model": "gpt-4o-mini-tts", "voice": "alloy",
                  "input": text, "response_format": "mp3"},
            timeout=60,
        )
        r.raise_for_status()
        return r.content
    except Exception as e:
        logging.warning("synthesize failed: %s", e)
        return None


def _send_audio(to: str, mp3: bytes) -> None:
    uri = "data:audio/mp3;base64," + base64.b64encode(mp3).decode()
    try:
        r = _call("POST", "/messages/audio",
                  json_body={"to": str(to), "media": uri, "mime_type": "audio/mpeg"},
                  timeout=90)
        logging.info("sent audio to %s -> %s", to, r.status_code)
    except Exception as e:
        logging.warning("send_audio failed: %s", e)


def _text_of(msg: dict) -> str:
    tb = msg.get("text")
    if isinstance(tb, dict):
        return (tb.get("body") or "").strip()
    return (str(tb) if tb else "").strip()


def _handle_message(msg: dict) -> None:
    if msg.get("from_me"):
        return  # skip our own outbound confirmations
    typ = msg.get("type") or ""
    sender = msg.get("from") or (msg.get("chat_id") or "").split("@")[0]
    sender = (sender or "").strip()
    if not sender:
        logging.info("message without sender; skipping")
        return
    logging.info("incoming type=%s sender=%s", typ, sender)

    if typ == "text":
        text = _text_of(msg)
        if not text:
            return
        booking_reply = handle_booking(sender, text)
        if booking_reply:
            _send_text(sender, booking_reply)
            booking = take_confirmed(sender)
            if booking:
                try:
                    start_booking_call(booking)
                except Exception as e:
                    logging.warning("booking call failed: %s", e)
                    _send_text(sender, "تعذر بدء الاتصال بالمطعم حالياً. لم يتم أي حجز.")
            return
        reply = generate_reply(text, sender)
        _send_text(sender, reply)
        return

    # voice note / media message
    medium = (msg.get("audio") or msg.get("ptt") or msg.get("media")
              or msg.get("video") or msg.get("document") or {})
    media_id = medium.get("id") if isinstance(medium, dict) else None
    if media_id and CONFIG.voice_replies:
        logging.info("processing media_id=%s", media_id)
        transcript = _transcribe(media_id)
        if transcript:
            reply = generate_reply(transcript, sender)
            mp3 = _synthesize(reply)
            if mp3:
                _send_audio(sender, mp3)
            else:
                _send_text(sender, reply)
        else:
            _send_text(sender, generate_reply("(voice message)", sender))
    else:
        _send_text(sender, generate_reply("(message)", sender))


@app.get("/")
async def webhook_check() -> dict:
    """Allow provider connectivity checks without triggering message handling."""
    return {"status": "ok", "webhook": "ready"}


@app.post("/")
async def webhook(req: Request) -> dict:
    secret = CONFIG.whapi_webhook_secret
    if not secret:
        if os.getenv("ENV", "production").lower() == "production":
            raise HTTPException(status_code=503, detail="Webhook verification is not configured")
    elif not hmac.compare_digest(req.headers.get("X-Webhook-Secret", ""), secret):
        raise HTTPException(status_code=401, detail="Invalid webhook signature")
    try:
        data = await req.json()
    except Exception:
        return {"status": "ok"}
    logging.info("webhook received event=%s", (data.get("event") or {}).get("type"))
    messages = data.get("messages") or []
    for msg in messages:
        try:
            _handle_message(msg)
        except Exception as e:
            logging.warning("handle_message error: %s", e)
    return {"status": "ok"}


@app.get("/health")
async def health() -> dict:
    return {"status": "ok", "llm": CONFIG.ai_provider if CONFIG.has_llm else "offline",
            "voice_replies": CONFIG.voice_replies, "whapi": bool(CONFIG.whapi_token),
            "webhook_verification": bool(CONFIG.whapi_webhook_secret)}


if __name__ == "__main__":
    import os
    import uvicorn
    uvicorn.run("whapi_whatsapp:app", host="0.0.0.0", port=int(os.environ.get("PORT", "8001")))
