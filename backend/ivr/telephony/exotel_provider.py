"""
Exotel Provider - Indian telecom provider integration (common for Indian IVR).
Uses Exotel API and AppML.
"""
import os
import re
import hashlib
import hmac
from typing import Optional, Dict, Any
from .base import BaseTelephonyProvider
from ..locales import t

class ExotelProvider(BaseTelephonyProvider):
    def __init__(self, account_sid: str = "", api_key: str = "", phone_number: str = ""):
        self.account_sid = account_sid or os.environ.get("TELEPHONY_ACCOUNT_ID", "")
        self.api_key = api_key or os.environ.get("TELEPHONY_AUTH_TOKEN", "")
        self.phone_number = phone_number or os.environ.get("TELEPHONY_PHONE_NUMBER", "")
        self.api_url = os.environ.get("TELEPHONY_API_URL", "https://api.exotel.com/v1/Accounts/{sid}/Calls/connect")

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
        # Exotel uses X-Exotel-Signature or basic auth
        sig = signature or request.headers.get("X-Exotel-Signature", "") or request.headers.get("X-Webhook-Signature", "")
        secret = os.environ.get("TELEPHONY_WEBHOOK_SECRET", "")
        if not secret:
            return True  # if not configured, permissive
        if not sig:
            return False
        body = request.get_data(as_text=True) if hasattr(request, 'get_data') else ""
        expected = hmac.new(secret.encode(), body.encode(), hashlib.sha256).hexdigest()
        return hmac.compare_digest(expected, sig)

    def generate_welcome_twiml(self, call_sid: str, language: str = "en") -> str:
        # Exotel uses similar XML but we emit Twilio-compatible TwiML which Exotel also supports via AppML bridge
        welcome = t("welcome", language)
        lang_select = t("language_select", language)
        say_welcome = self._say(welcome, language)
        say_lang = self._say(lang_select, language)
        gather = self._gather(say_welcome + say_lang, action=f"/api/ivr/webhook/language?call_sid={call_sid}", num_digits=1, timeout=10, input_type="dtmf")
        redirect = self._redirect(f"/api/ivr/webhook/welcome?call_sid={call_sid}")
        return self._wrap_response(gather + redirect)

    def generate_menu_twiml(self, call_sid: str, language: str = "en") -> str:
        menu = t("main_menu", language)
        say = self._say(menu, language)
        gather = self._gather(say, action=f"/api/ivr/webhook/menu?call_sid={call_sid}", num_digits=1, timeout=10, input_type="dtmf")
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
            gather = self._gather(say, action=f"/api/ivr/webhook/survey?call_sid={call_sid}&q={question_key}", num_digits=10, timeout=12, finish_on_key="#", input_type="dtmf")
        else:
            gather = self._gather(say, action=f"/api/ivr/webhook/survey?call_sid={call_sid}&q={question_key}", num_digits=1, timeout=12, finish_on_key="#", input_type="dtmf")
        fallback = self._say(t("timeout_prompt", language), language) + self._redirect(f"/api/ivr/webhook/survey?call_sid={call_sid}&q={question_key}")
        return self._wrap_response(gather + fallback)

    def generate_connect_vet_twiml(self, call_sid: str, vet_phone: str, language: str = "en") -> str:
        connecting = self._say(t("vet_connecting", language), language)
        dial = self._dial(vet_phone, caller_id=self.phone_number, record=False, timeout=30)
        return self._wrap_response(connecting + dial)

    def generate_goodbye_twiml(self, call_sid: str, language: str = "en", message_key: str = "goodbye") -> str:
        msg = t(message_key, language)
        say = self._say(msg, language)
        hangup = self._hangup()
        return self._wrap_response(say + hangup)

    def initiate_outbound_call(self, to: str, from_number: str, webhook_url: str) -> Dict[str, Any]:
        import requests, uuid
        # Mock if no credentials
        if not self.account_sid or not self.api_key:
            return {"call_sid": f"EXOTEL-MOCK-{uuid.uuid4().hex[:8]}", "status": "queued", "provider": "exotel-mock"}
        try:
            url = self.api_url.format(sid=self.account_sid)
            data = {"From": from_number or self.phone_number, "To": to, "CallerId": from_number or self.phone_number, "Url": webhook_url, "CallType": "trans"}
            resp = requests.post(url, data=data, auth=(self.account_sid, self.api_key), timeout=10)
            return {"status": "queued" if resp.status_code in (200, 201) else "failed", "response": resp.text[:500]}
        except Exception as e:
            return {"error": str(e), "status": "failed"}

    def get_recording_url(self, call_sid: str) -> Optional[str]:
        return None

