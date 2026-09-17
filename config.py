"""Central configuration loaded from environment / .env file."""
import os
from dotenv import load_dotenv

load_dotenv()


def _enabled(val: str | None, default: bool) -> bool:
    if val is None:
        return default
    return val.strip().lower() in {"1", "true", "yes", "on"}


class Config:
    # LLM
    ai_provider: str = os.getenv("AI_PROVIDER", "openai").lower()
    openai_key: str = os.getenv("OPENAI_API_KEY", "")
    gemini_key: str = os.getenv("GEMINI_API_KEY", "")

    # Behaviour
    language: str = os.getenv("LANGUAGE", "ar").lower()
    tone: str = os.getenv("TONE", "friendly").lower()
    user_name: str = os.getenv("USER_NAME", "")

    # Twilio
    twilio_account_sid: str = os.getenv("TWILIO_ACCOUNT_SID", "")
    twilio_auth_token: str = os.getenv("TWILIO_AUTH_TOKEN", "")
    twilio_from_number: str = os.getenv("TWILIO_FROM_NUMBER", "")
    owner_whatsapp_number: str = os.getenv("OWNER_WHATSAPP_NUMBER", "")
    public_base_url: str = os.getenv("PUBLIC_BASE_URL", "https://whatsapp-secretary-mrmm.onrender.com").rstrip("/")

    # Whapi.Cloud (WhatsApp assistant)
    whapi_token: str = os.getenv("WHAPI_TOKEN", "")
    voice_replies: bool = _enabled(os.getenv("VOICE_REPLIES"), True)
    # Debug: on failure, put the LLM error text into the reply (for diagnosing)
    debug: bool = _enabled(os.getenv("DEBUG"), False)
    twilio_webhook_secret: str = os.getenv("TWILIO_WEBHOOK_SECRET", "")
    whapi_webhook_secret: str = os.getenv("WHAPI_WEBHOOK_SECRET", "")

    # Optional: write every handled call transcript into this file (JSON lines)
    transcript_file: str = os.getenv("TRANSCRIPT_FILE", "transcripts.jsonl")

    @property
    def has_openai(self) -> bool:
        return self.ai_provider == "openai" and bool(self.openai_key)

    @property
    def has_gemini(self) -> bool:
        return self.ai_provider == "gemini" and bool(self.gemini_key)

    @property
    def has_llm(self) -> bool:
        return self.has_openai or self.has_gemini


CONFIG = Config()
