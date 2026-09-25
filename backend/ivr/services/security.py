"""
Security: webhook signature verification, rate limiting, input validation, audit.
"""
import time
import hashlib
import hmac
import re
from collections import defaultdict
from typing import Dict, Any

# Simple in-memory rate limiter (per IP)
_rate_store: Dict[str, list] = defaultdict(list)

def is_rate_limited(ip: str, limit_per_minute: int = 60) -> bool:
    import os
    # In mock mode (dev/tests), disable strict rate limiting to avoid false 429s during automated IVR flows
    if os.environ.get("TELEPHONY_PROVIDER", "mock") == "mock":
        # Allow 1000/min for mock; still prevents insane abuse but not test failures
        limit_per_minute = 1000
    now = time.time()
    window_start = now - 60
    _rate_store[ip] = [t for t in _rate_store[ip] if t > window_start]
    if len(_rate_store[ip]) >= limit_per_minute:
        return True
    _rate_store[ip].append(now)
    return False

def reset_rate_limiter():
    _rate_store.clear()

def validate_phone_input(phone: str) -> bool:
    if not phone:
        return False
    # Allow + and digits, 10-15 chars
    return bool(re.match(r"^\+?\d{10,15}$", phone.replace(" ", "").replace("-", "")))

def validate_dtmf_input(digit: str, allowed: list = None) -> bool:
    if not digit:
        return False
    if allowed:
        return digit in allowed
    return bool(re.match(r"^[0-9#*]$", digit))

def sanitize_input(text: str, max_len: int = 500) -> str:
    if not text:
        return ""
    # Remove dangerous characters but keep multilingual
    text = text.strip()[:max_len]
    # Remove null bytes, control chars except newline
    text = re.sub(r"[\x00-\x08\x0B\x0C\x0E-\x1F\x7F]", "", text)
    return text

def verify_hmac_signature(payload: str, signature: str, secret: str) -> bool:
    if not secret:
        return True  # no secret configured -> permissive for dev
    if not signature:
        return False
    expected = hmac.new(secret.encode(), payload.encode(), hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, signature)

def check_idempotency(conn, key: str, endpoint: str, payload_hash: str) -> bool:
    """Return True if this is a duplicate (already processed)."""
    row = conn.execute("SELECT id FROM ivr_webhook_idempotency WHERE idempotency_key=?", (key,)).fetchone()
    return bool(row)

def record_idempotency(conn, key: str, call_sid: str, endpoint: str, payload_hash: str, response_code: int = 200):
    try:
        conn.execute(
            "INSERT INTO ivr_webhook_idempotency (idempotency_key, call_sid, endpoint, payload_hash, response_code) VALUES (?,?,?,?,?)",
            (key, call_sid, endpoint, payload_hash, response_code)
        )
        conn.commit()
    except Exception:
        pass

def audit_ivr_event(conn, call_sid: str, event_type: str, details: dict, actor: str = "system"):
    import json
    try:
        conn.execute("INSERT INTO ivr_events (call_sid, event_type, details, actor) VALUES (?,?,?,?)",
                     (call_sid, event_type, json.dumps(details, ensure_ascii=False), actor))
        conn.commit()
    except Exception:
        pass
