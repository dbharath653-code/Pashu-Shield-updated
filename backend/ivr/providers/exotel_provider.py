"""Exotel (Indian telephony provider) integration.

Exotel posts passthru callbacks with slightly different parameter names
depending on the applet version, therefore every field is read through a
list of aliases.
"""
import requests

from ..config import settings
from .base import IncomingCall, TelephonyProvider


class ExotelProvider(TelephonyProvider):
    name = "exotel"
    serializer_flavor = "exotel_xml"

    F_CALL_ID = "CallSid"
    F_FROM = "From"
    F_TO = "To"
    F_STATUS = "Status"
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

    @staticmethod
    def _first(values, keys):
        for k in keys:
            v = values.get(k)
            if v not in (None, ""):
                return v
        return None

    def _pick(self, values, base, aliases):
        return self._first(values, [base] + aliases)

    def parse_incoming(self, values):
        return IncomingCall(
            provider=self.name,
            provider_call_id=self._pick(values, self.F_CALL_ID, ["CallUUID", "CallSid"]),
            from_raw=self._pick(values, self.F_FROM, ["CallFrom", "Caller", "From"]),
            to_raw=self._pick(values, self.F_TO, ["CallTo", "DialedNumber", "To", "ExotelNumber"]),
            status=self._pick(values, self.F_STATUS, ["CallStatus", "Status"]),
            direction=values.get("Direction", "inbound"),
            country=values.get("FromCountry"),
            raw=dict(values),
        )

    def parse_gather(self, values):
        g = super().parse_gather(values)
        if not g.digits:
            g.digits = self._first(values, ["Digits", "dtmf", "DtmfDigits"])
        if not g.speech:
            g.speech = self._first(values, ["SpeechResult", "Speech"])
        return g

    def parse_status(self, values):
        ev = super().parse_status(values)
        if ev.duration is None:
            raw = self._first(values, ["CallDuration", "Duration", "ConversationDuration"])
            try:
                ev.duration = int(raw) if raw is not None else None
            except (TypeError, ValueError):
                ev.duration = None
        return ev

    @property
    def _api_base(self):
        sub = (settings.TELEPHONY_SUBDOMAIN or "api.exotel.com").strip()
        if not sub.startswith("http"):
            sub = f"https://{sub}"
        return f"{sub.rstrip('/')}/v1/Accounts/{settings.TELEPHONY_SID}"

    @property
    def _auth(self):
        # Exotel accepts API key/secret or the account SID + auth token.
        if settings.TELEPHONY_API_KEY and settings.TELEPHONY_API_SECRET:
            return (settings.TELEPHONY_API_KEY, settings.TELEPHONY_API_SECRET)
        return (settings.TELEPHONY_SID, settings.TELEPHONY_AUTH_TOKEN)

    def configuration_errors(self):
        errs = []
        if not settings.TELEPHONY_SID:
            errs.append("TELEPHONY_SID (Exotel account SID) is not set")
        if not (settings.TELEPHONY_API_KEY and settings.TELEPHONY_API_SECRET) and not settings.TELEPHONY_AUTH_TOKEN:
            errs.append("TELEPHONY_API_KEY/TELEPHONY_API_SECRET (or TELEPHONY_AUTH_TOKEN) is not set")
        if not settings.TELEPHONY_PHONE_NUMBER:
            errs.append("TELEPHONY_PHONE_NUMBER (Exotel virtual number / caller ID) is not set")
        return errs

    def fetch_recording(self, recording_id, url=None, timeout=60):
        target = url or f"{self._api_base}/Recordings/{recording_id}.mp3"
        resp = requests.get(target, auth=self._auth, timeout=timeout)
        resp.raise_for_status()
        return resp.content, resp.headers.get("Content-Type", "audio/mpeg")

    def delete_recording(self, recording_id, timeout=30):
        resp = requests.delete(f"{self._api_base}/Recordings/{recording_id}", auth=self._auth, timeout=timeout)
        return resp.status_code in (200, 204)

    def send_sms(self, to_number, body, timeout=30):
        resp = requests.post(f"{self._api_base}/Sms/send.json", auth=self._auth, timeout=timeout, data={
            "From": settings.TELEPHONY_PHONE_NUMBER,
            "To": to_number,
            "Body": body,
        })
        resp.raise_for_status()
        return resp.json()

    def initiate_callback(self, to_number, answer_url=None, timeout=30):
        resp = requests.post(f"{self._api_base}/Calls/connect.json", auth=self._auth, timeout=timeout, data={
            "From": settings.TELEPHONY_PHONE_NUMBER,
            "To": to_number,
            "CallerId": settings.TELEPHONY_PHONE_NUMBER,
            "Url": answer_url or self.webhook_url("/ivr/voice"),
        })
        resp.raise_for_status()
        return resp.json()
