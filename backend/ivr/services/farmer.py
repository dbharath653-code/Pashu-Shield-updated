"""
Helpline farmer identification + profile prefill.

Identifies the caller against registered farmers (owners) by phone number and
builds the "known facts" used to skip redundant IVR questions:
  - preferred language (never ask again when known)
  - region (profile -> herd -> recent case -> previous verified report)
  - farmer name + latest registered animal details (prefill survey answers)

Only verified database facts are used. Nothing is invented: unknown fields stay
unknown and are still asked in the survey.
"""
from typing import Dict, Any, Optional, List

from .phone import normalize_phone

PREFILL_MARKER = "prefilled:profile"

# Survey keys that may be prefilled from the farmer profile / latest animal.
# Medical/problem fields (symptoms, severity, ...) are NEVER prefilled.
PREFILLABLE_KEYS = (
    "farmer_name",
    "species",
    "breed",
    "age",
    "sex",
    "location_village",
    "location_district",
    "location_state",
)


def find_user_by_phone(conn, caller_normalized: str) -> Optional[Dict[str, Any]]:
    """Find a registered user by caller ID. Exact E.164 match first, then
    last-10-digit match (tolerates +91 vs 0 vs raw 10-digit storage)."""
    if not caller_normalized:
        return None
    row = conn.execute("SELECT * FROM users WHERE mobile=?", (caller_normalized,)).fetchone()
    if row:
        return dict(row)
    digits = "".join(ch for ch in caller_normalized if ch.isdigit())
    last10 = digits[-10:] if len(digits) >= 10 else digits
    if last10:
        row = conn.execute(
            "SELECT * FROM users WHERE mobile LIKE ? ORDER BY CASE role WHEN 'owner' THEN 0 ELSE 1 END, id LIMIT 1",
            (f"%{last10}",),
        ).fetchone()
        if row:
            return dict(row)
    return None


def identify_farmer(conn, caller_number: str) -> Optional[Dict[str, Any]]:
    """Full farmer context for a caller: user, region, language, animals, cases."""
    caller_norm = normalize_phone(caller_number or "")
    user = find_user_by_phone(conn, caller_norm)
    if not user:
        return None
    animals = [
        dict(r) for r in conn.execute(
            "SELECT * FROM animals WHERE owner_id=? ORDER BY id DESC", (user["id"],)
        ).fetchall()
    ]
    active_cases = [
        dict(r) for r in conn.execute(
            "SELECT * FROM cases WHERE owner_id=? AND status NOT IN ('CLOSED','RECOVERED') ORDER BY id DESC",
            (user["id"],),
        ).fetchall()
    ]
    region = resolve_region(conn, user)
    return {
        "user": user,
        "user_id": user["id"],
        "caller_normalized": caller_norm,
        "language": (user.get("preferred_language") or "").strip().lower() or None,
        "region": region,
        "animals": animals,
        "latest_animal": animals[0] if animals else None,
        "active_cases": active_cases,
    }


def resolve_region(conn, user: Dict[str, Any]) -> Dict[str, Any]:
    """Region priority: verified profile -> herd farm location -> most recent
    case animal location -> most recent verified IVR report -> UNKNOWN."""
    def _clean(v):
        v = (v or "").strip()
        return v if v and v.lower() != "not provided" else ""

    village = _clean(user.get("village"))
    block = _clean(user.get("block"))
    district = _clean(user.get("district"))
    state = _clean(user.get("state")) or "Maharashtra"
    if village or district:
        return {"village": village or "Not provided", "block": block,
                "district": district or "Not provided", "state": state,
                "source": "PROFILE"}

    herd = conn.execute(
        "SELECT village, block, district, state FROM herds WHERE owner_id=? ORDER BY id DESC LIMIT 1",
        (user["id"],)).fetchone()
    if herd and (_clean(herd["village"]) or _clean(herd["district"])):
        return {"village": _clean(herd["village"]) or "Not provided",
                "block": _clean(herd["block"]),
                "district": _clean(herd["district"]) or "Not provided",
                "state": _clean(herd["state"]) or "Maharashtra",
                "source": "PROFILE"}

    case_loc = conn.execute(
        "SELECT a.village, a.block, a.district, a.state FROM cases c "
        "JOIN animals a ON a.id=c.animal_id WHERE c.owner_id=? "
        "ORDER BY c.id DESC LIMIT 1", (user["id"],)).fetchone()
    if case_loc and (_clean(case_loc["village"]) or _clean(case_loc["district"])):
        return {"village": _clean(case_loc["village"]) or "Not provided",
                "block": _clean(case_loc["block"]),
                "district": _clean(case_loc["district"]) or "Not provided",
                "state": _clean(case_loc["state"]) or "Maharashtra",
                "source": "PROFILE"}

    prev = conn.execute(
        "SELECT location_village, location_block, location_district, location_state "
        "FROM ivr_reports WHERE caller_user_id=? AND location_source IN "
        "('GPS','SMS_LINK','NETWORK','PROFILE','FARMER_PROVIDED') "
        "ORDER BY id DESC LIMIT 1", (user["id"],)).fetchone()
    if prev and (_clean(prev["location_village"]) or _clean(prev["location_district"])):
        return {"village": _clean(prev["location_village"]) or "Not provided",
                "block": _clean(prev["location_block"] or ""),
                "district": _clean(prev["location_district"]) or "Not provided",
                "state": _clean(prev["location_state"]) or "Maharashtra",
                "source": "PROFILE"}

    return {"village": "Not provided", "block": "",
            "district": "Not provided", "state": state, "source": "UNKNOWN"}


def build_prefill(conn, farmer: Dict[str, Any]) -> Dict[str, str]:
    """Prefill answers strictly from verified profile facts. Returns
    {question_key: value} for keys that may be skipped in the survey."""
    if not farmer:
        return {}
    user = farmer["user"]
    region = farmer.get("region") or {}
    prefill: Dict[str, str] = {}
    if (user.get("full_name") or "").strip():
        prefill["farmer_name"] = user["full_name"].strip()
    if region.get("source") == "PROFILE":
        if region.get("village") and region["village"] != "Not provided":
            prefill["location_village"] = region["village"]
        if region.get("district") and region["district"] != "Not provided":
            prefill["location_district"] = region["district"]
        if region.get("state"):
            prefill["location_state"] = region["state"]
    animal = farmer.get("latest_animal") or {}
    if (animal.get("species") or "").strip():
        prefill["species"] = animal["species"].strip()
    if (animal.get("breed") or "").strip():
        prefill["breed"] = animal["breed"].strip()
    age = animal.get("age_years") if animal.get("age_years") is not None else animal.get("age")
    if age is not None and str(age).strip() != "":
        prefill["age"] = str(age).strip()
    sex = (animal.get("sex") or animal.get("gender") or "").strip().title()
    if sex in ("Male", "Female"):
        prefill["sex"] = sex
    return {k: v for k, v in prefill.items() if k in PREFILLABLE_KEYS and v}


def get_prefilled_keys(conn, call_sid: str) -> List[str]:
    """Question keys whose LATEST answer is a profile prefill (still skippable).
    An explicit farmer answer overrides the prefill (latest row wins)."""
    rows = conn.execute(
        "SELECT question_key, transcript FROM ivr_survey_responses "
        "WHERE call_sid=? ORDER BY id", (call_sid,)).fetchall()
    latest = {}
    for r in rows:
        latest[r["question_key"]] = r["transcript"] or ""
    return [k for k, t in latest.items() if t == PREFILL_MARKER]


def apply_prefill(conn, call_sid: str, prefill: Dict[str, str], language: str = "en") -> List[str]:
    """Insert prefill answers as system responses (answer_source='manual' with
    a profile marker in transcript). Returns the skipped question keys."""
    if not prefill:
        return []
    from ..locales import t
    session = conn.execute(
        "SELECT * FROM ivr_sessions WHERE call_sid=?", (call_sid,)).fetchone()
    session_id = session["id"] if session else None
    existing = {r["question_key"] for r in conn.execute(
        "SELECT question_key FROM ivr_survey_responses WHERE call_sid=?", (call_sid,)).fetchall()}
    skipped = []
    for key, value in prefill.items():
        if key in existing:
            skipped.append(key)  # already answered explicitly; still known
            continue
        conn.execute(
            "INSERT INTO ivr_survey_responses (call_sid, session_id, question_key, question_text, "
            "answer_raw, answer_normalized, answer_source, confidence, language, transcript, is_confirmed) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (call_sid, session_id, key, t(f"q_{key}", language), value, value,
             "manual", 1.0, language, PREFILL_MARKER, 1),
        )
        skipped.append(key)
    conn.commit()
    return skipped


def location_provenance(conn, call_sid: str) -> str:
    """Honest location source for the final report: PROFILE only when BOTH the
    village and district answers in effect came from the verified profile and
    the farmer did not override them; otherwise '' (let survey logic decide)."""
    latest = {}
    rows = conn.execute(
        "SELECT question_key, answer_normalized, transcript FROM ivr_survey_responses "
        "WHERE call_sid=? AND question_key IN ('location_village','location_district') "
        "ORDER BY id", (call_sid,)).fetchall()
    for r in rows:
        latest[r["question_key"]] = r
    v = latest.get("location_village")
    d = latest.get("location_district")
    if not v and not d:
        return ""
    for r in (v, d):
        if not r:
            continue
        val = (r["answer_normalized"] or "").strip()
        if not val or val == "Not provided":
            return ""
        if (r["transcript"] or "") != PREFILL_MARKER:
            return ""
    return "PROFILE"


def save_preferred_language(conn, user_id: int, language: str):
    """Remember the farmer's language choice for future calls (all channels)."""
    if not user_id or not language:
        return
    language = language.strip().lower()
    from ..config import SUPPORTED_LANGUAGES
    if language not in SUPPORTED_LANGUAGES:
        return
    conn.execute("UPDATE users SET preferred_language=? WHERE id=?", (language, user_id))
    conn.commit()
