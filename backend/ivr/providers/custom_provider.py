"""Configurable "custom" provider.

Lets the deployment target another licensed Indian telephony provider that
speaks one of the supported markup dialects (Twilio-compatible, Plivo
compatible, or Exotel passthru XML) and exposes a REST API at
TELEPHONY_BASE_URL.

Endpoint templates can be overridden per deployment:
    TELEPHONY_RECORDING_PATH   default "/Recordings/{id}"
    TELEPHONY_SMS_PATH         default "/Sms"
    TELEPHONY_CALL_PATH        default "/Calls"
"""
import os

import requests

from ..config import settings
from .base import TelephonyProvider


class CustomProvider(TelephonyProvider):
    name = "custom"

    def __init__(self):
        self.serializer_flavor = (settings.TELEPHONY_MARKUP or "twilio_xml").lower()
        super().__init__()

    def _base(self):
        return (settings.TELEPHONY_BASE_URL or "").rstrip("/")

    @property
    def _auth(self):
        if settings.TELEPHONY_API_KEY and settings.TELEPHONY_API_SECRET:
            return (settings.TELEPHONY_API_KEY, settings.TELEPHONY_API_SECRET)
        if settings.TELEPHONY_ACCOUNT_ID and settings.TELEPHONY_AUTH_TOKEN:
            return (settings.TELEPHONY_ACCOUNT_ID, settings.TELEPHONY_AUTH_TOKEN)
        return None

    def configuration_errors(self):
        errs = []
        if not self._base():
            errs.append("TELEPHONY_BASE_URL is not set")
        if not settings.TELEPHONY_PHONE_NUMBER and not settings.IVR_PHONE_NUMBER:
            errs.append("TELEPHONY_PHONE_NUMBER (or IVR_PHONE_NUMBER) is not set")
        if not self._auth() and not settings.TELEPHONY_WEBHOOK_SECRET:
            errs.append("no credentials (TELEPHONY_API_KEY/SECRET or ACCOUNT_ID/AUTH_TOKEN) configured")
        return errs

    def fetch_recording(self, recording_id, url=None, timeout=60):
        target = url or f"{self._base()}{os.environ.get('TELEPHONY_RECORDING_PATH', '/Recordings/{id}').format(id=recording_id)}"
        resp = requests.get(target, auth=self._auth, timeout=timeout)
        resp.raise_for_status()
        return resp.content, resp.headers.get("Content-Type", "audio/mpeg")

    def send_sms(self, to_number, body, timeout=30):
        resp = requests.post(f"{self._base()}{os.environ.get('TELEPHONY_SMS_PATH', '/Sms')}",
                             auth=self._auth, timeout=timeout,
                             json={"from": settings.TELEPHONY_PHONE_NUMBER, "to": to_number, "body": body})
        resp.raise_for_status()
        return resp.json()

    def initiate_callback(self, to_number, answer_url=None, timeout=30):
        resp = requests.post(f"{self._base()}{os.environ.get('TELEPHONY_CALL_PATH', '/Calls')}",
                             auth=self._auth, timeout=timeout,
                             json={"from": settings.TELEPHONY_PHONE_NUMBER, "to": to_number,
                                   "url": answer_url or self.webhook_url("/ivr/voice")})
        resp.raise_for_status()
        return resp.json()
