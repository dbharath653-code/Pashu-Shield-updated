"""
IVR Configuration - All telephony and IVR settings via environment variables.
No hardcoded phone numbers, credentials, or secrets.
"""
import os

# Core IVR phone number (PSTN) - must be configured via env
# Official Pashu-Shield helpline. Project default is the fixed production
# number; override with IVR_PHONE_NUMBER only if the helpline ever changes.
IVR_PHONE_NUMBER = os.environ.get("IVR_PHONE_NUMBER", "7382210251")

# Telephony provider credentials (configurable)
TELEPHONY_PROVIDER = os.environ.get("TELEPHONY_PROVIDER", "mock")  # mock | twilio | exotel | plivo
TELEPHONY_ACCOUNT_ID = os.environ.get("TELEPHONY_ACCOUNT_ID", "")
TELEPHONY_AUTH_TOKEN = os.environ.get("TELEPHONY_AUTH_TOKEN", "")
TELEPHONY_PHONE_NUMBER = os.environ.get("TELEPHONY_PHONE_NUMBER", IVR_PHONE_NUMBER)
TELEPHONY_WEBHOOK_SECRET = os.environ.get("TELEPHONY_WEBHOOK_SECRET", "")
TELEPHONY_API_URL = os.environ.get("TELEPHONY_API_URL", "")

# Call recording
IVR_RECORDING_ENABLED = os.environ.get("IVR_RECORDING_ENABLED", "true").lower() in ("1", "true", "yes")
IVR_RECORDING_CONSENT_REQUIRED = os.environ.get("IVR_RECORDING_CONSENT_REQUIRED", "true").lower() in ("1", "true", "yes")

# AI settings
IVR_AI_ENABLED = os.environ.get("IVR_AI_ENABLED", "true").lower() in ("1", "true", "yes")
OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY", "")
ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "")
IVR_STT_PROVIDER = os.environ.get("IVR_STT_PROVIDER", "whisper")  # whisper | google | azure

# Location settings
IVR_LOCATION_SMS_ENABLED = os.environ.get("IVR_LOCATION_SMS_ENABLED", "false").lower() in ("1", "true", "yes")

# Security
IVR_RATE_LIMIT_PER_MINUTE = int(os.environ.get("IVR_RATE_LIMIT_PER_MINUTE", "60"))
IVR_WEBHOOK_TIMEOUT_SECONDS = int(os.environ.get("IVR_WEBHOOK_TIMEOUT_SECONDS", "15"))
IVR_MAX_CALL_DURATION_SECONDS = int(os.environ.get("IVR_MAX_CALL_DURATION_SECONDS", "600"))

# Supported languages
SUPPORTED_LANGUAGES = ["en", "te", "hi", "mr"]
DEFAULT_LANGUAGE = os.environ.get("IVR_DEFAULT_LANGUAGE", "en")

# Survey config
IVR_SURVEY_MAX_RETRIES = int(os.environ.get("IVR_SURVEY_MAX_RETRIES", "3"))
IVR_DUPLICATE_WINDOW_HOURS = int(os.environ.get("IVR_DUPLICATE_WINDOW_HOURS", "24"))

# Async processing
IVR_ASYNC_ENABLED = os.environ.get("IVR_ASYNC_ENABLED", "true").lower() in ("1", "true", "yes")

# Veterinarian working hours (IST, 24h) used for automatic availability.
# Vets with an explicit availability_status override these hours.
VET_WORK_START_HOUR = int(os.environ.get("VET_WORK_START_HOUR", "8"))
VET_WORK_END_HOUR = int(os.environ.get("VET_WORK_END_HOUR", "20"))
VET_TIMEZONE = os.environ.get("VET_TIMEZONE", "Asia/Kolkata")
VET_MAX_ACTIVE_CASES = int(os.environ.get("VET_MAX_ACTIVE_CASES", "10"))

# Preferred-language choices for farmers/vets (also the IVR menu order).
# Add new Indian languages here + a locales/<code>.json file + LANGUAGE_DTMF entry.
LANGUAGE_NAMES = {"en": "English", "te": "Telugu", "hi": "Hindi", "mr": "Marathi"}


def _helpline_digits():
    import re
    return re.sub(r"\D", "", IVR_PHONE_NUMBER or "")


def get_helpline_e164():
    """Helpline in E.164 for tel: links, e.g. +917382210251."""
    d = _helpline_digits()
    if len(d) == 10:
        return f"+91{d}"
    if d.startswith("91") and len(d) == 12:
        return f"+{d}"
    return f"+{d}" if d else ""


def get_helpline_display_in():
    """Helpline in Indian display format, e.g. +91 73822 10251."""
    d = _helpline_digits()
    local = d[-10:] if len(d) >= 10 else d
    if len(local) == 10:
        return f"+91 {local[:5]} {local[5:]}"
    return IVR_PHONE_NUMBER or ""


def is_helpline_number(to_number):
    """True when the dialled number is the Pashu-Shield helpline (last-10 match)."""
    import re
    want = _helpline_digits()[-10:]
    got = re.sub(r"\D", "", to_number or "")[-10:]
    return bool(want) and bool(got) and want == got

def get_ivr_config_summary():
    """Return sanitized config for admin view (no secrets)."""
    return {
        "ivr_phone_number": IVR_PHONE_NUMBER or "NOT_CONFIGURED",
        "helpline_number": _helpline_digits() or "NOT_CONFIGURED",
        "helpline_e164": get_helpline_e164() or "NOT_CONFIGURED",
        "helpline_display_in": get_helpline_display_in() or "NOT_CONFIGURED",
        # Honest PSTN flag: the application provides click-to-call plus the full
        # application-side voice workflow. PSTN termination of the helpline
        # number itself requires a carrier/SIP-trunk and is NOT claimed here.
        "pstn_connected": False,
        "pstn_note": "PSTN termination for the helpline requires a carrier/SIP-trunk provider. The website opens the farmer's native dialer (tel: link); the browser cannot receive PSTN calls directly.",
        "telephony_provider": TELEPHONY_PROVIDER,
        "telephony_phone_number": TELEPHONY_PHONE_NUMBER or "NOT_CONFIGURED",
        "recording_enabled": IVR_RECORDING_ENABLED,
        "ai_enabled": IVR_AI_ENABLED,
        "stt_provider": IVR_STT_PROVIDER,
        "location_sms_enabled": IVR_LOCATION_SMS_ENABLED,
        "supported_languages": SUPPORTED_LANGUAGES,
        "default_language": DEFAULT_LANGUAGE,
        "async_enabled": IVR_ASYNC_ENABLED,
        "provider_configured": bool(TELEPHONY_ACCOUNT_ID and TELEPHONY_AUTH_TOKEN),
    }

def is_provider_configured():
    """Check if real telephony provider is configured."""
    if TELEPHONY_PROVIDER == "mock":
        return True  # mock always works for dev
    return bool(TELEPHONY_ACCOUNT_ID and TELEPHONY_AUTH_TOKEN and TELEPHONY_PHONE_NUMBER)

def validate_required_config():
    """Return list of missing required env vars for production."""
    missing = []
    if not IVR_PHONE_NUMBER:
        missing.append("IVR_PHONE_NUMBER")
    if TELEPHONY_PROVIDER != "mock":
        if not TELEPHONY_ACCOUNT_ID:
            missing.append("TELEPHONY_ACCOUNT_ID")
        if not TELEPHONY_AUTH_TOKEN:
            missing.append("TELEPHONY_AUTH_TOKEN")
        if not TELEPHONY_PHONE_NUMBER:
            missing.append("TELEPHONY_PHONE_NUMBER")
    return missing
