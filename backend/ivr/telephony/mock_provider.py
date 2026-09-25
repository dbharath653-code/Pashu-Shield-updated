"""
MockProvider for local development and automated tests.
Simulates telephony without requiring real PSTN credentials.
WARNING: This is ONLY for dev/test - must not be presented as production telephony.
"""
import hashlib
import hmac
import os
import re
import uuid
from typing import Optional, Dict, Any
from .base import BaseTelephonyProvider
from ..locales import t

class MockTelephonyProvider(BaseTelephonyProvider):
    """Mock provider - generates realistic TwiML and validates webhooks in dev mode."""

    def __init__(self, webhook_secret: str = ""):
        self.webhook_secret = webhook_secret or os.environ.get("TELEPHONY_WEBHOOK_SECRET", "mock-secret")

    def normalize_phone(self, raw: str) -> str:
        if not raw:
            return ""
        # Strip all non-digits, handle +91 India numbers
        digits = re.sub(r"\D", "", raw)
        if len(digits) == 10:
            # Assume Indian 10-digit -> +91
            return f"+91{digits}"
        if digits.startswith("91") and len(digits) == 12:
            return f"+{digits}"
        if raw.startswith("+"):
            return f"+{digits}"
        return f"+{digits}" if digits else raw

    def verify_webhook_signature(self, request, signature: str = "") -> bool:
        # In mock mode, accept all if no secret configured, else check HMAC
        # For production mock tests, we allow bypass with header X-Mock-Bypass
        if request.headers.get("X-Mock-Bypass") == "true":
            return True
        if not self.webhook_secret or self.webhook_secret == "mock-secret":
            return True  # dev mode: permissive
        sig = signature or request.headers.get("X-Twilio-Signature") or request.headers.get("X-Webhook-Signature") or ""
        if not sig:
            return False
        # Simple HMAC check
        body = request.get_data(as_text=True) if hasattr(request, 'get_data') else ""
        expected = hmac.new(self.webhook_secret.encode(), body.encode(), hashlib.sha256).hexdigest()
        return hmac.compare_digest(expected, sig)

    def generate_welcome_twiml(self, call_sid: str, language: str = "en") -> str:
        welcome = t("welcome", language)
        lang_select = t("language_select", language)
        say_welcome = self._say(welcome, language)
        say_lang = self._say(lang_select, language)
        gather = self._gather(say_welcome + say_lang, action=f"/api/ivr/webhook/language?call_sid={call_sid}", num_digits=1, timeout=10, input_type="dtmf speech")
        # fallback if no input
        redirect = self._redirect(f"/api/ivr/webhook/welcome?call_sid={call_sid}")
        return self._wrap_response(gather + redirect)

    def generate_menu_twiml(self, call_sid: str, language: str = "en") -> str:
        menu = t("main_menu", language)
        say = self._say(menu, language)
        gather = self._gather(say, action=f"/api/ivr/webhook/menu?call_sid={call_sid}", num_digits=1, timeout=10, input_type="dtmf speech")
        redirect = self._redirect(f"/api/ivr/webhook/menu?call_sid={call_sid}")
        return self._wrap_response(gather + redirect)

    def generate_survey_question_twiml(self, call_sid: str, question_key: str, language: str, attempt: int = 0) -> str:
        # Map question_key to locale key
        locale_key = f"q_{question_key}"
        prompt = t(locale_key, language)
        # Add dtmf help on retry
        if attempt > 0:
            prompt = t("retry_prompt", language) + " " + prompt
        say = self._say(prompt, language)
        # Decide gather params based on question type
        # For number inputs, allow multiple digits ending with #
        is_number = question_key in ("animal_count", "age", "duration", "temperature")
        if is_number:
            gather = self._gather(say, action=f"/api/ivr/webhook/survey?call_sid={call_sid}&q={question_key}", num_digits=10, timeout=12, finish_on_key="#", input_type="dtmf speech")
        else:
            gather = self._gather(say, action=f"/api/ivr/webhook/survey?call_sid={call_sid}&q={question_key}", num_digits=1, timeout=12, finish_on_key="#", input_type="dtmf speech")
        # On no input, repeat
        fallback = self._say(t("timeout_prompt", language), language) + self._redirect(f"/api/ivr/webhook/survey?call_sid={call_sid}&q={question_key}")
        return self._wrap_response(gather + fallback)

    def generate_connect_vet_twiml(self, call_sid: str, vet_phone: str, language: str = "en") -> str:
        connecting = self._say(t("vet_connecting", language), language)
        # Also inform about recording if enabled
        try:
            from ..config import IVR_RECORDING_ENABLED, IVR_RECORDING_CONSENT_REQUIRED
            if IVR_RECORDING_ENABLED and IVR_RECORDING_CONSENT_REQUIRED:
                notice = self._say(t("recording_notice", language), language)
                connecting = notice + connecting
        except Exception:
            pass
        dial = self._dial(vet_phone, record=IVR_RECORDING_ENABLED if 'IVR_RECORDING_ENABLED' in dir() else False)
        return self._wrap_response(connecting + dial)

    def generate_goodbye_twiml(self, call_sid: str, language: str = "en", message_key: str = "goodbye") -> str:
        msg = t(message_key, language)
        say = self._say(msg, language)
        hangup = self._hangup()
        return self._wrap_response(say + hangup)

    def initiate_outbound_call(self, to: str, from_number: str, webhook_url: str) -> Dict[str, Any]:
        # Mock: generate a fake call sid
        mock_sid = f"MOCK-{uuid.uuid4().hex[:12].upper()}"
        return {"call_sid": mock_sid, "status": "queued", "to": to, "from": from_number, "provider": "mock"}

    def get_recording_url(self, call_sid: str) -> Optional[str]:
        return None

    def generate_vet_unavailable_twiml(self, call_sid: str, language: str = "en") -> str:
        msg = self._say(t("vet_unavailable", language), language)
        intro = self._say(t("survey_intro", language), language)
        # Redirect to first survey question
        redirect = self._redirect(f"/api/ivr/webhook/survey/start?call_sid={call_sid}")
        return self._wrap_response(msg + intro + redirect)

    def generate_recording_consent_twiml(self, call_sid: str, language: str = "en") -> str:
        notice = self._say(t("recording_notice", language), language)
        prompt = self._say(t("recording_consent_prompt", language), language)
        gather = self._gather(notice + prompt, action=f"/api/ivr/webhook/recording-consent?call_sid={call_sid}", num_digits=1, timeout=10, input_type="dtmf speech")
        return self._wrap_response(gather)
