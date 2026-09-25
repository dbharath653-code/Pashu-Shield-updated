"""
Transparent business rules for the IVR channel:

  * veterinarian availability + assignment (district, availability window,
    current workload)
  * rule-based urgency escalation (never an AI medical decision)
  * duplicate report detection (flag + merge, never silently discard)

Every rule carries an id so the escalation can be explained on screen and in
the audit trail.
"""
import json
from datetime import datetime

from .config import settings

URGENCY_LEVELS = {"LOW": 0, "MEDIUM": 1, "HIGH": 2, "CRITICAL": 3}
NOT_PROVIDED = "Not provided"

# ------------------------------------------------------------- vet lookup ---
def ensure_vet_availability(conn):
    """Create an availability row for every veterinarian (defaults from env)."""
    vets = conn.execute("SELECT id, district FROM users WHERE role='vet'").fetchall()
    for v in vets:
        exists = conn.execute("SELECT id FROM ivr_vet_availability WHERE vet_id=?", (v["id"],)).fetchone()
        if not exists:
            conn.execute(
                "INSERT INTO ivr_vet_availability (vet_id, is_available, available_from, available_to, district) "
                "VALUES (?,1,?,?,?)",
                (v["id"], settings.VET_AVAILABLE_FROM, settings.VET_AVAILABLE_TO, v["district"]),
            )
    conn.commit()


def _within_window(row, now):
    try:
        start = datetime.strptime(row["available_from"] or "00:00", "%H:%M").time()
        end = datetime.strptime(row["available_to"] or "23:59", "%H:%M").time()
    except ValueError:
        return True
    current = now.time()
    if start <= end:
        return start <= current <= end
    return current >= start or current <= end  # window crossing midnight


def _open_case_count(conn, vet_id):
    row = conn.execute(
        "SELECT COUNT(*) c FROM cases WHERE vet_id=? AND status NOT IN ('CLOSED','RECOVERED')", (vet_id,)
    ).fetchone()
    return row["c"] if row else 0


def available_vets(conn, district=None, now=None):
    """Veterinarians currently on duty, ordered by workload (lightest first)."""
    now = now or datetime.now()
    ensure_vet_availability(conn)
    rows = conn.execute(
        "SELECT u.id, u.full_name, u.mobile, u.district, a.is_available, a.available_from, "
        "a.available_to, a.max_open_cases "
        "FROM users u JOIN ivr_vet_availability a ON a.vet_id=u.id "
        "WHERE u.role='vet' AND a.is_available=1"
    ).fetchall()
    out = []
    for r in rows:
        if not _within_window(r, now):
            continue
        workload = _open_case_count(conn, r["id"])
        if workload >= (r["max_open_cases"] or 20):
            continue
        out.append({
            "vet": dict(r),
            "workload": workload,
            "district_match": bool(district and (r["district"] or "").lower() == str(district).lower()),
        })
    out.sort(key=lambda x: (not x["district_match"], x["workload"]))
    return out


def assign_vet(conn, district=None, now=None, require_available=True):
    """Pick the veterinarian for a new report.

    Returns (vet_row_or_None, reason).

    require_available=True (used when we are about to BRIDGE a live call)
        only on-duty veterinarians inside their availability window and below
        their workload limit are returned.
    require_available=False (used for notification only)
        falls back progressively so a report is never left unassigned when at
        least one veterinarian exists.
    """
    candidates = available_vets(conn, district=district, now=now)
    if candidates:
        best = candidates[0]
        reason = ("assigned to on-duty veterinarian in district "
                  f"{district}" if best["district_match"] else "assigned to on-duty veterinarian (nearest available)")
        return best["vet"], reason

    if require_available:
        return None, "no veterinarian is on duty in the availability window"

    # Nobody "on duty": fall back to any vet in the district, then any vet.
    if district:
        row = conn.execute(
            "SELECT id, full_name, mobile, district FROM users WHERE role='vet' AND district=? "
            "ORDER BY id LIMIT 1", (district,)
        ).fetchone()
        if row:
            return dict(row), f"no veterinarian on duty; fallback to district {district} veterinarian for notification"
    row = conn.execute(
        "SELECT id, full_name, mobile, district FROM users WHERE role='vet' ORDER BY id LIMIT 1"
    ).fetchone()
    if row:
        return dict(row), "no veterinarian on duty; fallback to first available veterinarian for notification"
    return None, "no veterinarian registered in the system"


# --------------------------------------------------------------- urgency ----
CRITICAL_RULES = [
    ("DEATH_REPORTED", ["died", "dead", "death", "मर गया", "मृत्यु", "చనిపోయింది", "मेले"]),
    ("DOWN_ANIMAL", ["cannot stand", "collapsed", "unconscious", "खड़ा नहीं", "निलबడलेक", "उभे राहू शकत नाही"]),
    ("MULTIPLE_ANIMALS_DOWN", ["many animals sick", "several animals", "whole herd"]),
]

HIGH_RULES = [
    ("BREATHING_DIFFICULTY", ["breathing", "gasping", "suffocation", "सांस", "శ్వాస", "श्वास"]),
    ("BLOOD_DISCHARGE", ["blood", "bloody", "खून", "రక్తం", "रक्त"]),
    ("NEUROLOGICAL_SIGNS", ["convulsion", "fit", "seizure", "circling", "दौरा", "ఫిట్స", "झटके"]),
    ("NOT_EATING_AND_DRINKING", ["not eating", "not drinking", "खाना बंद", "पानी नहीं"]),
    ("ABORTION", ["abortion", "miscarriage", "गर्भपात", "गर्भस्रावం"]),
]


def evaluate_urgency(structured, transcript=""):
    """Rule based escalation. Returns (urgency, [rule_ids], explanation)."""
    text = f"{json.dumps(structured, ensure_ascii=False)} {transcript or ''}".lower()
    fired = []
    urgency = "MEDIUM"

    def bump(level):
        nonlocal urgency
        if URGENCY_LEVELS.get(level, 0) > URGENCY_LEVELS.get(urgency, 0):
            urgency = level

    for rule_id, words in CRITICAL_RULES:
        if any(w.lower() in text for w in words):
            fired.append(rule_id)
            bump("CRITICAL")

    for rule_id, words in HIGH_RULES:
        if any(w.lower() in text for w in words):
            fired.append(rule_id)
            bump("HIGH")

    symptoms = [s.lower() for s in (structured.get("symptoms") or [])]
    eating = str(structured.get("eating") or "").strip().lower()
    drinking = str(structured.get("drinking") or "").strip().lower()
    not_eating = eating == "no" or any("not eating" in s for s in symptoms)
    not_drinking = drinking == "no" or any("not drinking" in s for s in symptoms)
    if not_eating and not_drinking:
        if "NOT_EATING_AND_DRINKING" not in fired:
            fired.append("NOT_EATING_AND_DRINKING")
        bump("HIGH")

    severity = str(structured.get("severity") or "").lower()
    if severity in ("severe", "critical", "animal cannot stand"):
        fired.append("SEVERITY_REPORTED_HIGH")
        bump("CRITICAL" if severity == "critical" else "HIGH")
    elif severity == "mild":
        fired.append("SEVERITY_REPORTED_MILD")
        if urgency == "MEDIUM":
            urgency = "LOW"

    count = structured.get("animal", {}).get("count")
    try:
        count_val = int(count) if count not in (None, "") else 0
    except (TypeError, ValueError):
        count_val = 0
    other = structured.get("other_animals_affected")
    try:
        other_val = int(other) if other not in (None, "") else 0
    except (TypeError, ValueError):
        other_val = 0
    if count_val >= 5 or other_val >= 4:
        fired.append("OUTBREAK_SIZED_GROUP")
        bump("CRITICAL")
    elif count_val >= 3 or other_val >= 2:
        fired.append("MULTIPLE_ANIMALS_AFFECTED")
        bump("HIGH")

    try:
        temp = float(str(structured.get("temperature") or "").strip())
        if temp >= 104:
            fired.append("HIGH_TEMPERATURE")
            bump("HIGH")
    except (TypeError, ValueError):
        pass

    duration = str(structured.get("duration") or "").lower()
    if "week" in duration or "month" in duration:
        fired.append("LONG_DURATION")
        bump("HIGH" if urgency == "MEDIUM" else urgency)

    if str(structured.get("pregnancy_status") or "").lower().startswith("pregnant") and (
            "abortion" in symptoms or "blood" in " ".join(symptoms)):
        fired.append("PREGNANCY_COMPLICATION")
        bump("HIGH")

    if not fired:
        fired.append("NO_ESCALATION_RULE_MATCHED")
        urgency = "MEDIUM"

    return urgency, fired, (
        "Rule-based escalation (not a veterinary diagnosis)."
    )


# ------------------------------------------------------------- duplicates ---
def detect_duplicate(conn, caller_hash, structured, window_minutes=None, exclude_report_id=None):
    """Find a probable duplicate report.

    Matching: same caller + same species + overlapping problem/symptoms inside
    the configured window. Returns (report_row_or_None, [reasons]).
    """
    window = window_minutes if window_minutes is not None else settings.DUPLICATE_WINDOW_MINUTES
    if not caller_hash:
        return None, []
    rows = conn.execute(
        "SELECT r.* FROM ivr_reports r JOIN ivr_calls c ON c.id=r.call_id "
        "WHERE c.caller_number_hash=? AND r.status != 'CLOSED' "
        "AND r.created_at >= datetime('now', ?) ORDER BY r.id DESC",
        (caller_hash, f"-{int(window)} minutes"),
    ).fetchall()

    species = str((structured.get("animal") or {}).get("species") or "").lower()
    problem = str(structured.get("problem") or "").lower()
    symptoms = {str(s).lower() for s in (structured.get("symptoms") or [])}

    for r in rows:
        if exclude_report_id and r["id"] == exclude_report_id:
            continue
        reasons = ["same caller phone number"]
        try:
            prev = json.loads(r["structured_json"] or "{}")
        except ValueError:
            prev = {}
        prev_species = str((prev.get("animal") or {}).get("species") or "").lower()
        prev_problem = str(prev.get("problem") or "").lower()
        prev_symptoms = {str(s).lower() for s in (prev.get("symptoms") or [])}

        if species and species != "not provided" and prev_species == species:
            reasons.append(f"same species ({species})")
        else:
            continue
        if problem and problem != "not provided" and prev_problem == problem:
            reasons.append(f"same main problem ({problem})")
        elif symptoms and prev_symptoms and (symptoms & prev_symptoms):
            reasons.append("overlapping symptoms: " + ", ".join(sorted(symptoms & prev_symptoms)[:3]))
        else:
            continue
        reasons.append(f"reported within {window} minutes")
        return dict(r), reasons
    return None, []
