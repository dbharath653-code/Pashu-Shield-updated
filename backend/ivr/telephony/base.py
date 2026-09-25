"""
Base Telephony Provider abstraction.
Allows swapping Twilio / Exotel / Plivo / Mock without changing IVR logic.
"""
from abc import ABC, abstractmethod
from typing import Optional, Dict, Any

class TelephonyResponse:
    """Normalized response for TwiML-like voice instructions.
    Providers return provider-specific XML/JSON, but IVR logic uses this abstraction.
    """
    def __init__(self, twiml: str = "", action: str = "", status: str = "ok"):
        self.twiml = twiml
        self.action = action
        self.status = status

class BaseTelephonyProvider(ABC):
    """Abstract provider - all concrete providers must implement these."""

    @abstractmethod
    def generate_welcome_twiml(self, call_sid: str, language: str = "en") -> str:
        """Generate TwiML/voice XML for welcome + language selection."""
        pass

    @abstractmethod
    def generate_menu_twiml(self, call_sid: str, language: str = "en") -> str:
        """Generate main menu TwiML."""
        pass

    @abstractmethod
    def generate_survey_question_twiml(self, call_sid: str, question_key: str, language: str, attempt: int = 0) -> str:
        """Generate TwiML for a specific survey question."""
        pass

    @abstractmethod
    def generate_connect_vet_twiml(self, call_sid: str, vet_phone: str, language: str = "en") -> str:
        """Generate TwiML to bridge farmer to vet."""
        pass

    @abstractmethod
    def generate_goodbye_twiml(self, call_sid: str, language: str = "en", message_key: str = "goodbye") -> str:
        pass

    @abstractmethod
    def verify_webhook_signature(self, request, signature: str = "") -> bool:
        """Verify webhook authenticity (HMAC / signature)."""
        pass

    @abstractmethod
    def normalize_phone(self, raw: str) -> str:
        """Normalize phone to E.164-like format."""
        pass

    @abstractmethod
    def initiate_outbound_call(self, to: str, from_number: str, webhook_url: str) -> Dict[str, Any]:
        """Initiate outbound call (for vet bridging / callbacks)."""
        pass

    @abstractmethod
    def get_recording_url(self, call_sid: str) -> Optional[str]:
        pass

    def provider_name(self) -> str:
        return self.__class__.__name__

    # Helper: build TwiML verb strings (provider-agnostic, Twilio-compatible)
    def _say(self, text: str, language: str = "en", voice: str = "alice") -> str:
        # Use SSML-friendly escaping
        safe = text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
        # Map language to voice locale hint if needed
        return f'<Say voice="{voice}" language="{language}">{safe}</Say>'

    def _gather(self, inner: str, action: str, num_digits: int = 1, timeout: int = 8, finish_on_key: str = "#", input_type: str = "dtmf speech") -> str:
        return f'<Gather action="{action}" numDigits="{num_digits}" timeout="{timeout}" finishOnKey="{finish_on_key}" input="{input_type}">{inner}</Gather>'

    def _redirect(self, url: str) -> str:
        return f'<Redirect>{url}</Redirect>'

    def _hangup(self) -> str:
        return '<Hangup/>'

    def _dial(self, number: str, caller_id: str = "", record: bool = False, timeout: int = 30) -> str:
        attrs = f' callerId="{caller_id}"' if caller_id else ""
        if record:
            attrs += ' record="record-from-answer"'
        attrs += f' timeout="{timeout}"'
        return f'<Dial{attrs}><Number>{number}</Number></Dial>'

    def _wrap_response(self, inner: str) -> str:
        return f'<?xml version="1.0" encoding="UTF-8"?><Response>{inner}</Response>'

