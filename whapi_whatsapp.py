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

import httpx
from fastapi import FastAPI, Request

from config import CONFIG
from llm import generate_reply

logging.basicConfig(level=logging.INFO)
app = FastAPI(title="WhatsApp AI Secretary (Whapi)")

# Official Whapi base for sending/receiving media
WHAPI = "https://gate.whapi.cloud"


def _headers() -> dict:
    return {"Authorization": f"Bearer {CONFIG.whapi_token}"}


def _send_text(to: str, text: str) -> None:
    try:
        r = httpx.post(f"{WHAPI}/messages/text",
                       json={"to": str(to), "body": text},
                       headers=_headers(), timeout=40)
        r.raise_for_status()
        logging.info("sent text to %s -> %s", to, r.status_code)
    except Exception as e:
        logging.warning("send_text failed: %s", e)


def _transcribe(media_id: str) -> str:
    """Downloads a voice note and transcribes it via OpenAI Whisper (needs key)."""
    if not CONFIG.openai_key:
        return ""
    try:
        r = httpx.get(f"{WHAPI}/media/{media_id}", headers=_headers(), timeout=120)
        r.raise_for_status()
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
        r = httpx.post(f"{WHAPI}/messages/audio",
                       json={"to": str(to), "media": uri, "mime_type": "audio/mpeg"},
                       headers=_headers(), timeout=90)
        r.raise_for_status()
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


@app.post("/")
async def webhook(req: Request) -> dict:
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
            "voice_replies": CONFIG.voice_replies, "whapi": bool(CONFIG.whapi_token)}


if __name__ == "__main__":
    import os
    import uvicorn
    uvicorn.run("whapi_whatsapp:app", host="0.0.0.0", port=int(os.environ.get("PORT", "8001")))
