"""Twilio-backed AI voice assistant backend.

Endpoints (configure these as the Twilio number's webhooks):
  POST /voice    -> TwiML entry: greet the caller and listen (Gather speech)
  POST /respond  -> receives the caller's transcript, asks the LLM, replies
  POST /sms      -> auto-reply to SMS / WhatsApp sent to the Twilio number
  GET  /health   -> simple health check

Run:  uvicorn main:app --host 0.0.0.0 --port 8000
Expose publicly (e.g. ngrok) and set that URL as the Twilio webhook.
"""
import json
import os
import logging
import time
import hmac

from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import Response
from twilio.twiml.voice_response import VoiceResponse, Gather, Say, Hangup
from twilio.twiml.messaging_response import MessagingResponse

from config import CONFIG
from llm import generate_reply

logging.basicConfig(level=logging.INFO)
app = FastAPI(title="AI Voice Assistant (Twilio)")

async def _verified_request(req: Request) -> None:
    secret = CONFIG.twilio_webhook_secret
    if not secret:
        if os.getenv("ENV", "production").lower() == "production":
            raise HTTPException(status_code=503, detail="Webhook verification is not configured")
        return
    if not hmac.compare_digest(req.headers.get("X-Webhook-Secret", ""), secret):
        raise HTTPException(status_code=401, detail="Invalid webhook signature")


# ---------- helpers ----------

def _voice_name() -> str:
    return {
        "ar": "Google.ar-XA-Standard-A",
        "en": "Google.en-US-Standard-F",
        "tr": "Google.tr-TR-Standard-A",
    }.get(CONFIG.language, "Google.en-US-Standard-F")


def _greeting() -> str:
    return {
        "ar": "أهلاً! صاحب الرقم مشغول الآن. هل يمكنك إخباري بالسبب أو تترك رسالة وسأوصّله بها؟",
        "en": "Hi! The owner is busy right now. Could you tell me the reason or leave a message and I'll pass it along?",
        "tr": "Merhaba! Sahibi şu an meşgul. Nedeni söyleyebilir veya mesaj bırakabilirsiniz, ileteceğim.",
    }.get(CONFIG.language, "Hi! Please leave a message.")


def _log(call_sid: str, caller: str, transcript: str, reply: str) -> None:
    try:
        with open(CONFIG.transcript_file, "a", encoding="utf-8") as f:
            f.write(json.dumps({
                "ts": int(time.time()), "call_sid": call_sid, "caller": caller,
                "transcript": transcript, "reply": reply,
            }) + "\n")
    except Exception as e:
        logging.warning("transcript write failed: %s", e)


# ---------- voice ----------

@app.post("/voice")
async def voice(req: Request) -> Response:
    await _verified_request(req)
    form = await req.form()
    caller = form.get("From", "")
    call_sid = form.get("CallSid", "")

    resp = VoiceResponse()
    gather = Gather(
        input="speech",
        timeout="4",
        speechTimeout="auto",
        speechModel="phone_call",
        action="/respond",
        method="POST",
    )
    gather.say(_greeting(), voice=_voice_name())
    resp.append(gather)
    resp.say(
        "لم أسمعك جيداً. سأنهي المكالمة. شكراً لك." if CONFIG.language == "ar"
        else "I couldn't hear you. Ending the call. Thank you.",
        voice=_voice_name(),
    )
    resp.hangup()
    return Response(resp.to_xml(), media_type="text/xml")


@app.post("/respond")
async def respond(req: Request) -> Response:
    await _verified_request(req)
    form = await req.form()
    transcript = (form.get("SpeechResult") or "").strip()
    caller = form.get("From", "")
    call_sid = form.get("CallSid", "")

    reply = generate_reply(transcript, caller)
    _log(call_sid, caller, transcript, reply)

    resp = VoiceResponse()
    resp.say(reply, voice=_voice_name())
    # Give the caller a chance to add one more detail, then end warmly.
    resp.say(
        "هل هناك أي شيء آخر؟" if CONFIG.language == "ar" else
        "Anything else?" if CONFIG.language == "en" else "Başka bir şey var mı?",
        voice=_voice_name(),
    )
    gather = Gather(
        input="speech", timeout="3", speechTimeout="auto",
        speechModel="phone_call", action="/final", method="POST",
    )
    resp.append(gather)
    resp.say(
        "شكراً، سأقوم بتوصيل رسالتك. مع السلامة." if CONFIG.language == "ar"
        else "Thank you, I'll pass it along. Goodbye." if CONFIG.language == "en"
        else "Teşekkürler, mesajınızı ileteceğim. Hoşça kalın.",
        voice=_voice_name(),
    )
    resp.hangup()
    return Response(resp.to_xml(), media_type="text/xml")


@app.post("/final")
async def final(req: Request) -> Response:
    await _verified_request(req)
    form = await req.form()
    transcript = (form.get("SpeechResult") or "").strip()
    caller = form.get("From", "")
    call_sid = form.get("CallSid", "")
    if transcript:
        _log(call_sid, caller, transcript, "(additional)")
    resp = VoiceResponse()
    resp.say(
        "شكراً، سأقوم بتوصيل كل شيء. مع السلامة." if CONFIG.language == "ar"
        else "Thank you, I'll pass everything along. Goodbye." if CONFIG.language == "en"
        else "Teşekkürler, her şeyi ileteceğim. Hoşça kalın.",
        voice=_voice_name(),
    )
    resp.hangup()
    return Response(resp.to_xml(), media_type="text/xml")


# ---------- messaging (SMS / WhatsApp) ----------

@app.post("/sms")
async def sms(req: Request) -> Response:
    await _verified_request(req)
    form = await req.form()
    incoming = (form.get("Body") or "").strip()
    sender = form.get("From", "")
    reply = generate_reply(incoming, sender)

    msg = MessagingResponse()
    msg.message(reply)
    return Response(msg.to_xml(), media_type="text/xml")


# ---------- health / info ----------

@app.get("/health")
async def health() -> dict:
    return {
        "status": "ok",
        "language": CONFIG.language,
        "tone": CONFIG.tone,
        "llm": CONFIG.ai_provider if CONFIG.has_llm else "offline-fallback",
        "caller_sees_voice": True,
        "webhook_verification": bool(CONFIG.twilio_webhook_secret),
    }


if __name__ == "__main__":
    os.environ.setdefault("PORT", "8000")
    import uvicorn
    uvicorn.run("main:app", host="0.0.0.0", port=int(os.environ["PORT"]))
