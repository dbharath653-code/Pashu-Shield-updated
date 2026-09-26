"""Unified notification delivery with an honest provider boundary.

In-app notifications are persisted by the application. External providers are
never treated as successful unless the provider returned a successful response.
This module is deliberately dependency-light so the SQLite deployment keeps
working, while SMS can be backed by any HTTP provider that accepts the
configured JSON contract.
"""
from __future__ import annotations

import hashlib
import hmac
import logging
import os
import threading
import time
from dataclasses import dataclass
from typing import Any

import requests

log = logging.getLogger("pashu.notifications")


@dataclass
class DeliveryResult:
    channel: str
    status: str
    provider: str | None = None
    provider_id: str | None = None
    error: str | None = None
    attempts: int = 0


class SMSProvider:
    """Configurable HTTP SMS provider.

    The provider contract is intentionally generic: POST JSON to SMS_BASE_URL
    with to, from, message and client_reference. Deployments can point it at a
    provider adapter/gateway without changing application code. Credentials
    are read only from the environment and are never logged.
    """

    def __init__(self) -> None:
        self.base_url = os.environ.get("SMS_BASE_URL", "").strip()
        self.api_key = os.environ.get("SMS_API_KEY", "").strip()
        self.api_secret = os.environ.get("SMS_API_SECRET", "").strip()
        self.sender_id = os.environ.get("SMS_SENDER_ID", "").strip()
        self.provider_name = os.environ.get("SMS_PROVIDER", "").strip().lower()
        self.timeout = max(1, min(int(os.environ.get("SMS_TIMEOUT_SECONDS", "10")), 60))
        self.max_retries = max(0, min(int(os.environ.get("SMS_MAX_RETRIES", "2")), 5))
        self._rate_lock = threading.Lock()
        self._window_started = 0.0
        self._window_count = 0

    @property
    def configured(self) -> bool:
        return bool(self.provider_name and self.base_url and self.api_key and self.sender_id)

    def configuration(self) -> dict[str, Any]:
        return {
            "provider": self.provider_name or None,
            "configured": self.configured,
            "base_url_configured": bool(self.base_url),
            "sender_configured": bool(self.sender_id),
            "credentials_configured": bool(self.api_key),
        }

    def _rate_limit(self) -> None:
        limit = max(1, int(os.environ.get("SMS_RATE_LIMIT_PER_MINUTE", "60")))
        with self._rate_lock:
            now = time.monotonic()
            if now - self._window_started >= 60:
                self._window_started, self._window_count = now, 0
            if self._window_count >= limit:
                raise RuntimeError("SMS rate limit exceeded")
            self._window_count += 1

    def send(self, *, to: str, message: str, reference: str) -> DeliveryResult:
        if not self.configured:
            return DeliveryResult("sms", "NOT_CONFIGURED", provider=self.provider_name or None,
                                  error="SMS provider is not configured", attempts=0)
        if not to or not message:
            return DeliveryResult("sms", "FAILED", provider=self.provider_name,
                                  error="recipient and message are required", attempts=0)
        try:
            self._rate_limit()
        except RuntimeError as exc:
            return DeliveryResult("sms", "RATE_LIMITED", provider=self.provider_name,
                                  error=str(exc), attempts=0)

        payload = {"to": to, "from": self.sender_id, "message": message,
                   "client_reference": reference}
        headers = {"Content-Type": "application/json", "Accept": "application/json",
                   "X-Idempotency-Key": reference}
        if self.api_key:
            headers["X-API-Key"] = self.api_key
        if self.api_secret:
            # A provider may validate this HMAC; this avoids putting the secret
            # in the URL or logs and is useful for simple gateway adapters.
            headers["X-API-Signature"] = hmac.new(
                self.api_secret.encode(), reference.encode(), hashlib.sha256
            ).hexdigest()

        last_error = "provider request failed"
        for attempt in range(1, self.max_retries + 2):
            try:
                response = requests.post(self.base_url, json=payload, headers=headers,
                                         timeout=self.timeout)
                if 200 <= response.status_code < 300:
                    body = response.json() if response.content else {}
                    provider_id = body.get("id") or body.get("message_id") or body.get("sid")
                    return DeliveryResult("sms", "DELIVERED", self.provider_name,
                                          str(provider_id) if provider_id else None,
                                          attempts=attempt)
                last_error = f"provider returned HTTP {response.status_code}"
            except requests.RequestException as exc:
                last_error = str(exc)[:240]
            if attempt <= self.max_retries:
                time.sleep(min(2 ** (attempt - 1), 4))
        return DeliveryResult("sms", "FAILED", self.provider_name, error=last_error,
                              attempts=self.max_retries + 1)


def provider_health() -> dict[str, Any]:
    """Return configuration state; never claims that a provider is reachable."""
    return {"sms": SMSProvider().configuration(),
            "whatsapp": {"configured": bool(os.environ.get("WHATSAPP_API_URL") and
                                               os.environ.get("WHATSAPP_API_TOKEN"))},
            "push": {"configured": bool(os.environ.get("PUSH_API_URL") and
                                           os.environ.get("PUSH_API_KEY"))}}
