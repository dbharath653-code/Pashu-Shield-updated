"""
SIPProvider: telephony provider for the self-hosted Asterisk PBX voice gateway.

The PBX (pbx/) receives the real carrier-SIP call for the helpline, drives the
existing IVR webhooks, and executes the returned TwiML (Say/Gather/Dial/
Redirect/Hangup). No IVR business logic lives in the PBX.

This provider therefore reuses the exact same TwiML generation as the mock
provider (no duplication, no drift), and only changes the security boundary:
  - webhook verification is STRICT (shared gateway secret or HMAC; no bypass)
  - outbound calls are NOT supported: the helpline is INBOUND ONLY. Vet legs
    are placed by the PBX inside the live call and only after the backend
    authorizes the exact selected vet number (services/gateway).
  - recordings stay on the PBX host (see docs/PSTN_SIP_PBX.md); no URL here.
"""
import hashlib
import hmac
import os
from typing import Optional, Dict, Any
from .mock_provider import MockTelephonyProvider


class SIPProvider(MockTelephonyProvider):
    """Strict, inbound-only provider for the self-hosted PBX gateway."""

    def __init__(self, webhook_secret: str = ""):
        self.webhook_secret = (
            webhook_secret
            or os.environ.get("PBX_WEBHOOK_SECRET", "")
            or os.environ.get("TELEPHONY_WEBHOOK_SECRET", "")
        )

    def provider_name(self) -> str:
        return "SIPProvider"

    def verify_webhook_signature(self, request, signature: str = "") -> bool:
        # STRICT: no bypass headers, no permissive dev fallback. The PBX
        # gateway authenticates every webhook call with the shared secret.
        if not self.webhook_secret:
            return False  # fail closed: no secret configured -> reject
        try:
            presented = request.headers.get("X-PBX-Secret") or ""
        except Exception:
            presented = ""
        if presented and hmac.compare_digest(presented, self.webhook_secret):
            return True
        sig = (
            signature
            or request.headers.get("X-Webhook-Signature")
            or request.headers.get("X-PBX-Signature")
            or ""
        )
        if not sig:
            return False
        try:
            body = request.get_data(as_text=True) if hasattr(request, "get_data") else ""
        except Exception:
            body = ""
        expected = hmac.new(self.webhook_secret.encode(), body.encode(), hashlib.sha256).hexdigest()
        try:
            return hmac.compare_digest(expected, sig)
        except Exception:
            return False

    def initiate_outbound_call(self, to: str, from_number: str, webhook_url: str) -> Dict[str, Any]:
        # INBOUND ONLY by design: the helpline must never become an open
        # outbound calling gateway. Vet legs are dialled by the PBX inside an
        # answered inbound call, only after per-call backend authorization.
        return {
            "status": "unsupported",
            "reason": "inbound-only: outbound PSTN calls are disabled; "
                      "vet legs are bridged by the PBX inside live inbound calls",
            "to": to,
            "from": from_number,
            "provider": "sip",
        }

    def get_recording_url(self, call_sid: str) -> Optional[str]:
        # MVP: recordings are stored on the PBX host with retention policy
        # (see pbx/ + docs/PSTN_SIP_PBX.md), not fetched by the backend.
        return None
