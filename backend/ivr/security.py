"""
Security primitives for the IVR channel:

  * phone number normalisation (E.164), masking, hashing and optional
    authenticated encryption at rest
  * telephony webhook signature verification (Twilio / Plivo / static token /
    generic HMAC) with replay protection
  * idempotency bookkeeping for provider webhook events
  * rate limiting (durable, in the database so multiple workers share it)
  * signed, expiring tokens for the browser GPS consent link
"""
import base64
import hashlib
import hmac
import json
import os
import re
import time
from datetime import datetime, timedelta, timezone

from .config import settings

try:  # optional dependency - see requirements.txt
    from cryptography.fernet import Fernet, InvalidToken
except Exception:  # pragma: no cover
    Fernet = None
    InvalidToken = Exception


# --------------------------------------------------------------- phones ---
_DEFAULT_COUNTRY_CODE = "91"


def normalize_phone(raw, default_country=_DEFAULT_COUNTRY_CODE):
    """Normalise a caller/participant number to E.164 where possible.

    Returns (normalized_or_None, status) where status is one of
    CAPTURED / WITHHELD / NOT_AVAILABLE.
    """
    if raw is None:
        return None, "NOT_AVAILABLE"
    s = str(raw).strip()
    if not s:
        return None, "NOT_AVAILABLE"
    lowered = s.lower()
    if any(x in lowered for x in ("anonymous", "private", "withheld", "restricted", "unknown", "blocked")):
        return None, "WITHHELD"

    s = re.sub(r"[^\d+]", "", s)
    if s.startswith("+"):
        digits = "+" + re.sub(r"\D", "", s[1:])
    elif s.startswith("00"):
        digits = "+" + s[2:]
    else:
        digits = s

    if digits.startswith("+"):
        body = re.sub(r"\D", "", digits)
        if 8 <= len(body) <= 15:
            return "+" + body, "CAPTURED"
        return None, "WITHHELD"

    body = re.sub(r"\D", "", digits)
    if len(body) == 10 and body[0] in "6789":
        return f"+{default_country}{body}", "CAPTURED"
    if 11 <= len(body) <= 15:
        return "+" + body, "CAPTURED"
    if 0 < len(body) < 10:
        # Short codes, SIP extensions, or carrier-truncated CLIs: keep the
        # digits but do not pretend it is a valid E.164 number.
        return None, "WITHHELD"
    return None, "NOT_AVAILABLE"


def mask_phone(phone):
    if not phone:
        return ""
    digits = re.sub(r"\D", "", phone)
    if len(digits) < 4:
        return "****"
    return "+" + "*" * max(len(digits) - 4, 0) + digits[-4:]


def _hash_secret():
    return (settings.PHONE_HASH_SECRET or os.environ.get("SIH_SECRET_KEY", "") or "pashumitra-ivr").encode()


def phone_hash(phone):
    if not phone:
        return None
    return hmac.new(_hash_secret(), phone.encode(), hashlib.sha256).hexdigest()


def _fernet():
    key = settings.PHONE_ENCRYPTION_KEY
    if not key or Fernet is None:
        return None
    try:
        return Fernet(key.encode())
    except Exception:
        return None


def encryption_mode():
    return "fernet" if _fernet() is not None else "hash_only"


def encrypt_phone(phone):
    """Authenticated encryption of a phone number at rest.

    Without IVR_PHONE_ENCRYPTION_KEY configured the plaintext is NOT stored
    (only the HMAC-SHA256 lookup hash and a masked form), so a database leak
    does not expose farmer numbers.
    """
    if not phone:
        return None
    f = _fernet()
    if f is None:
        return None
    return f.encrypt(phone.encode()).decode()


def decrypt_phone(token):
    if not token:
        return None
    f = _fernet()
    if f is None:
        return None
    try:
        return f.decrypt(token.encode()).decode()
    except InvalidToken:
        return None
    except Exception:
        return None


def get_call_phone(conn, call_row):
    """Recover a caller number for authorised use only."""
    if not call_row:
        return None
    raw = call_row["caller_number_encrypted"] if "caller_number_encrypted" in call_row.keys() else None
    return decrypt_phone(raw)


# ------------------------------------------------------------ signatures ---
def _b64_digest(digest):
    return base64.b64encode(digest).decode()


def constant_time_eq(a, b):
    if a is None or b is None:
        return False
    return hmac.compare_digest(str(a), str(b))


def twilio_signature(url, params, auth_token):
    """Twilio: HMAC-SHA1 over url + sorted(key+value) pairs."""
    data = url + "".join(f"{k}{params[k]}" for k in sorted(params.keys()))
    return _b64_digest(hmac.new(auth_token.encode(), data.encode(), hashlib.sha1).digest())


def plivo_v2_signature(url, params, auth_token):
    return _b64_digest(hmac.new(auth_token.encode(), (url + "".join(f"{k}{params[k]}" for k in sorted(params))).encode(), hashlib.sha1).digest())


def plivo_v3_signature(url, nonce, auth_token):
    return _b64_digest(hmac.new(auth_token.encode(), (url + nonce).encode(), hashlib.sha256).digest())


def generic_signature(secret, timestamp, body):
    """Stripe style: HMAC-SHA256 over "<timestamp>.<body>"."""
    return hmac.new(secret.encode(), f"{timestamp}.{body}".encode(), hashlib.sha256).hexdigest()


def reconstruct_webhook_url(request, configured_base=None):
    """Rebuild the external URL the provider signed.

    Providers sign the public URL; behind a proxy request.url may contain the
    internal host, so IVR_PUBLIC_BASE_URL (or an X-Forwarded-* host) wins.
    """
    base = (configured_base or settings.PUBLIC_BASE_URL or "").rstrip("/")
    path = request.path
    if base:
        return base + path
    forwarded_host = request.headers.get("X-Forwarded-Host")
    scheme = request.headers.get("X-Forwarded-Proto") or request.scheme
    if forwarded_host:
        return f"{scheme}://{forwarded_host}{path}"
    return request.url.split("?", 1)[0]


class SignatureResult:
    def __init__(self, valid, reason="", detail=""):
        self.valid = valid
        self.reason = reason
        self.detail = detail

    def __bool__(self):
        return self.valid


def verify_webhook(request, provider, values, raw_body=None):
    """Verify an inbound provider webhook.

    Returns SignatureResult. When no verification material is configured the
    result is explicitly invalid unless IVR_REQUIRE_WEBHOOK_SIGNATURE=false -
    we never silently "pass" an unverifiable webhook in production.
    """
    url = reconstruct_webhook_url(request)
    name = (provider or "").lower()

    if name == "twilio":
        token = settings.TELEPHONY_AUTH_TOKEN
        if not token:
            return SignatureResult(False, "not_configured", "TELEPHONY_AUTH_TOKEN missing")
        sig = request.headers.get("X-Twilio-Signature", "")
        if not sig:
            return SignatureResult(False, "missing_signature", "X-Twilio-Signature header absent")
        expected = twilio_signature(url, values, token)
        return SignatureResult(constant_time_eq(sig, expected), "twilio", "" if constant_time_eq(sig, expected) else "signature mismatch")

    if name == "plivo":
        token = settings.TELEPHONY_AUTH_TOKEN
        if not token:
            return SignatureResult(False, "not_configured", "TELEPHONY_AUTH_TOKEN missing")
        v3 = request.headers.get("X-Plivo-Signature-V3")
        nonce = request.headers.get("X-Plivo-Signature-V3-Nonce", "")
        if v3:
            ok = constant_time_eq(v3, plivo_v3_signature(url, nonce, token))
            return SignatureResult(ok, "plivo_v3", "" if ok else "signature mismatch")
        v2 = request.headers.get("X-Plivo-Signature-V2") or request.headers.get("X-Plivo-Signature")
        if v2:
            ok = constant_time_eq(v2, plivo_v2_signature(url, values, token))
            return SignatureResult(ok, "plivo_v2", "" if ok else "signature mismatch")
        return SignatureResult(False, "missing_signature", "no Plivo signature header")

    if name in ("exotel", "custom"):
        # These providers do not universally sign callbacks. Supported
        # controls: shared secret header, then optional IP allowlist.
        secret = settings.TELEPHONY_WEBHOOK_SECRET
        if secret:
            header_sig = request.headers.get("X-Pashu-Signature") or request.headers.get("X-Exotel-Signature") or ""
            ts = request.headers.get("X-Pashu-Timestamp") or ""
            if ts:
                try:
                    if abs(time.time() - int(ts)) > settings.WEBHOOK_REPLAY_WINDOW:
                        return SignatureResult(False, "replay", "timestamp outside replay window")
                except ValueError:
                    return SignatureResult(False, "replay", "bad timestamp")
            if header_sig:
                body = raw_body if raw_body is not None else ""
                expected = generic_signature(secret, ts, body)
                ok = constant_time_eq(header_sig, expected)
                return SignatureResult(ok, "hmac", "" if ok else "signature mismatch")
            token_header = request.headers.get("X-Pashu-Token") or ""
            if token_header:
                ok = constant_time_eq(token_header, secret)
                return SignatureResult(ok, "static_token", "" if ok else "token mismatch")
            return SignatureResult(False, "missing_signature", "no signature/token header")
        allow = [x.strip() for x in os.environ.get("IVR_WEBHOOK_IP_ALLOWLIST", "").split(",") if x.strip()]
        if allow:
            ip = request.headers.get("X-Forwarded-For", "").split(",")[0].strip() or (request.remote_addr or "")
            ok = ip in allow
            return SignatureResult(ok, "ip_allowlist", "" if ok else f"ip {ip} not allowed")
        return SignatureResult(False, "not_configured",
                               "no TELEPHONY_WEBHOOK_SECRET / IVR_WEBHOOK_IP_ALLOWLIST configured")

    return SignatureResult(False, "unknown_provider", f"provider '{provider}' has no verifier")


# ----------------------------------------------------------- idempotency ---
def payload_fingerprint(values, raw_body=None):
    blob = raw_body if raw_body else json.dumps(values or {}, sort_keys=True, default=str)
    return hashlib.sha256(blob.encode("utf-8", "replace")).hexdigest()


def claim_webhook_event(conn, provider, event_id, event_type, values, raw_body=None,
                        signature_valid=0, replay_rejected=0):
    """Claim a provider event id. Returns (is_new, row_id).

    Re-delivery of the same provider event id returns is_new=False so the
    caller can short-circuit and avoid creating a duplicate report.
    """
    if not event_id:
        return True, None
    fp = payload_fingerprint(values, raw_body)
    try:
        cur = conn.execute(
            "INSERT INTO ivr_webhook_events (provider, provider_event_id, event_type, payload_hash, "
            "signature_valid, replay_rejected) VALUES (?,?,?,?,?,?)",
            (provider, str(event_id), event_type, fp, int(signature_valid), int(replay_rejected)),
        )
        conn.commit()
        return True, cur.lastrowid
    except Exception:
        return False, None


def mark_webhook_processed(conn, provider, event_id):
    try:
        conn.execute(
            "UPDATE ivr_webhook_events SET processed_at=datetime('now') WHERE provider=? AND provider_event_id=?",
            (provider, str(event_id)),
        )
        conn.commit()
    except Exception:
        pass


# ---------------------------------------------------------- rate limiting ---
def rate_limit(conn, bucket_key, limit=None):
    """Durable fixed-window rate limit. Returns True when the call is allowed."""
    limit = limit or settings.RATE_LIMIT_PER_MINUTE
    if limit <= 0:
        return True
    window = datetime.utcnow().strftime("%Y-%m-%dT%H:%M")
    row = conn.execute(
        "SELECT id, count FROM ivr_rate_limits WHERE bucket_key=? AND window_start=?", (bucket_key, window)
    ).fetchone()
    if row:
        if row["count"] >= limit:
            conn.execute("UPDATE ivr_rate_limits SET count=count+1 WHERE id=?", (row["id"],))
            conn.commit()
            return False
        conn.execute("UPDATE ivr_rate_limits SET count=count+1 WHERE id=?", (row["id"],))
        conn.commit()
        return True
    try:
        conn.execute("INSERT INTO ivr_rate_limits (bucket_key, window_start, count) VALUES (?,?,1)", (bucket_key, window))
        conn.commit()
    except Exception:
        return True
    return True


def purge_rate_limits(conn, older_than_minutes=10):
    cutoff = (datetime.utcnow() - timedelta(minutes=older_than_minutes)).strftime("%Y-%m-%dT%H:%M")
    try:
        conn.execute("DELETE FROM ivr_rate_limits WHERE window_start < ?", (cutoff,))
        conn.commit()
    except Exception:
        pass


# --------------------------------------------------------- signed tokens ---
def _token_secret():
    return (settings.LOCATION_TOKEN_SECRET or os.environ.get("SIH_SECRET_KEY", "") or "pashumitra-ivr-token").encode()


def sign_token(payload: dict, ttl_minutes=None):
    """Create a tamper-proof, expiring token (used for the GPS consent link)."""
    ttl = ttl_minutes if ttl_minutes is not None else settings.LOCATION_LINK_TTL_MINUTES
    body = dict(payload)
    body["exp"] = int((datetime.now(timezone.utc) + timedelta(minutes=ttl)).timestamp())
    raw = json.dumps(body, sort_keys=True, separators=(",", ":")).encode()
    b64 = base64.urlsafe_b64encode(raw).decode().rstrip("=")
    sig = hmac.new(_token_secret(), b64.encode(), hashlib.sha256).hexdigest()
    return f"{b64}.{sig}"


def verify_token(token: str):
    if not token or "." not in token:
        return None
    b64, sig = token.rsplit(".", 1)
    expected = hmac.new(_token_secret(), b64.encode(), hashlib.sha256).hexdigest()
    if not constant_time_eq(sig, expected):
        return None
    try:
        raw = base64.urlsafe_b64decode(b64 + "=" * (-len(b64) % 4))
        payload = json.loads(raw.decode())
    except Exception:
        return None
    if payload.get("exp") and int(payload["exp"]) < int(datetime.now(timezone.utc).timestamp()):
        return None
    return payload


def token_hash(token: str):
    return hashlib.sha256(token.encode()).hexdigest() if token else None
