"""Combined public service for Whapi webhooks and Twilio Voice."""
from fastapi import FastAPI

from main import app as twilio_app
from whapi_whatsapp import app as whapi_app

app = FastAPI(title="AI Assistant Gateway")
# Preserve the existing Whapi root webhook while adding Twilio voice routes.
app.include_router(twilio_app.router)
app.include_router(whapi_app.router)
