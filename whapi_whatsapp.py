"""WhatsApp "secretary" via Whapi.Cloud.

Receives WhatsApp messages (text and voice notes) and replies with the AI
assistant - as text or as a voice note (TTS), in the configured language.

Endpoints:
  POST /  -> Whapi webhook (set this URL in Whapi.Cloud webhook settings)
  GET  /health

Run:  uvicorn whapi_whatsapp:app --host 0.0.0.0 --port 8001
Expose with ngrok and register the / endpoint as the Whapi webhook.
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

WHAPI = "https://api.whapi.cloud"


def _headers() -> dict:
    return {"Authorization": f"Bearer {CONFIG.whapi_token}"}


def _send_text(to: str, text: str) -> None:
    try:
        httpx.post(f"{WHAPI}/messages/text", json={"to": to, "text": text},
                   headers=_headers(), timeout=30).raise_for_status()
    except Exception as e:
        logging.warning("send_text failed: %s", e)


def _transcribe(media_id: str) -> str:
    """Downloads a voice note and transcribes it via OpenAI Whisper (needs key)."""
    if not CONFIG.openai_key:
        return ""
    r = httpx.get(f"{WHAPI}/media/{media_id}", headers=_headers(), timeout=90)
    r.raise_for_status()
    audio_bytes = r.content
    files = {"file": ("voice.ogg", io.BytesIO(audio_bytes), "audio/ogg"),
             "model": (None, "whisper-1")}
    t = httpx.post("https://api.openai.com/v1/audio/transcriptions",
                   headers={"Authorization": f"Bearer {CONFIG.openai_key}"},
                   files=files, timeout=120)
    t.raise_for_status()
    return t.json().get("text", "").strip()


def _synthesize(text: str) -> bytes | None:
    """TTS via OpenAI (gpt-4o-mini-tts) -> mp3 bytes. Needs OpenAI key."""
    if not CONFIG.openai_key:
        return None
    r = httpx.post(
        "https://api.openai.com/v1/audio/speech",
        headers={"Authorization": f"Bearer {CONFIG.openai_key}"},
        json={"model": "gpt-4o-mini-tts", "voice": "alloy",
              "input": text, "response_format": "mp3"},
        timeout=60,
    )
    r.raise_for_status()
    return r.content


def _send_audio(to: str, mp3: bytes) -> None:
    uri = "data:audio/mp3;base64," + base64.b64encode(mp3).decode()
    try:
        httpx.post(f"{WHAPI}/messages/audio",
                   json={"to": to, "media": uri},
                   headers=_headers(), timeout=60).raise_for_status()
    except Exception as e:
        logging.warning("send_audio failed: %s", e)


def _handle_message(msg: dict) -> None:
    typ = msg.get("type") or ""
    sender = (msg.get("from") or msg.get("chat_id") or "").replace(" ", "")
    if not sender:
        logging.info("ignoring message without sender: %s", str(msg)[:200])
        return

    if typ == "text":
        text = msg.get("text") or msg.get("text_body") or ""
        reply = generate_reply(text, sender)
        _send_text(sender, reply)

    elif typ == "voice" and CONFIG.voice_replies:
        media_id = (msg.get("audio") or {}).get("id")
        if media_id:
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
            _send_text(sender, generate_reply("(voice message)", sender))
    else:
        _send_text(sender, generate_reply("(message)", sender))


@app.post("/")
async def webhook(req: Request) -> dict:
    try:
        data = await req.json()
    except Exception:
        return {"status": "ok"}
    messages = data.get("messages") or []
    for msg in reversed(messages):
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
