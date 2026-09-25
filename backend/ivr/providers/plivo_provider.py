"""Plivo Voice integration."""
import requests

from ..config import settings
from .base import IncomingCall, TelephonyProvider


class PlivoProvider(TelephonyProvider):
    name = "plivo"
    serializer_flavor = "plivo_xml"

    F_CALL_ID = "CallUUID"
    F_FROM = "From"
    F_TO = "To"
    F_STATUS = "CallStatus"
    F_DIGITS = "Digits"
    F_SPEECH = "Speech"
    F_CONFIDENCE = "Confidence"
    F_DURATION = "Duration"
    F_RECORDING_ID = "RecordingID"
    F_RECORDING_URL = "RecordUrl"
    F_RECORDING_DURATION = "RecordingDuration"
    F_RECORDING_STATUS = "RecordingStatus"
    F_TRANSCRIPT = "TranscriptionText"
    F_TRANSCRIPT_STATUS = "TranscriptionStatus"

    API_BASE = "https://api.plivo.com/v1/Account"

    def configuration_errors(self):
        errs = []
        if not settings.TELEPHONY_ACCOUNT_ID:
            errs.append("TELEPHONY_ACCOUNT_ID (Plivo auth ID) is not set")
        if not settings.TELEPHONY_AUTH_TOKEN:
            errs.append("TELEPHONY_AUTH_TOKEN (Plivo auth token) is not set")
        if not settings.TELEPHONY_PHONE_NUMBER:
            errs.append("TELEPHONY_PHONE_NUMBER (Plivo caller ID) is not set")
        return errs

    @property
    def _auth(self):
        return (settings.TELEPHONY_ACCOUNT_ID, settings.TELEPHONY_AUTH_TOKEN)

    def parse_incoming(self, values):
        return IncomingCall(
            provider=self.name,
            provider_call_id=values.get(self.F_CALL_ID) or values.get("CallUUID"),
            from_raw=values.get(self.F_FROM),
            to_raw=values.get(self.F_TO),
            status=values.get(self.F_STATUS),
            direction=values.get("Direction", "inbound"),
            raw=dict(values),
        )

    def parse_recording(self, values):
        ev = super().parse_recording(values)
        if not ev.recording_id:
            ev.recording_id = values.get("RecordingID") or values.get("RecordingSid")
        if not ev.url:
            ev.url = values.get("RecordUrl") or values.get("RecordingUrl")
        if ev.duration is None:
            raw = values.get("RecordingDuration") or values.get("Duration")
            try:
                ev.duration = int(raw) if raw is not None else None
            except (TypeError, ValueError):
                ev.duration = None
        return ev

    def fetch_recording(self, recording_id, url=None, timeout=60):
        target = url or f"{self.API_BASE}/{settings.TELEPHONY_ACCOUNT_ID}/Recording/{recording_id}/"
        resp = requests.get(target, auth=self._auth, timeout=timeout, allow_redirects=True)
        resp.raise_for_status()
        return resp.content, resp.headers.get("Content-Type", "audio/mpeg")

    def delete_recording(self, recording_id, timeout=30):
        resp = requests.delete(f"{self.API_BASE}/{settings.TELEPHONY_ACCOUNT_ID}/Recording/{recording_id}/",
                               auth=self._auth, timeout=timeout)
        return resp.status_code in (200, 204)

    def send_sms(self, to_number, body, timeout=30):
        resp = requests.post(
            f"{self.API_BASE}/{settings.TELEPHONY_ACCOUNT_ID}/Message/",
            auth=self._auth,
            timeout=timeout,
            json={"src": settings.TELEPHONY_PHONE_NUMBER, "dst": to_number, "text": body},
        )
        resp.raise_for_status()
        return resp.json()

    def initiate_callback(self, to_number, answer_url=None, timeout=30):
        resp = requests.post(
            f"{self.API_BASE}/{settings.TELEPHONY_ACCOUNT_ID}/Call/",
            auth=self._auth,
            timeout=timeout,
            json={
                "from": settings.TELEPHONY_PHONE_NUMBER,
                "to": to_number,
                "answer_url": answer_url or self.webhook_url("/ivr/voice"),
                "answer_method": "POST",
            },
        )
        resp.raise_for_status()
        return resp.json()
