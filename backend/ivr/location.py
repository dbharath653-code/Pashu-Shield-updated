"""
Legitimate location capture for IVR reports.

A plain PSTN call does NOT expose GPS coordinates and caller ID is not a
location. This module implements only real sources, always labelling which
one was used:

    GPS              farmer opened the signed consent link and the browser
                     geolocation API returned coordinates
    NETWORK          the telephony provider returned network/carrier location
                     through its authorised API (only if the provider
                     supports it and it is legally permitted)
    FARMER_PROVIDED  the farmer stated village / district / state in the survey
    REGISTRY         the caller matched a registered user/animal profile
    NOT_AVAILABLE    none of the above - explicitly recorded, never guessed
"""
import json
from datetime import datetime, timedelta

from .config import settings
from .locales import t
from .security import sign_token, token_hash

VALID_SOURCES = ("GPS", "NETWORK", "FARMER_PROVIDED", "REGISTRY", "NOT_AVAILABLE")


def record_capture(conn, call_id, source, lat=None, lng=None, accuracy=None,
                   village=None, district=None, state=None, provider=None,
                   token=None, consent=0, raw=None, report_id=None, ttl_minutes=None):
    if source not in VALID_SOURCES:
        source = "NOT_AVAILABLE"
    expires = None
    if token:
        expires = (datetime.utcnow() + timedelta(minutes=ttl_minutes or settings.LOCATION_LINK_TTL_MINUTES)).isoformat()
    cur = conn.execute(
        "INSERT INTO ivr_location_captures (call_id, report_id, source, lat, lng, accuracy_meters, "
        "village, district, state, provider, token_hash, consent, raw, expires_at) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (call_id, report_id, source, lat, lng, accuracy, village, district, state, provider,
         token_hash(token) if token else None, int(consent),
         json.dumps(raw, ensure_ascii=False) if raw else None, expires),
    )
    conn.commit()
    return cur.lastrowid


def create_consent_link(conn, call_id, public_base=None, language="en"):
    """Create a signed, expiring browser-GPS consent link for this call."""
    token = sign_token({"call_id": call_id, "purpose": "gps_consent", "lang": language})
    record_capture(conn, call_id, "GPS", provider="consent_link", token=token, consent=0)
    base = (public_base or settings.PUBLIC_BASE_URL or "").rstrip("/")
    return f"{base}/ivr/location/{token}", token


def resolve_gps_submission(conn, token, lat, lng, accuracy=None, consent=True):
    """Store coordinates submitted through the consent link."""
    from .security import verify_token

    payload = verify_token(token)
    if not payload or payload.get("purpose") != "gps_consent":
        return None, "invalid or expired location link"
    call_id = payload.get("call_id")
    try:
        lat_f = float(lat)
        lng_f = float(lng)
    except (TypeError, ValueError):
        return None, "coordinates are not numeric"
    if not (-90 <= lat_f <= 90 and -180 <= lng_f <= 180):
        return None, "coordinates outside valid range"
    if not consent:
        record_capture(conn, call_id, "NOT_AVAILABLE", provider="consent_link", token=token,
                       consent=0, raw={"declined": True})
        return None, "consent not granted"
    acc = None
    try:
        acc = float(accuracy) if accuracy is not None else None
    except (TypeError, ValueError):
        acc = None
    capture_id = record_capture(conn, call_id, "GPS", lat=lat_f, lng=lng_f, accuracy=acc,
                                provider="browser_geolocation", token=token, consent=1)
    conn.execute(
        "UPDATE ivr_location_captures SET source='NOT_AVAILABLE' WHERE call_id=? AND id!=? AND source='GPS' AND consent=0",
        (call_id, capture_id),
    )
    conn.commit()
    return call_id, None


def capture_from_provider(conn, call_id, provider, meta):
    """Use network/carrier location only when the provider actually returns it."""
    if not meta:
        return None
    lat = meta.get("lat") or meta.get("latitude")
    lng = meta.get("lng") or meta.get("longitude")
    acc = meta.get("accuracy") or meta.get("accuracy_meters")
    if lat is None or lng is None:
        return None
    try:
        return record_capture(conn, call_id, "NETWORK", lat=float(lat), lng=float(lng),
                              accuracy=float(acc) if acc is not None else None,
                              provider=getattr(provider, "name", "provider"), consent=1, raw=meta)
    except (TypeError, ValueError):
        return None


def capture_from_answers(conn, call_id, answers):
    """Village/district/state spoken by the farmer."""
    village = answers.get("village")
    district = answers.get("district")
    state = answers.get("state") or settings.DEFAULT_STATE
    if not (village or district or state):
        return None
    return record_capture(conn, call_id, "FARMER_PROVIDED", village=village, district=district,
                          state=state, provider="survey", consent=1)


def capture_from_registry(conn, call_id, user_row=None, animal_row=None):
    """Known profile location (registered farmer / animal record)."""
    source_row = animal_row or user_row
    if not source_row:
        return None
    village = source_row["village"] if "village" in source_row.keys() else None
    district = source_row["district"] if "district" in source_row.keys() else None
    state = source_row["state"] if "state" in source_row.keys() else None
    if not (village or district):
        return None
    return record_capture(conn, call_id, "REGISTRY", village=village, district=district,
                          state=state or settings.DEFAULT_STATE, provider="registry", consent=1)


def resolve(conn, call_id):
    """Best available location for a call, in strict priority order."""
    rows = conn.execute(
        "SELECT * FROM ivr_location_captures WHERE call_id=? ORDER BY id DESC", (call_id,)
    ).fetchall()
    priority = {"GPS": 0, "NETWORK": 1, "FARMER_PROVIDED": 2, "REGISTRY": 3, "NOT_AVAILABLE": 4}
    best = None
    for r in rows:
        r = dict(r)
        if r["source"] == "GPS" and not r["consent"]:
            continue
        if best is None or priority.get(r["source"], 9) < priority.get(best["source"], 9):
            best = r
    if best is None:
        return {"source": "NOT_AVAILABLE", "lat": None, "lng": None, "accuracy": None,
                "village": None, "district": None, "state": None}
    return {
        "source": best["source"],
        "lat": best["lat"],
        "lng": best["lng"],
        "accuracy": best["accuracy_meters"],
        "village": best["village"],
        "district": best["district"],
        "state": best["state"],
    }


def send_location_sms(conn, call_id, phone, link, language="en", provider=None):
    """Send the consent link by SMS when SMS is enabled and configured."""
    if not settings.SMS_ENABLED or not phone:
        return False, "SMS disabled or no phone number"
    if provider is None:
        from .providers import get_provider
        provider = get_provider()
    try:
        provider.send_sms(phone, f"{t(language, 'location_sms_sent')} {link}")
        return True, None
    except Exception as exc:
        return False, str(exc)
