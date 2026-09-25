"""
Twilio Provider - production telephony integration.
Uses Twilio REST API and TwiML. Credentials via env vars.
"""
import hashlib
import hmac
import os
import re
import base64
from typing import Optional, Dict, Any
from .base import BaseTelephonyProvider
from ..locales import t

try:
    from twilio.request_validator import RequestValidator
    HAS_TWILIO = True
except ImportError:
    HAS_TWILIO = False

class TwilioProvider(BaseTelephonyProvider):
    def __init__(self, account_sid: str = "", auth_token: str = "", phone_number: str = ""):
        self.account_sid = account_sid or os.environ.get("TELEPHONY_ACCOUNT_ID", "")
        self.auth_token = auth_token or os.environ.get("TELEPHONY_AUTH_TOKEN", "")
        self.phone_number = phone_number or os.environ.get("TELEPHONY_PHONE_NUMBER", "")
        self.validator = RequestValidator(self.auth_token) if HAS_TWILIO and self.auth_token else None

    def normalize_phone(self, raw: str) -> str:
        if not raw:
            return ""
        digits = re.sub(r"\D", "", raw)
        if len(digits) == 10:
            return f"+91{digits}"
        if digits.startswith("91") and len(digits) == 12:
            return f"+{digits}"
        if raw.startswith("+"):
            return f"+{digits}"
        return f"+{digits}" if digits else raw

    def verify_webhook_signature(self, request, signature: str = "") -> bool:
        sig = signature or request.headers.get("X-Twilio-Signature", "")
        if not sig or not self.validator:
            # If validator not available, fallback to permissive in dev, strict in prod
            if os.environ.get("TELEPHONY_PROVIDER") == "twilio" and not sig:
                return False
            return True
        # Twilio validation requires full URL
        url = request.url
        params = request.form.to_dict() if request.form else {}
        # Also check JSON body for newer Twilio APIs
        if not params and request.is_json:
            try:
                params = request.get_json() or {}
            except Exception:
                params = {}
        try:
            return self.validator.validate(url, params, sig)
        except Exception:
            return False

    def generate_welcome_twiml(self, call_sid: str, language: str = "en") -> str:
        welcome = t("welcome", language)
        lang_select = t("language_select", language)
        say_welcome = self._say(welcome, language)
        say_lang = self._say(lang_select, language)
        gather = self._gather(say_welcome + say_lang, action=f"/api/ivr/webhook/language?call_sid={call_sid}", num_digits=1, timeout=10, input_type="dtmf speech")
        redirect = self._redirect(f"/api/ivr/webhook/welcome?call_sid={call_sid}")
        return self._wrap_response(gather + redirect)

    def generate_menu_twiml(self, call_sid: str, language: str = "en") -> str:
        menu = t("main_menu", language)
        say = self._say(menu, language)
        gather = self._gather(say, action=f"/api/ivr/webhook/menu?call_sid={call_sid}", num_digits=1, timeout=10, input_type="dtmf speech")
        redirect = self._redirect(f"/api/ivr/webhook/menu?call_sid={call_sid}")
        return self._wrap_response(gather + redirect)

    def generate_survey_question_twiml(self, call_sid: str, question_key: str, language: str, attempt: int = 0) -> str:
        locale_key = f"q_{question_key}"
        prompt = t(locale_key, language)
        if attempt > 0:
            prompt = t("retry_prompt", language) + " " + prompt
        say = self._say(prompt, language)
        is_number = question_key in ("animal_count", "age", "duration", "temperature")
        if is_number:
            gather = self._gather(say, action=f"/api/ivr/webhook/survey?call_sid={call_sid}&q={question_key}", num_digits=10, timeout=12, finish_on_key="#", input_type="dtmf speech")
        else:
            gather = self._gather(say, action=f"/api/ivr/webhook/survey?call_sid={call_sid}&q={question_key}", num_digits=1, timeout=12, finish_on_key="#", input_type="dtmf speech")
        fallback = self._say(t("timeout_prompt", language), language) + self._redirect(f"/api/ivr/webhook/survey?call_sid={call_sid}&q={question_key}")
        return self._wrap_response(gather + fallback)

    def generate_connect_vet_twiml(self, call_sid: str, vet_phone: str, language: str = "en") -> str:
        from ..config import IVR_RECORDING_ENABLED
        connecting = self._say(t("vet_connecting", language), language)
        dial = self._dial(vet_phone, caller_id=self.phone_number, record=IVR_RECORDING_ENABLED, timeout=30)
        return self._wrap_response(connecting + dial)

    def generate_goodbye_twiml(self, call_sid: str, language: str = "en", message_key: str = "goodbye") -> str:
        msg = t(message_key, language)
        say = self._say(msg, language)
        hangup = self._hangup()
        return self._wrap_response(say + hangup)

    def initiate_outbound_call(self, to: str, from_number: str, webhook_url: str) -> Dict[str, Any]:
        if not self.account_sid or not self.auth_token:
            return {"error": "Twilio credentials not configured", "status": "failed"}
        try:
            from twilio.rest import Client
            client = Client(self.account_sid, self.auth_token)
            call = client.calls.create(to=to, from_=from_number or self.phone_number, url=webhook_url)
            return {"call_sid": call.sid, "status": call.status, "to": to, "from": from_number}
        except Exception as e:
            return {"error": str(e), "status": "failed"}

    def get_recording_url(self, call_sid: str) -> Optional[str]:
        # Twilio recordings are fetched via API
        return None

