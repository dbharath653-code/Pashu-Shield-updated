"""
Helpline veterinarian routing engine.

Availability (honest, derived from real data only):
  AVAILABLE     - vet can take a call now
  BUSY          - on another helpline call, or overloaded with active cases
  OFFLINE       - vet (or admin) explicitly marked unavailable
  OUTSIDE_HOURS - outside configured working hours (IST) with no manual override

Routing score (deterministic, never random):
  same district ......... +50
  same state ............ +20
  preferred language .... +15 (only when both sides known)
  existing assignment ... +25 (active case linking this farmer to this vet)
  specialization match .. +10
  load .................. -2 per active case (prefer least-loaded)

Unavailable vets (BUSY/OFFLINE/OUTSIDE_HOURS) are never selected.
"""
from datetime import datetime
from typing import Dict, Any, List, Optional, Tuple

AVAILABLE = "AVAILABLE"
BUSY = "BUSY"
OFFLINE = "OFFLINE"
OUTSIDE_HOURS = "OUTSIDE_HOURS"


def _current_ist_hour() -> int:
    try:
        from zoneinfo import ZoneInfo
        from ..config import VET_TIMEZONE
        return datetime.now(ZoneInfo(VET_TIMEZONE or "Asia/Kolkata")).hour
    except Exception:
        return datetime.now().hour


def vet_in_live_call(conn, vet_id: int) -> bool:
    """True when the vet is currently bridged on an unfinished helpline call.

    Only calls active within VET_LIVE_CALL_STALE_MINUTES count: provider
    status callbacks close rows promptly in production, but a crashed session
    must never pin a vet BUSY forever (stuck-BUSY guard)."""
    import os
    try:
        stale_min = int(os.environ.get("VET_LIVE_CALL_STALE_MINUTES", "30"))
    except Exception:
        stale_min = 30
    window = f"-{max(stale_min, 1)} minutes"
    row = conn.execute(
        "SELECT COUNT(*) c FROM ivr_call_participants p "
        "JOIN ivr_calls c ON c.call_sid=p.call_sid "
        "WHERE p.user_id=? AND p.role='vet' AND p.left_at IS NULL "
        "AND c.status IN ('INITIATED','RINGING','IN_PROGRESS') "
        "AND datetime(c.updated_at) >= datetime('now', ?)",
        (vet_id, window)).fetchone()
    if row and row["c"]:
        return True
    row = conn.execute(
        "SELECT COUNT(*) c FROM ivr_sessions s "
        "JOIN ivr_calls c ON c.call_sid=s.call_sid "
        "WHERE s.vet_id=? AND s.vet_connected=1 "
        "AND c.status IN ('INITIATED','RINGING','IN_PROGRESS') "
        "AND datetime(c.updated_at) >= datetime('now', ?)",
        (vet_id, window)).fetchone()
    return bool(row and row["c"])


def active_case_load(conn, vet_id: int) -> int:
    row = conn.execute(
        "SELECT COUNT(*) c FROM cases WHERE vet_id=? AND status NOT IN ('CLOSED','RECOVERED')",
        (vet_id,)).fetchone()
    return row["c"] if row else 0


def get_vet_availability(conn, vet: Dict[str, Any]) -> str:
    """Availability status for one vet. Manual override wins, except an
    explicit AVAILABLE vet on a live call is BUSY."""
    if not vet:
        return OFFLINE
    from ..config import VET_WORK_START_HOUR, VET_WORK_END_HOUR, VET_MAX_ACTIVE_CASES
    override = (vet.get("availability_status") or "").strip().upper()
    if override == OFFLINE:
        return OFFLINE
    if override == BUSY:
        return BUSY
    if vet_in_live_call(conn, vet["id"]):
        return BUSY
    if active_case_load(conn, vet["id"]) >= (VET_MAX_ACTIVE_CASES or 10):
        return BUSY
    if override == AVAILABLE:
        return AVAILABLE
    hour = _current_ist_hour()
    start, end = VET_WORK_START_HOUR, VET_WORK_END_HOUR
    if start <= end:
        in_hours = start <= hour < end
    else:  # overnight window, e.g. 20 -> 8
        in_hours = hour >= start or hour < end
    return AVAILABLE if in_hours else OUTSIDE_HOURS


def rank_available_vets(conn, district: str = None, language: str = None,
                        caller_user_id: int = None,
                        specialization: str = None) -> List[Dict[str, Any]]:
    """All vets with availability + routing score, best first. Unavailable
    vets are included with their status but flagged selectable=False."""
    vets = conn.execute("SELECT * FROM users WHERE role='vet' ORDER BY id").fetchall()
    caller = None
    if caller_user_id:
        caller = conn.execute("SELECT * FROM users WHERE id=?", (caller_user_id,)).fetchone()
    caller_district = (district or (caller["district"] if caller else "") or "").strip().lower()
    caller_state = ((caller["state"] if caller else "") or "").strip().lower()
    want_lang = (language or "").strip().lower()
    want_spec = (specialization or "").strip().lower()

    ranked = []
    for v in vets:
        v = dict(v)
        status = get_vet_availability(conn, v)
        score = 0
        reasons = []
        v_district = (v.get("district") or "").strip().lower()
        v_state = (v.get("state") or "").strip().lower()
        if caller_district and v_district and v_district == caller_district:
            score += 50
            reasons.append("same_district")
        elif caller_state and v_state and v_state == caller_state:
            score += 20
            reasons.append("same_state")
        v_lang = (v.get("preferred_language") or "").strip().lower()
        if want_lang and v_lang and v_lang == want_lang:
            score += 15
            reasons.append("language_match")
        if caller_user_id:
            linked = conn.execute(
                "SELECT COUNT(*) c FROM cases WHERE owner_id=? AND vet_id=? "
                "AND status NOT IN ('CLOSED','RECOVERED')",
                (caller_user_id, v["id"])).fetchone()["c"]
            if linked:
                score += 25
                reasons.append("existing_assignment")
        v_spec = (v.get("specialization") or "").strip().lower()
        if want_spec and v_spec and (want_spec in v_spec or v_spec in want_spec):
            score += 10
            reasons.append("specialization_match")
        load = active_case_load(conn, v["id"])
        score -= 2 * load
        ranked.append({
            "id": v["id"], "full_name": v["full_name"], "mobile": v["mobile"],
            "district": v.get("district"), "state": v.get("state"),
            "specialization": v.get("specialization"),
            "preferred_language": v.get("preferred_language"),
            "availability": status, "selectable": status == AVAILABLE,
            "active_cases": load, "score": score, "reasons": reasons,
        })
    # Selectable first, then score desc, then id asc (fully deterministic)
    ranked.sort(key=lambda r: (not r["selectable"], -r["score"], r["id"]))
    return ranked


def pick_best_vet(conn, district: str = None, language: str = None,
                  caller_user_id: int = None,
                  specialization: str = None) -> Tuple[Optional[Dict[str, Any]], List[Dict[str, Any]]]:
    """Returns (best selectable vet or None, full ranked list for audit)."""
    ranked = rank_available_vets(conn, district, language, caller_user_id, specialization)
    best = next((r for r in ranked if r["selectable"]), None)
    return best, ranked
