"""
Phone number normalization and validation.
Protects PII, standardizes to E.164.
"""
import re

def normalize_phone(raw: str) -> str:
    if not raw or not isinstance(raw, str):
        return ""
    raw = raw.strip()
    # Handle blocked/private
    if raw.lower() in ("blocked", "private", "unknown", "anonymous", "withheld"):
        return ""
    digits = re.sub(r"\D", "", raw)
    if not digits:
        return ""
    # India-specific: 10 digits -> +91
    if len(digits) == 10:
        return f"+91{digits}"
    if digits.startswith("91") and len(digits) == 12:
        return f"+{digits}"
    if raw.startswith("+") and digits:
        return f"+{digits}"
    # US-like 11 with leading 1: keep as +1
    if len(digits) == 11 and digits.startswith("1"):
        return f"+{digits}"
    # Already has plus implied
    if len(digits) >= 10:
        return f"+{digits}"
    return raw

def is_valid_phone(normalized: str) -> bool:
    if not normalized:
        return False
    # E.164: + followed by 10-15 digits
    return bool(re.match(r"^\+\d{10,15}$", normalized))

def mask_phone(phone: str) -> str:
    """Mask for display: +91******1234"""
    if not phone or len(phone) < 6:
        return "***"
    return phone[:3] + "****" + phone[-4:]

def extract_caller_from_request(request) -> str:
    """Extract caller ID from various provider form fields / headers."""
    # Try common fields
    for key in ["Caller", "From", "caller", "from", "CallerNumber", "CallSid", "caller_number", "phone"]:
        val = request.form.get(key) if hasattr(request, 'form') else None
        if val:
            norm = normalize_phone(val)
            if norm:
                return norm
        val = request.args.get(key) if hasattr(request, 'args') else None
        if val:
            norm = normalize_phone(val)
            if norm:
                return norm
        # JSON body
        try:
            body = request.get_json(silent=True) or {}
            if key in body and body[key]:
                norm = normalize_phone(str(body[key]))
                if norm:
                    return norm
        except Exception:
            pass
    # Headers fallback (for mock)
    for h in ["X-Caller-Number", "X-Phone-Number"]:
        val = request.headers.get(h)
        if val:
            norm = normalize_phone(val)
            if norm:
                return norm
    # Direct call_sid may encode caller? Not reliable
    # Final fallback: check form "From" case insensitive
    try:
        for k, v in (request.form.items() if hasattr(request, 'form') else []):
            if k.lower() in ("from", "caller", "caller_number", "phone"):
                norm = normalize_phone(str(v))
                if norm:
                    return norm
    except Exception:
        pass
    return ""

def get_caller_or_ask(call_sid: str, conn, request) -> str:
    """Get caller number from DB if already stored, else from request."""
    row = conn.execute("SELECT caller_number_normalized, caller_number FROM ivr_calls WHERE call_sid=?", (call_sid,)).fetchone()
    if row and row["caller_number_normalized"]:
        return row["caller_number_normalized"]
    extracted = extract_caller_from_request(request)
    if extracted:
        return extracted
    # Check session fallback stored responses
    resp = conn.execute("SELECT answer_normalized FROM ivr_survey_responses WHERE call_sid=? AND question_key='callback_number' ORDER BY id DESC LIMIT 1", (call_sid,)).fetchone()
    if resp and resp["answer_normalized"]:
        return normalize_phone(resp["answer_normalized"])
    return ""
