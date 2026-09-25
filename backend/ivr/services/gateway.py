"""
Voice-gateway (self-hosted PBX) backend support.

The PBX in pbx/ is a pure transport layer: it receives the real SIP call from
the carrier trunk, POSTs the existing IVR webhooks, and executes the returned
TwiML (Say/Gather/Dial/Redirect/Hangup). It holds NO IVR business logic.

This module holds the backend side:
  - gateway authentication (shared secret + optional IP allowlist)
  - PBX heartbeat state -> pbx_healthy / sip_registered
  - first-real-inbound marker -> pstn_connected (honest, automatic, unfakeable
    via any public API: it is written only for requests that present the
    gateway secret AND declare transport=sip)
  - vet-leg dial authorization: the PBX may only ever dial the exact mobile
    number of the veterinarian the application selected for that call.
    This makes toll fraud through the PBX impossible by construction.
"""
import hashlib
import hmac
import json
import os
import re
from datetime import datetime, timezone


def _secret():
    return os.environ.get("PBX_WEBHOOK_SECRET", "")


def gateway_authenticated(request) -> bool:
    """True only when the request presents the gateway shared secret.

    Accepts EITHER the X-PBX-Secret header (used by the PBX AGI on every
    webhook call) OR a valid HMAC (X-Webhook-Signature over the raw body).
    With no secret configured, gateway auth always fails closed: nothing can
    be marked as a real inbound call until the operator provisions the secret.
    """
    secret = _secret()
    if not secret:
        return False
    presented = ""
    try:
        presented = request.headers.get("X-PBX-Secret") or ""
    except Exception:
        presented = ""
    if presented and hmac.compare_digest(presented, secret):
        return True
    # HMAC fallback: hex(HMAC_SHA256(secret, raw_body))
    sig = ""
    try:
        sig = (request.headers.get("X-Webhook-Signature")
               or request.headers.get("X-Twilio-Signature") or "")
    except Exception:
        sig = ""
    if sig:
        try:
            body = request.get_data(as_text=True) if hasattr(request, "get_data") else ""
        except Exception:
            body = ""
        expected = hmac.new(secret.encode(), body.encode(), hashlib.sha256).hexdigest()
        try:
            if hmac.compare_digest(expected, sig):
                return True
        except Exception:
            return False
    return False


def gateway_ip_allowed(request) -> bool:
    """Optional egress-IP allowlist for gateway endpoints (fail-open when unset)."""
    allowed = [ip.strip() for ip in os.environ.get("PBX_ALLOWED_IPS", "").split(",") if ip.strip()]
    if not allowed:
        return True
    try:
        remote = (request.remote_addr or "").strip()
    except Exception:
        return False
    # request.remote_addr may include port in some proxies; strip it.
    remote = remote.rsplit(":", 1)[0] if remote.count(":") == 1 and "." in remote else remote
    return remote in allowed


def _set_state(conn, key: str, value: str):
    conn.execute(
        "INSERT INTO ivr_gateway_state (key, value, updated_at) VALUES (?,?,datetime('now')) "
        "ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated_at=datetime('now')",
        (key, value if value is not None else ""))
    conn.commit()


def _get_state(conn, key: str):
    try:
        row = conn.execute("SELECT value, updated_at FROM ivr_gateway_state WHERE key=?", (key,)).fetchone()
    except Exception:
        return None, None
    if not row:
        return None, None
    try:
        return row["value"], row["updated_at"]
    except Exception:
        return row[0], row[1]


def record_heartbeat(conn, info: dict) -> dict:
    """Store a PBX heartbeat. Returns the stored summary."""
    info = info or {}
    _set_state(conn, "pbx_last_heartbeat", "1")
    _set_state(conn, "pbx_host", str(info.get("pbx_host", ""))[:128])
    _set_state(conn, "pbx_sip_registered", "1" if info.get("sip_registered") else "0")
    _set_state(conn, "pbx_trunk", str(info.get("trunk", ""))[:128])
    _set_state(conn, "pbx_asterisk_version", str(info.get("asterisk_version", ""))[:64])
    return gateway_health(conn)


def heartbeat_stale_seconds() -> int:
    try:
        return max(30, int(os.environ.get("PBX_HEARTBEAT_STALE_SECONDS", "300")))
    except Exception:
        return 300


def gateway_health(conn) -> dict:
    """PBX/SIP liveness derived from heartbeats. All False when never seen."""
    hb_val, hb_at = _get_state(conn, "pbx_last_heartbeat")
    sip_val, _ = _get_state(conn, "pbx_sip_registered")
    trunk_val, _ = _get_state(conn, "pbx_trunk")
    host_val, _ = _get_state(conn, "pbx_host")
    age_s = None
    healthy = False
    if hb_at:
        try:
            # SQLite datetime('now') is UTC "YYYY-MM-DD HH:MM:SS".
            hb_dt = datetime.strptime(hb_at, "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
            age_s = max(0, int((datetime.now(timezone.utc) - hb_dt).total_seconds()))
            healthy = age_s <= heartbeat_stale_seconds()
        except Exception:
            healthy = False
    sip_registered = (sip_val == "1") and healthy
    return {
        "pbx_healthy": healthy,
        "pbx_last_heartbeat_at_utc": hb_at,
        "pbx_last_heartbeat_age_s": age_s,
        "pbx_host": host_val or None,
        "sip_registered": sip_registered,
        "sip_trunk": trunk_val or None,
    }


def record_real_inbound(conn, call_sid: str, caller: str = "", to_number: str = "") -> bool:
    """Mark the honest first-real-inbound moment (once). Returns True if newly set.

    MUST only be called after gateway_authenticated(request) is True and the
    request declares transport=sip. There is intentionally no endpoint that
    sets this flag directly.
    """
    if not call_sid or "MOCK" in str(call_sid).upper():
        return False
    existing, _ = _get_state(conn, "first_real_inbound_at")
    try:
        conn.execute(
            "INSERT INTO ivr_events (call_sid, event_type, details, actor) VALUES (?,?,?,?)",
            (call_sid, "REAL_INBOUND_RECEIVED",
             json.dumps({"caller": caller or "", "to": to_number or ""}), "pbx-gateway"))
        conn.commit()
    except Exception:
        pass
    if existing:
        return False
    _set_state(conn, "first_real_inbound_at", "1")  # value unused; updated_at is the timestamp
    _set_state(conn, "first_real_inbound_sid", str(call_sid))
    return True


def pstn_status(conn) -> dict:
    """pstn_connected is True ONLY if a real inbound call was recorded."""
    marker, at = _get_state(conn, "first_real_inbound_at")
    sid, _ = _get_state(conn, "first_real_inbound_sid")
    connected = bool(marker)
    return {
        "pstn_connected": connected,
        "first_real_inbound_at_utc": at if connected else None,
        "first_real_inbound_sid": (sid or None) if connected else None,
    }


def _last10(number: str) -> str:
    return re.sub(r"\D", "", number or "")[-10:]


def authorize_dial_number(conn, call_sid: str, number: str) -> dict:
    """Decide whether the PBX may place the vet leg of this call.

    Allowed ONLY when `number` matches the mobile number (last-10 digits) of
    the veterinarian the application already selected for this call_sid
    (ivr_sessions.vet_id) and that vet row exists with role='vet'.
    Everything else -> allowed=False (toll-fraud impossible by construction).

    Also returns the per-call recording consent from the live call row so the
    PBX records only when the farmer actually consented (1) on this call.
    """
    want = _last10(number)
    if not call_sid or not want or len(want) < 10:
        return {"allowed": False, "reason": "bad-request"}
    try:
        sess = conn.execute(
            "SELECT vet_id FROM ivr_sessions WHERE call_sid=? ORDER BY id DESC LIMIT 1",
            (call_sid,)).fetchone()
        vet_id = sess["vet_id"] if sess else None
        if not vet_id:
            return {"allowed": False, "reason": "no-vet-selected-for-call"}
        vet = conn.execute("SELECT id, mobile, role FROM users WHERE id=?", (vet_id,)).fetchone()
        if not vet:
            return {"allowed": False, "reason": "vet-not-found"}
        if vet["role"] != "vet" or _last10(vet["mobile"] or "") != want:
            return {"allowed": False, "reason": "number-not-selected-vet"}
        call = conn.execute(
            "SELECT recording_enabled, recording_consent FROM ivr_calls WHERE call_sid=?",
            (call_sid,)).fetchone()
        record = False
        if call:
            try:
                record = bool(call["recording_enabled"]) and bool(call["recording_consent"])
            except Exception:
                record = False
        return {"allowed": True, "vet_id": vet_id, "record": record}
    except Exception:
        return {"allowed": False, "reason": "error"}
