"""Twilio Programmable Voice integration."""
import requests

from ..config import settings
from .base import TelephonyProvider


class TwilioProvider(TelephonyProvider):
    name = "twilio"
    serializer_flavor = "twilio_xml"

    F_CALL_ID = "CallSid"
    F_FROM = "From"
    F_TO = "To"
    F_STATUS = "CallStatus"
    F_DIGITS = "Digits"
    F_SPEECH = "SpeechResult"
    F_CONFIDENCE = "Confidence"
    F_DURATION = "CallDuration"
    F_RECORDING_ID = "RecordingSid"
    F_RECORDING_URL = "RecordingUrl"
    F_RECORDING_DURATION = "RecordingDuration"
    F_RECORDING_STATUS = "RecordingStatus"
    F_TRANSCRIPT = "TranscriptionText"
    F_TRANSCRIPT_STATUS = "TranscriptionStatus"

    API_BASE = "https://api.twilio.com/2010-04-01/Accounts"

    def configuration_errors(self):
        errs = []
        if not settings.TELEPHONY_ACCOUNT_ID:
            errs.append("TELEPHONY_ACCOUNT_ID (Twilio Account SID) is not set")
        if not settings.TELEPHONY_AUTH_TOKEN:
            errs.append("TELEPHONY_AUTH_TOKEN is not set")
        if not settings.TELEPHONY_PHONE_NUMBER:
            errs.append("TELEPHONY_PHONE_NUMBER (Twilio caller ID / IVR number) is not set")
        return errs

    @property
    def _auth(self):
        return (settings.TELEPHONY_ACCOUNT_ID, settings.TELEPHONY_AUTH_TOKEN)

    def fetch_recording(self, recording_id, url=None, timeout=60):
        """Download the recording media (Twilio requires basic auth)."""
        target = url or f"{self.API_BASE}/{settings.TELEPHONY_ACCOUNT_ID}/Recordings/{recording_id}.mp3"
        resp = requests.get(target, auth=self._auth, timeout=timeout)
        resp.raise_for_status()
        return resp.content, resp.headers.get("Content-Type", "audio/mpeg")

    def delete_recording(self, recording_id, timeout=30):
        target = f"{self.API_BASE}/{settings.TELEPHONY_ACCOUNT_ID}/Recordings/{recording_id}"
        resp = requests.delete(target, auth=self._auth, timeout=timeout)
        return resp.status_code in (200, 204)

    def send_sms(self, to_number, body, timeout=30):
        target = f"{self.API_BASE}/{settings.TELEPHONY_ACCOUNT_ID}/Messages.json"
        resp = requests.post(target, auth=self._auth, timeout=timeout, data={
            "To": to_number,
            "From": settings.TELEPHONY_PHONE_NUMBER,
            "Body": body,
        })
        resp.raise_for_status()
        return resp.json()

    def initiate_callback(self, to_number, answer_url=None, timeout=30):
        target = f"{self.API_BASE}/{settings.TELEPHONY_ACCOUNT_ID}/Calls.json"
        resp = requests.post(target, auth=self._auth, timeout=timeout, data={
            "To": to_number,
            "From": settings.TELEPHONY_PHONE_NUMBER,
            "Url": answer_url or self.webhook_url("/ivr/voice"),
        })
        resp.raise_for_status()
        return resp.json()
