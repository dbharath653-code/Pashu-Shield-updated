"""
Provider-neutral telephony abstraction.

    TelephonyProvider  (Twilio | Exotel | Plivo | Custom)
            |
      MarkupBuilder    (say / gather / dial / record / hangup / redirect)
            |
    MarkupSerializer   (twilio_xml | plivo_xml | exotel_xml)

The IVR controller only ever talks to `TelephonyProvider`, so swapping
provider = change environment variables, no code change.
"""
from xml.sax.saxutils import escape

from ..config import settings


# --------------------------------------------------------------- markup ---
class MarkupBuilder:
    """Provider-neutral IVR step builder."""

    def __init__(self):
        self.actions = []
        self._open_gather = None

    # -- speech ---------------------------------------------------------
    def say(self, text, voice=None, language=None, loop=1):
        self.actions.append(("say", {"text": text or "", "voice": voice, "language": language, "loop": loop}))
        return self

    def pause(self, seconds=1):
        self.actions.append(("pause", {"seconds": seconds}))
        return self

    def play(self, url):
        self.actions.append(("play", {"url": url}))
        return self

    # -- input ----------------------------------------------------------
    def gather_start(self, action_url, num_digits=1, timeout=None, finish_on_key="#",
                     input_modes=("dtmf",), speech_hints=None, speech_language=None,
                     speech_timeout=None, method="POST"):
        self.actions.append(("gather_start", {
            "action_url": action_url,
            "num_digits": num_digits,
            "timeout": timeout or settings.GATHER_TIMEOUT,
            "finish_on_key": finish_on_key,
            "input_modes": tuple(input_modes),
            "speech_hints": speech_hints or [],
            "speech_language": speech_language,
            "speech_timeout": speech_timeout or settings.SPEECH_TIMEOUT,
            "method": method,
        }))
        self._open_gather = True
        return self

    def gather_end(self):
        self.actions.append(("gather_end", {}))
        self._open_gather = None
        return self

    # -- call control ---------------------------------------------------
    def dial(self, target, action_url=None, timeout=None, caller_id=None, record=False, method="POST"):
        self.actions.append(("dial", {
            "target": target,
            "action_url": action_url,
            "timeout": timeout or settings.VET_RING_TIMEOUT,
            "caller_id": caller_id,
            "record": record,
            "method": method,
        }))
        return self

    def record(self, action_url=None, max_length=3600, play_beep=True, method="POST"):
        self.actions.append(("record", {"action_url": action_url, "max_length": max_length,
                                        "play_beep": play_beep, "method": method}))
        return self

    def hangup(self):
        self.actions.append(("hangup", {}))
        return self

    def redirect(self, url, method="POST"):
        self.actions.append(("redirect", {"url": url, "method": method}))
        return self


class MarkupSerializer:
    """Base XML serializer."""

    content_type = "application/xml"
    flavor = "generic_xml"

    def render(self, builder: MarkupBuilder) -> str:
        body = []
        i = 0
        actions = builder.actions
        while i < len(actions):
            op, kw = actions[i]
            if op == "gather_start":
                inner = []
                j = i + 1
                while j < len(actions) and actions[j][0] != "gather_end":
                    inner.append(actions[j])
                    j += 1
                body.append(self._gather(kw, inner))
                i = j + 1
                continue
            if op == "gather_end":
                i += 1
                continue
            body.append(self._simple(op, kw))
            i += 1
        return self._document("\n".join(x for x in body if x))

    # -- to be implemented by subclasses --------------------------------
    def _document(self, inner):
        raise NotImplementedError

    def _gather(self, kw, inner):
        raise NotImplementedError

    def _simple(self, op, kw):
        raise NotImplementedError

    # -- helpers ---------------------------------------------------------
    @staticmethod
    def _esc(text):
        return escape(str(text or ""), {'"': "&quot;"})

    @staticmethod
    def _attr(name, value):
        if value is None or value == "":
            return ""
        return f' {name}="{MarkupSerializer._esc(value)}"'


class TwilioSerializer(MarkupSerializer):
    flavor = "twilio_xml"

    def _document(self, inner):
        return '<?xml version="1.0" encoding="UTF-8"?>\n<Response>\n' + inner + "\n</Response>"

    def _simple(self, op, kw):
        if op == "say":
            return (f'<Say{self._attr("voice", kw.get("voice"))}{self._attr("language", kw.get("language"))}'
                    f'{self._attr("loop", kw.get("loop"))}>{self._esc(kw.get("text"))}</Say>')
        if op == "pause":
            return f'<Pause length="{int(kw.get("seconds") or 1)}"/>'
        if op == "play":
            return f'<Play>{self._esc(kw.get("url"))}</Play>'
        if op == "dial":
            dial_open = (f'<Dial{self._attr("action", kw.get("action_url"))}{self._attr("method", kw.get("method"))}'
                         f'{self._attr("timeout", kw.get("timeout"))}{self._attr("callerId", kw.get("caller_id"))}'
                         f'{self._attr("record", "record-from-answer-dual" if kw.get("record") else None)}>')
            return f"{dial_open}<Number>{self._esc(kw.get('target'))}</Number></Dial>"
        if op == "record":
            return (f'<Record{self._attr("action", kw.get("action_url"))}{self._attr("method", kw.get("method"))}'
                    f'{self._attr("maxLength", kw.get("max_length"))}'
                    f'{self._attr("playBeep", "true" if kw.get("play_beep") else "false")}/>')
        if op == "hangup":
            return "<Hangup/>"
        if op == "redirect":
            return f'<Redirect{self._attr("method", kw.get("method"))}>{self._esc(kw.get("url"))}</Redirect>'
        return ""

    def _gather(self, kw, inner):
        modes = "+".join(kw.get("input_modes") or ["dtmf"])
        hints = ",".join(kw.get("speech_hints") or []) or None
        inner_xml = "\n".join(self._simple(o, k) for o, k in inner)
        return (
            f'<Gather{self._attr("action", kw.get("action_url"))}{self._attr("method", kw.get("method"))}'
            f'{self._attr("numDigits", kw.get("num_digits"))}{self._attr("timeout", kw.get("timeout"))}'
            f'{self._attr("finishOnKey", kw.get("finish_on_key"))}{self._attr("input", modes)}'
            f'{self._attr("language", kw.get("speech_language"))}{self._attr("hints", hints)}'
            f'{self._attr("speechTimeout", kw.get("speech_timeout")) if "speech" in (kw.get("input_modes") or []) else ""}'
            f' actionOnEmptyResult="true">\n{inner_xml}\n</Gather>'
        )


class ExotelSerializer(MarkupSerializer):
    """Exotel passthru applet XML."""

    flavor = "exotel_xml"

    def _document(self, inner):
        return '<?xml version="1.0" encoding="UTF-8"?>\n<Response>\n' + inner + "\n</Response>"

    def _simple(self, op, kw):
        if op == "say":
            # Exotel <Say> honours language on the voice attribute for supported voices
            return (f'<Say{self._attr("voice", kw.get("voice"))}{self._attr("language", kw.get("language"))}>'
                    f'{self._esc(kw.get("text"))}</Say>')
        if op == "pause":
            return f'<Pause length="{int(kw.get("seconds") or 1)}"/>'
        if op == "play":
            return f'<Play>{self._esc(kw.get("url"))}</Play>'
        if op == "dial":
            return (f'<Dial{self._attr("action", kw.get("action_url"))}{self._attr("timeout", kw.get("timeout"))}'
                    f'{self._attr("callerId", kw.get("caller_id"))}'
                    f'{self._attr("record", "true" if kw.get("record") else None)}>'
                    f'<Number>{self._esc(kw.get("target"))}</Number></Dial>')
        if op == "record":
            return (f'<Record{self._attr("action", kw.get("action_url"))}'
                    f'{self._attr("maxLength", kw.get("max_length"))}'
                    f'{self._attr("playBeep", "true" if kw.get("play_beep") else "false")}/>')
        if op == "hangup":
            return "<Hangup/>"
        if op == "redirect":
            return f'<Redirect{self._attr("method", kw.get("method"))}>{self._esc(kw.get("url"))}</Redirect>'
        return ""

    def _gather(self, kw, inner):
        inner_xml = "\n".join(self._simple(o, k) for o, k in inner)
        return (
            f'<Gather{self._attr("action", kw.get("action_url"))}{self._attr("method", kw.get("method"))}'
            f'{self._attr("numDigits", kw.get("num_digits"))}{self._attr("timeout", kw.get("timeout"))}'
            f'{self._attr("finishOnKey", kw.get("finish_on_key"))}>\n{inner_xml}\n</Gather>'
        )


class PlivoSerializer(MarkupSerializer):
    flavor = "plivo_xml"

    def _document(self, inner):
        return '<?xml version="1.0" encoding="UTF-8"?>\n<Response>\n' + inner + "\n</Response>"

    def _simple(self, op, kw):
        if op == "say":
            return (f'<Speak{self._attr("voice", kw.get("voice"))}{self._attr("language", kw.get("language"))}'
                    f'{self._attr("loop", kw.get("loop"))}>{self._esc(kw.get("text"))}</Speak>')
        if op == "pause":
            return f'<Wait length="{int(kw.get("seconds") or 1)}"/>'
        if op == "play":
            return f'<Play>{self._esc(kw.get("url"))}</Play>'
        if op == "dial":
            return (f'<Dial{self._attr("action", kw.get("action_url"))}{self._attr("method", kw.get("method"))}'
                    f'{self._attr("timeout", kw.get("timeout"))}{self._attr("callerId", kw.get("caller_id"))}'
                    f'{self._attr("record", "true" if kw.get("record") else None)}>'
                    f'<Number>{self._esc(kw.get("target"))}</Number></Dial>')
        if op == "record":
            return (f'<Record{self._attr("action", kw.get("action_url"))}{self._attr("method", kw.get("method"))}'
                    f'{self._attr("maxLength", kw.get("max_length"))}'
                    f'{self._attr("playBeep", "true" if kw.get("play_beep") else "false")}/>')
        if op == "hangup":
            return "<Hangup/>"
        if op == "redirect":
            return f'<Redirect{self._attr("method", kw.get("method"))}>{self._esc(kw.get("url"))}</Redirect>'
        return ""

    def _gather(self, kw, inner):
        modes = set(kw.get("input_modes") or ["dtmf"])
        parts = []
        inner_xml = "\n".join(self._simple(o, k) for o, k in inner)
        if "speech" in modes:
            hints = ",".join(kw.get("speech_hints") or []) or None
            parts.append(
                f'<GetSpeech{self._attr("action", kw.get("action_url"))}{self._attr("method", kw.get("method"))}'
                f'{self._attr("timeout", kw.get("speech_timeout"))}{self._attr("language", kw.get("speech_language"))}'
                f'{self._attr("hints", hints)} engine="google"/>'
            )
        if "dtmf" in modes:
            parts.append(
                f'<GetDigits{self._attr("action", kw.get("action_url"))}{self._attr("method", kw.get("method"))}'
                f'{self._attr("numDigits", kw.get("num_digits"))}{self._attr("timeout", kw.get("timeout"))}'
                f'{self._attr("finishOnKey", kw.get("finish_on_key"))} retries="1"/>'
            )
        return (
            f'<GetInput{self._attr("action", kw.get("action_url"))}{self._attr("method", kw.get("method"))}'
            f' inputType="{" ".join(sorted(modes))}">\n' + "\n".join(parts) + f"\n{inner_xml}\n</GetInput>"
        )


SERIALIZERS = {
    "twilio_xml": TwilioSerializer,
    "exotel_xml": ExotelSerializer,
    "plivo_xml": PlivoSerializer,
}


def get_serializer(flavor):
    return (SERIALIZERS.get((flavor or "twilio_xml").lower()) or TwilioSerializer)()


# ------------------------------------------------------------- datatypes --
class IncomingCall:
    def __init__(self, provider, provider_call_id, from_raw, to_raw, status=None,
                 direction="inbound", country=None, network=None, raw=None):
        self.provider = provider
        self.provider_call_id = provider_call_id
        self.from_raw = from_raw
        self.to_raw = to_raw
        self.status = status
        self.direction = direction
        self.country = country
        self.network = network
        self.raw = raw or {}

    def __repr__(self):
        return f"<IncomingCall {self.provider}:{self.provider_call_id} from={self.from_raw}>"


class GatherInput:
    """Normalised result of one IVR input step."""

    def __init__(self, digits=None, speech=None, confidence=None, provider_call_id=None,
                 error=None, raw=None):
        self.digits = (digits or "").strip() or None
        self.speech = (speech or "").strip() or None
        self.confidence = confidence
        self.provider_call_id = provider_call_id
        self.error = error
        self.raw = raw or {}

    @property
    def has_input(self):
        return bool(self.digits or self.speech)

    @property
    def mode(self):
        if self.speech and not self.digits:
            return "SPEECH"
        if self.digits:
            return "DTMF"
        return "UNKNOWN"


class StatusEvent:
    def __init__(self, provider_call_id, status=None, duration=None, raw=None):
        self.provider_call_id = provider_call_id
        self.status = status
        self.duration = duration
        self.raw = raw or {}


class RecordingEvent:
    def __init__(self, provider_call_id, recording_id=None, url=None, duration=None,
                 status=None, raw=None):
        self.provider_call_id = provider_call_id
        self.recording_id = recording_id
        self.url = url
        self.duration = duration
        self.status = status
        self.raw = raw or {}


class ProviderError(Exception):
    pass


class ProviderNotConfigured(ProviderError):
    pass


# ------------------------------------------------------------- provider ---
class TelephonyProvider:
    """Base class every telephony integration implements."""

    name = "none"
    serializer_flavor = "twilio_xml"

    # provider specific parameter names (override in subclasses)
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

    def __init__(self):
        self.serializer = get_serializer(self.serializer_flavor)

    # -- configuration ----------------------------------------------------
    def configuration_errors(self):
        """Return a list of missing/invalid settings. Empty list == ready."""
        if not settings.TELEPHONY_PHONE_NUMBER and not settings.IVR_PHONE_NUMBER:
            return ["TELEPHONY_PHONE_NUMBER (or IVR_PHONE_NUMBER) is not set"]
        return []

    def is_configured(self):
        return not self.configuration_errors()

    def require_configured(self):
        errs = self.configuration_errors()
        if errs:
            raise ProviderNotConfigured("; ".join(errs))

    # -- webhook parsing ---------------------------------------------------
    def parse_incoming(self, values):
        return IncomingCall(
            provider=self.name,
            provider_call_id=values.get(self.F_CALL_ID),
            from_raw=values.get(self.F_FROM),
            to_raw=values.get(self.F_TO),
            status=values.get(self.F_STATUS),
            direction=values.get("Direction", "inbound"),
            country=values.get("FromCountry") or values.get("CallerCountry"),
            raw=dict(values),
        )

    def parse_gather(self, values):
        conf = values.get(self.F_CONFIDENCE)
        try:
            conf = float(conf) if conf is not None else None
        except (TypeError, ValueError):
            conf = None
        return GatherInput(
            digits=values.get(self.F_DIGITS),
            speech=values.get(self.F_SPEECH),
            confidence=conf,
            provider_call_id=values.get(self.F_CALL_ID),
            raw=dict(values),
        )

    def parse_status(self, values):
        dur = values.get(self.F_DURATION)
        try:
            dur = int(dur) if dur is not None else None
        except (TypeError, ValueError):
            dur = None
        return StatusEvent(
            provider_call_id=values.get(self.F_CALL_ID),
            status=values.get(self.F_STATUS),
            duration=dur,
            raw=dict(values),
        )

    def parse_recording(self, values):
        dur = values.get(self.F_RECORDING_DURATION)
        try:
            dur = int(dur) if dur is not None else None
        except (TypeError, ValueError):
            dur = None
        return RecordingEvent(
            provider_call_id=values.get(self.F_CALL_ID),
            recording_id=values.get(self.F_RECORDING_ID),
            url=values.get(self.F_RECORDING_URL),
            duration=dur,
            status=values.get(self.F_RECORDING_STATUS),
            raw=dict(values),
        )

    def parse_transcription(self, values):
        return {
            "provider_call_id": values.get(self.F_CALL_ID),
            "text": values.get(self.F_TRANSCRIPT) or "",
            "status": values.get(self.F_TRANSCRIPT_STATUS),
            "recording_id": values.get(self.F_RECORDING_ID),
            "raw": dict(values),
        }

    # -- rendering ---------------------------------------------------------
    def response(self):
        return MarkupBuilder()

    def render(self, builder):
        return self.serializer.content_type, self.serializer.render(builder)

    def webhook_url(self, path="/ivr/voice"):
        base = (settings.PUBLIC_BASE_URL or "").rstrip("/")
        return f"{base}{path}" if base else path

    # -- provider REST helpers (optional) ----------------------------------
    def fetch_recording(self, recording_id, url=None):
        raise ProviderNotConfigured(f"{self.name} recording download is not implemented")

    def delete_recording(self, recording_id):
        raise ProviderNotConfigured(f"{self.name} recording deletion is not implemented")

    def send_sms(self, to_number, body):
        raise ProviderNotConfigured(f"{self.name} SMS is not implemented")

    def initiate_callback(self, to_number, answer_url=None):
        raise ProviderNotConfigured(f"{self.name} outbound call is not implemented")
