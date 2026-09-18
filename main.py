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
import httpx
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from apscheduler.schedulers.background import BackgroundScheduler
from uuid import uuid4
from twilio.rest import Client

from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import Response
from twilio.twiml.voice_response import VoiceResponse, Gather, Say, Hangup
from twilio.twiml.messaging_response import MessagingResponse

from config import CONFIG
from booking import normalize_phone
from llm import generate_reply, generate_booking_reply
from profile import get_profile
from store import update_booking, create_reminder, due_reminders, mark_reminder_sent

logging.basicConfig(level=logging.INFO)
app = FastAPI(title="AI Voice Assistant (Twilio)")
_booking_calls: dict[str, dict] = {}
_WHAPI_BASES = ("https://api.whapi.cloud", "https://gate.whapi.cloud", "https://whapi.cloud")
_reminders: list[dict] = []


def _booking_result_status(result: str) -> str:
    text = result.casefold()
    if any(word in text for word in ("غير متاح", "لا يوجد", "ممتلئ", "لا يمكن", "رفض", "fully booked", "not available")):
        return "not_available"
    if any(word in text for word in ("تم الحجز", "تم التأكيد", "متاح", "أكد", "confirmed", "available")):
        return "confirmed"
    return "needs_review"


def _schedule_reminder(booking: dict) -> None:
    details = booking.get("details", {})
    if details.get("date") not in ("اليوم", "غداً") or not details.get("time"):
        logging.info("reminder skipped: date or time was not understood")
        return
    match = __import__("re").search(r"(\d{1,2})(?::(\d{2}))?", details["time"])
    if not match:
        return
    hour, minute = int(match.group(1)), int(match.group(2) or 0)
    if "مساء" in details["time"] and hour < 12:
        hour += 12
    now = datetime.now(ZoneInfo("Europe/Istanbul"))
    day = now.date() + timedelta(days=1 if details["date"] == "غداً" else 0)
    reminder_at = datetime.combine(day, datetime.min.time(), ZoneInfo("Europe/Istanbul")).replace(hour=hour, minute=minute) - timedelta(hours=2)
    create_reminder(booking.get("owner", ""), booking.get("request", ""), reminder_at.isoformat())


def _send_due_reminders() -> None:
    now = datetime.now(ZoneInfo("Europe/Istanbul")).isoformat()
    due = due_reminders(now)
    for reminder_id, owner, request in due:
        _notify_owner_whatsapp("reminder", owner,
                               f"تذكير: موعد الحجز بعد ساعتين.\nالطلب: {request}",
                               heading="تذكير الحجز")
        mark_reminder_sent(reminder_id)


@app.get("/tasks/reminders")
async def reminder_task(req: Request) -> dict:
    """Publicly callable, secret-protected wake-up endpoint for a free host."""
    secret = CONFIG.reminder_task_secret
    supplied = req.query_params.get("key", "")
    if not secret or not hmac.compare_digest(supplied, secret):
        raise HTTPException(status_code=401, detail="Invalid task key")
    _send_due_reminders()
    return {"status": "ok"}


_scheduler = BackgroundScheduler(daemon=True)
_scheduler.add_job(_send_due_reminders, "interval", minutes=1)
_scheduler.start()

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


def _notify_owner_whatsapp(call_sid: str, caller: str, transcript: str,
                            heading: str = "رسالة من مكالمة واردة") -> None:
    """Forward a caller's message to the configured owner via Whapi."""
    if not CONFIG.owner_whatsapp_number:
        logging.warning("owner WhatsApp notification skipped: OWNER_WHATSAPP_NUMBER is empty")
        return
    if not CONFIG.whapi_token:
        logging.warning("owner WhatsApp notification skipped: WHAPI_TOKEN is empty")
        return
    if not transcript:
        return
    body = (
        f"{heading}\n"
        f"رقم المتصل: {caller or 'غير معروف'}\n"
        f"النص: {transcript}\n"
        f"معرّف المكالمة: {call_sid or 'غير متوفر'}"
    )
    last_error = None
    for base in _WHAPI_BASES:
        try:
            r = httpx.post(
                f"{base}/messages/text",
                headers={"Authorization": f"Bearer {CONFIG.whapi_token}"},
                json={"to": CONFIG.owner_whatsapp_number, "body": body},
                timeout=25,
            )
            if r.status_code < 300:
                logging.info("owner notification sent via WhatsApp using %s", base)
                return
            last_error = f"HTTP {r.status_code}: {r.text[:160]}"
        except Exception as e:
            last_error = str(e)
    logging.warning("owner WhatsApp notification failed: %s", last_error)

def start_booking_call(booking: dict) -> str:
    if not CONFIG.twilio_account_sid or not CONFIG.twilio_auth_token or not CONFIG.twilio_from_number:
        raise RuntimeError("Twilio credentials or caller number are not configured")
    phone = normalize_phone(booking.get("restaurant_phone", ""))
    if not phone or not phone.startswith("+"):
        raise RuntimeError("رقم المطعم غير صالح. أرسله بصيغة دولية مثل +905356041588")
    booking["restaurant_phone"] = phone
    booking_id = uuid4().hex
    _booking_calls[booking_id] = booking
    Client(CONFIG.twilio_account_sid, CONFIG.twilio_auth_token).calls.create(
        to=phone, from_=CONFIG.twilio_from_number,
        url=f"{CONFIG.public_base_url}/booking/voice?booking_id={booking_id}", method="POST",
        status_callback=f"{CONFIG.public_base_url}/booking/status?booking_id={booking_id}",
        status_callback_method="POST",
        status_callback_event=["completed"])
    return booking_id

@app.post("/booking/voice")
async def booking_voice(req: Request) -> Response:
    booking_id = req.query_params.get("booking_id", "")
    if booking_id not in _booking_calls:
        return Response("<Response><Say>Booking unavailable.</Say><Hangup/></Response>", media_type="text/xml")
    booking = _booking_calls[booking_id]
    retry = req.query_params.get("retry", "0") == "1"
    resp = VoiceResponse()
    gather = Gather(input="speech", timeout="8", speechTimeout="auto",
                    speechModel="phone_call", language="ar-SA",
                    hints="نعم، لا، متاح، غير متاح، الساعة، غداً، اليوم، شخص، أشخاص، حجز، تأكيد",
                    action=f"/booking/respond?booking_id={booking_id}", method="POST")
    details = booking.get("details", {})
    request = details.get("request", booking.get("request", "حجز طاولة"))
    date = details.get("date", "غير محدد")
    time = details.get("time", "غير محدد")
    people = details.get("people", "غير محدد")
    profile = booking.get("profile", {})
    customer_name = profile.get("name", "").strip() or "صاحب هذا الرقم"
    customer = f" واسم صاحب الحجز هو {customer_name}"
    if booking.get("operation") == "cancel":
        action = "إلغاء الحجز"
    else:
        action = (f"حجز طاولة باسم {customer_name}، بتاريخ {date}، الساعة {time}، لعدد {people} من الأشخاص. "
                  f"الطلب الأصلي: {request}")
    gather.say(
        f"مرحباً، أتصل نيابة عن عميل{customer}. أريد {action}. "
        "هل هذا الموعد متاح؟ أرجو الإجابة بوضوح: نعم أو لا، وذكر أي ملاحظة.",
        voice=_voice_name())
    resp.append(gather)
    if not retry:
        # Do not treat silence as a successful booking. Give the restaurant
        # one additional chance to answer before ending the call.
        resp.say("لم أسمع إجابة. سأعيد السؤال مرة واحدة.", voice=_voice_name())
        resp.redirect(f"/booking/voice?booking_id={booking_id}&retry=1", method="POST")
    else:
        resp.say("لم تصل إجابة واضحة من المطعم. سأبلغ صاحب الطلب للمراجعة.", voice=_voice_name())
        resp.hangup()
    return Response(resp.to_xml(), media_type="text/xml")

@app.post("/booking/respond")
async def booking_respond(req: Request) -> Response:
    booking_id = req.query_params.get("booking_id", "")
    followup = req.query_params.get("followup", "0") == "1"
    booking = _booking_calls.get(booking_id)
    form = await req.form()
    result = (form.get("SpeechResult") or "لم تصل نتيجة واضحة من المطعم.").strip()
    if booking and result != "لم تصل نتيجة واضحة من المطعم.":
        reply, conversation_status = generate_booking_reply(result, booking)
        conversation = booking.setdefault("conversation", [])
        conversation.append({"restaurant": result, "assistant": reply})
        booking["_conversation_status"] = conversation_status
        if conversation_status == "continue" and len(conversation) < 4:
            _booking_calls[booking_id] = booking
            answer = VoiceResponse()
            answer.say(reply, voice=_voice_name())
            gather = Gather(input="speech", timeout="10", speechTimeout="auto",
                            speechModel="phone_call", language="ar-SA",
                            hints="نعم، لا، متاح، غير متاح، الاسم، التاريخ، الساعة، العدد، بديل",
                            action=f"/booking/respond?booking_id={booking_id}&followup=1",
                            method="POST")
            answer.append(gather)
            answer.say("لم تصل إجابة أخرى. سأرسل المحادثة للمراجعة.", voice=_voice_name())
            answer.hangup()
            return Response(answer.to_xml(), media_type="text/xml")
        if conversation_status == "confirmed":
            result = "تم التأكيد: " + result
        elif conversation_status == "not_available":
            result = "غير متاح: " + result
    if booking and any(word in result.casefold() for word in ("اسم", "باسم", "مين", "who")):
        profile = booking.get("profile", {})
        customer_name = profile.get("name", "").strip() or "صاحب هذا الرقم"
        _booking_calls[booking_id] = booking
        answer = VoiceResponse()
        answer.say(
            f"اسم الحجز هو {customer_name}. والتفاصيل: التاريخ {booking.get('details', {}).get('date', 'غير محدد')}، "
            f"الساعة {booking.get('details', {}).get('time', 'غير محدد')}، لعدد {booking.get('details', {}).get('people', 'غير محدد')} من الأشخاص. "
            "هل الموعد متاح؟ أجيبوا بنعم أو لا.", voice=_voice_name())
        gather = Gather(input="speech", timeout="10", speechTimeout="auto",
                        speechModel="phone_call", language="ar-SA",
                        hints="نعم، لا، متاح، غير متاح، مؤكد، ممتلئ",
                        action=f"/booking/respond?booking_id={booking_id}&followup=1",
                        method="POST")
        answer.append(gather)
        answer.say("لم تصل إجابة واضحة. سأرسل النتيجة للمراجعة.", voice=_voice_name())
        answer.hangup()
        return Response(answer.to_xml(), media_type="text/xml")
    if booking and not followup and _booking_result_status(result) == "needs_review":
        details = booking.get("details", {})
        _booking_calls[booking_id] = booking
        retry = VoiceResponse()
        retry.say(
            f"سمعت: {result}. للتوضيح، الطلب بتاريخ {details.get('date', 'غير محدد')}، "
            f"الساعة {details.get('time', 'غير محدد')}، لعدد {details.get('people', 'غير محدد')} من الأشخاص. "
            "هل الموعد متاح؟ أجب بنعم أو لا.",
            voice=_voice_name())
        gather = Gather(input="speech", timeout="10", speechTimeout="auto",
                        speechModel="phone_call", language="ar-SA",
                        hints="نعم، لا، متاح، غير متاح، مؤكد، ممتلئ",
                        action=f"/booking/respond?booking_id={booking_id}&followup=1",
                        method="POST")
        retry.append(gather)
        retry.say("لم تصل إجابة واضحة. سأرسل النتيجة للمراجعة.", voice=_voice_name())
        retry.hangup()
        return Response(retry.to_xml(), media_type="text/xml")

    booking = _booking_calls.pop(booking_id, None)
    if booking:
        status = booking.get("_conversation_status") or _booking_result_status(result)
        if booking.get("operation") == "cancel" and status == "confirmed":
            status = "canceled"
        update_booking(str(booking.get("history_id", "")), status, result)
        if status == "confirmed":
            _schedule_reminder(booking)
        _notify_owner_whatsapp(booking_id, booking.get("owner", ""),
                               f"الطلب: {booking.get('request', '')}\nالحالة: {status}\nالنتيجة: {result}",
                               heading="نتيجة حجز المطعم")
    resp = VoiceResponse(); resp.say("شكراً لكم، إلى اللقاء.", voice=_voice_name()); resp.hangup()
    return Response(resp.to_xml(), media_type="text/xml")


@app.post("/booking/status")
async def booking_status(req: Request) -> dict:
    """Notify the owner when the restaurant call ends without a speech result."""
    booking_id = req.query_params.get("booking_id", "")
    form = await req.form()
    status = (form.get("CallStatus") or "unknown").strip().lower()
    booking = _booking_calls.pop(booking_id, None)
    if booking and status != "completed":
        labels = {
            "busy": "الخط مشغول",
            "no-answer": "لم يرد المطعم",
            "failed": "فشل الاتصال بالمطعم",
            "canceled": "تم إلغاء الاتصال",
        }
        result = labels.get(status, f"انتهى الاتصال بالحالة: {status}")
        update_booking(str(booking.get("history_id", "")), status, result)
        _notify_owner_whatsapp(booking_id, booking.get("owner", ""),
                               f"الطلب: {booking.get('request', '')}\nالنتيجة: {result}",
                               heading="نتيجة حجز المطعم")
    return {"status": "ok"}


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
        language="ar-SA",
        hints="نعم، لا، متاح، غير متاح، الساعة، غداً، اليوم، شخص، أشخاص، حجز، تأكيد",
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
    _notify_owner_whatsapp(call_sid, caller, transcript)

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
        _notify_owner_whatsapp(call_sid, caller, transcript)
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
        "owner_whatsapp_configured": bool(CONFIG.owner_whatsapp_number),
        "twilio_configured": bool(CONFIG.twilio_account_sid and CONFIG.twilio_auth_token and CONFIG.twilio_from_number),
        "twilio_account_sid_configured": bool(CONFIG.twilio_account_sid),
        "twilio_account_sid_prefix": CONFIG.twilio_account_sid[:2] if CONFIG.twilio_account_sid else "",
        "twilio_account_sid_length": len(CONFIG.twilio_account_sid),
        "twilio_auth_token_configured": bool(CONFIG.twilio_auth_token),
        "twilio_auth_token_length": len(CONFIG.twilio_auth_token),
        "twilio_from_number_configured": bool(CONFIG.twilio_from_number),
    }


if __name__ == "__main__":
    os.environ.setdefault("PORT", "8000")
    import uvicorn
    uvicorn.run("main:app", host="0.0.0.0", port=int(os.environ["PORT"]))
