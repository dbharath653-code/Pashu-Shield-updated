"""
IVR Configuration - All telephony and IVR settings via environment variables.
No hardcoded phone numbers, credentials, or secrets.
"""
import os

# Core IVR phone number (PSTN) - must be configured via env
IVR_PHONE_NUMBER = os.environ.get("IVR_PHONE_NUMBER", "")

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

def get_ivr_config_summary():
    """Return sanitized config for admin view (no secrets)."""
    return {
        "ivr_phone_number": IVR_PHONE_NUMBER or "NOT_CONFIGURED",
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
