"""
IVR report generation - the bridge between a phone call and the EXISTING
PashuMitra domain model.

On completion of a call (or a partial call) this module:

  1. validates and normalises the collected survey/transcript data
  2. runs the structured AI/summarisation pipeline
  3. resolves or creates the EXISTING user (farmer), animal and case rows
  4. stores the IVR-specific record (ivr_reports, responses, transcripts)
  5. assigns urgency from transparent rules
  6. notifies the appropriate veterinarian through the EXISTING
     notification table and the government portal users
  7. writes audit + IVR events

No manual data entry is required after the farmer hangs up.
"""
import json
import os
import secrets
from datetime import datetime

from . import jobs, location as location_mod, rules
from .ai_pipeline import NOT_PROVIDED, summarise
from .config import settings
from .security import mask_phone, normalize_phone
from .stt import combined_transcript

# ------------------------------------------------- status synchronisation ---
IVR_STATUS_TO_CASE_STATUS = {
    "RECEIVED": "NEW",
    "AI_SUMMARIZED": "NEW",
    "VET_NOTIFIED": "ASSIGNED",
    "UNDER_REVIEW": "UNDER INVESTIGATION",
    "VET_CONTACTED": "ASSIGNED",
    "ACTION_RECOMMENDED": "TREATMENT",
    "FOLLOW_UP": "FOLLOW-UP",
    "RESOLVED": "RECOVERED",
    "CLOSED": "CLOSED",
}

CASE_STATUS_TO_IVR_STATUS = {
    "NEW": "RECEIVED",
    "ASSIGNED": "VET_NOTIFIED",
    "UNDER INVESTIGATION": "UNDER_REVIEW",
    "SAMPLE COLLECTED": "UNDER_REVIEW",
    "LAB PENDING": "UNDER_REVIEW",
    "DIAGNOSED": "UNDER_REVIEW",
    "TREATMENT": "ACTION_RECOMMENDED",
    "FOLLOW-UP": "FOLLOW_UP",
    "RECOVERED": "RESOLVED",
    "CLOSED": "CLOSED",
}

STATUS_FLOW = ["RECEIVED", "AI_SUMMARIZED", "VET_NOTIFIED", "UNDER_REVIEW",
               "VET_CONTACTED", "ACTION_RECOMMENDED", "FOLLOW_UP", "RESOLVED", "CLOSED"]


# ------------------------------------------------------------------ utils ---
def _log_event(conn, call_id, event_type, actor="system", actor_type="SYSTEM",
               report_id=None, details=None):
    conn.execute(
        "INSERT INTO ivr_events (call_id, report_id, event_type, actor, actor_type, details) "
        "VALUES (?,?,?,?,?,?)",
        (call_id, report_id, event_type, actor, actor_type,
         json.dumps(details, ensure_ascii=False, default=str) if details else None),
    )


def _audit(conn, context, action, entity_type, entity_id, details=None,
           actor_name="IVR System", actor_role="SYSTEM"):
    fn = (context or {}).get("audit_log")
    if fn:
        fn(conn, action, entity_type, entity_id, actor_name=actor_name,
           actor_role=actor_role, details=details)


def answers_map(conn, call_id):
    """{question_key: normalized_value} for a call (latest answer wins)."""
    rows = conn.execute(
        "SELECT * FROM ivr_responses WHERE call_id=? ORDER BY id", (call_id,)
    ).fetchall()
    out = {}
    for r in rows:
        if r["normalized_value"] is None:
            continue
        out[r["question_key"]] = r["normalized_value"]
    return out


def _find_user_by_phone(conn, phone):
    if not phone:
        return None
    digits = phone.lstrip("+")
    tail = digits[-10:]
    rows = conn.execute("SELECT * FROM users WHERE mobile LIKE ?", (f"%{tail}",)).fetchall()
    for r in rows:
        normalized, _ = normalize_phone(r["mobile"])
        if normalized == phone:
            return dict(r)
    return None


def _create_ivr_user(conn, phone, answers, context):
    """Create a minimal owner account for an unknown caller."""
    hash_password = (context or {}).get("hash_password")
    from database import hash_password as _hp
    hash_password = hash_password or _hp
    pwd = secrets.token_urlsafe(24)
    pw_hash, salt = hash_password(pwd)
    name = answers.get("farmer_name") or f"IVR Caller {mask_phone(phone)}"
    email = f"ivr.{secrets.token_hex(6)}@ivr.local"
    cur = conn.execute(
        "INSERT INTO users (full_name, mobile, email, password_hash, salt, role, village, district, state, is_seed) "
        "VALUES (?,?,?,?,?,'owner',?,?,?,0)",
        (name, phone, email, pw_hash, salt,
         answers.get("village"), answers.get("district") or "Unknown",
         answers.get("state") or settings.DEFAULT_STATE),
    )
    conn.commit()
    return conn.execute("SELECT * FROM users WHERE id=?", (cur.lastrowid,)).fetchone()


def _resolve_animal(conn, owner, structured, district):
    """Reuse an existing animal of the same owner/species when possible."""
    species = structured.get("animal", {}).get("species")
    if species and species != NOT_PROVIDED:
        row = conn.execute(
            "SELECT * FROM animals WHERE owner_id=? AND LOWER(COALESCE(species,''))=LOWER(?) "
            "ORDER BY id DESC LIMIT 1", (owner["id"], species)
        ).fetchone()
        if row:
            return dict(row)
    next_code = None
    try:
        from database import next_code as _nc
        next_code = _nc
    except Exception:
        next_code = None
    code = next_code(conn, "MH", "animals", "animal_code",
                     district=(district or "PUN")[:3].upper()) if next_code else f"IVR-{secrets.token_hex(4).upper()}"
    animal = structured.get("animal", {})
    cur = conn.execute(
        "INSERT INTO animals (animal_code, owner_id, species, breed, sex, age_years, owner_name, mobile, "
        "village, district, state, status, is_seed) VALUES (?,?,?,?,?,?,?,?,?,?,?,'Under Observation',0)",
        (code, owner["id"],
         (species if species and species != NOT_PROVIDED else "Unknown"),
         animal.get("breed") if animal.get("breed") != NOT_PROVIDED else None,
         animal.get("sex") if animal.get("sex") != NOT_PROVIDED else None,
         _as_float(animal.get("age")),
         owner["full_name"], owner["mobile"],
         structured.get("location", {}).get("village"),
         district, structured.get("location", {}).get("state") or settings.DEFAULT_STATE),
    )
    conn.commit()
    return conn.execute("SELECT * FROM animals WHERE id=?", (cur.lastrowid,)).fetchone()


def _as_float(value):
    try:
        return float(str(value).split()[0])
    except (TypeError, ValueError, IndexError, AttributeError):
        return None


def _next_report_no(conn):
    from database import next_code as _nc
    return _nc(conn, "IVR", "ivr_reports", "report_no")


# --------------------------------------------------------- report creation --
def generate_report(conn, call_id, context=None, partial=None):
    """Create (or return) the IVR report for a call. Idempotent."""
    existing = conn.execute("SELECT * FROM ivr_reports WHERE call_id=?", (call_id,)).fetchone()
    if existing:
        return dict(existing)

    call = conn.execute("SELECT * FROM ivr_calls WHERE id=?", (call_id,)).fetchone()
    if not call:
        return None
    call = dict(call)
    language = call.get("language") or "en"
    answers = answers_map(conn, call_id)
    transcript = combined_transcript(conn, call_id) or ""

    # ---- structured summary -------------------------------------------
    extra = {
        "species": answers.get("species"),
        "breed": answers.get("breed"),
        "age": answers.get("age"),
        "sex": answers.get("sex"),
        "count": answers.get("animal_count") or answers.get("count"),
        "problem": answers.get("main_problem"),
        "duration": answers.get("duration"),
        "severity": answers.get("severity"),
        "eating": answers.get("eating"),
        "drinking": answers.get("drinking"),
        "temperature": answers.get("temperature"),
        "vaccination_status": answers.get("vaccination_status"),
        "previous_disease": answers.get("previous_disease"),
        "pregnancy_status": answers.get("pregnancy_status"),
        "additional_description": answers.get("additional_notes") or answers.get("additional_description"),
        "symptoms": [answers["symptoms"]] if answers.get("symptoms") else [],
        "previous_treatment": [answers[k] for k in ("treatment_given", "treatment_detail") if answers.get(k)],
    }
    extra = {k: v for k, v in extra.items() if v not in (None, "", NOT_PROVIDED, [])}

    result = summarise(transcript, language=language,
                       call_id=call.get("provider_call_id"), extra_answers=extra)
    structured = result["structured"]

    # ---- location ------------------------------------------------------
    # Record what the farmer actually told us, then resolve the best source.
    if any(answers.get(k) for k in ("village", "district", "state")):
        location_mod.capture_from_answers(conn, call_id, {
            "village": answers.get("village"),
            "district": answers.get("district"),
            "state": answers.get("state"),
        })
    loc = location_mod.resolve(conn, call_id)
    structured["location"] = {
        "village": loc.get("village") or answers.get("village") or NOT_PROVIDED,
        "district": loc.get("district") or answers.get("district") or NOT_PROVIDED,
        "state": loc.get("state") or answers.get("state") or settings.DEFAULT_STATE,
        "lat": loc.get("lat"),
        "lng": loc.get("lng"),
        "source": loc.get("source") or "NOT_AVAILABLE",
        "accuracy": loc.get("accuracy"),
    }
    if answers.get("other_animals_affected") is not None:
        structured["other_animals_affected"] = answers["other_animals_affected"]

    # ---- urgency --------------------------------------------------------
    urgency, fired, note = rules.evaluate_urgency(structured, transcript)
    structured["urgency"] = urgency
    structured["urgency_rules"] = fired
    structured["summary_text"] = result["summary_text"]

    # ---- missing fields / completion state -------------------------------
    from .survey import get_active_survey
    _, definition = get_active_survey(conn)
    required = [q["key"] for q in definition.get("questions", []) if q.get("required")]
    missing = [k for k in required if answers.get(k) in (None, "")]
    completion = "PARTIALLY_COMPLETED" if partial or missing else "COMPLETE"

    # ---- existing domain objects ----------------------------------------
    phone = None
    try:
        from .security import decrypt_phone
        phone = decrypt_phone(call.get("caller_number_encrypted"))
    except Exception:
        phone = None
    owner = _find_user_by_phone(conn, phone) if phone else None
    if owner is None and phone:
        owner = _create_ivr_user(conn, phone, answers, context)
    if owner is None:
        # No caller number at all: keep the report but flag it clearly.
        owner = conn.execute("SELECT * FROM users WHERE role='owner' ORDER BY id LIMIT 1").fetchone()
        structured["farmer"]["phone"] = "Not provided"
    else:
        structured["farmer"]["phone"] = mask_phone(phone) if phone else NOT_PROVIDED
        structured["farmer"]["name"] = answers.get("farmer_name") or owner["full_name"]

        # A registered farmer profile is a legitimate (but lower priority)
        # location source when the farmer did not state one.
        location_mod.capture_from_registry(conn, call_id, user_row=owner)
        loc = location_mod.resolve(conn, call_id)
        for key in ("village", "district", "state"):
            if structured["location"].get(key) in (None, "", NOT_PROVIDED) and loc.get(key):
                structured["location"][key] = loc[key]
        if structured["location"].get("source") == "NOT_AVAILABLE" and loc.get("source") != "NOT_AVAILABLE":
            structured["location"]["source"] = loc["source"]
            structured["location"]["lat"] = loc.get("lat")
            structured["location"]["lng"] = loc.get("lng")
            structured["location"]["accuracy"] = loc.get("accuracy")

    district = structured["location"].get("district")
    district = None if district in (None, "", NOT_PROVIDED) else district
    animal = _resolve_animal(conn, owner, structured, district) if owner else None

    vet, vet_reason = (None, "auto-assignment disabled")
    if settings.AUTO_ASSIGN_VET:
        vet, vet_reason = rules.assign_vet(conn, district=district)

    structured["source"] = "IVR"
    structured["call_id"] = call.get("provider_call_id") or ""
    structured["timestamp"] = datetime.utcnow().isoformat()

    # ---- case (existing domain object) -----------------------------------
    symptoms_txt = ", ".join(structured.get("symptoms") or []) or (answers.get("main_problem") or "")
    severity_for_case = {"CRITICAL": "Critical", "HIGH": "High", "MEDIUM": "Medium", "LOW": "Low"}.get(urgency, "Medium")
    description = (
        f"{result['summary_text']}\n\n"
        f"Source: IVR call {call.get('provider_call_id') or ''} "
        f"({call.get('provider') or 'unknown'}), language {language}.\n"
        f"Urgency: {urgency} ({', '.join(fired)}). {note}\n"
        f"{result['disclaimer']}"
    )
    case_id = None
    if animal and owner:
        try:
            from database import next_code as _nc
            case_no = _nc(conn, "CASE", "cases", "case_no")
        except Exception:
            case_no = f"CASE-{secrets.token_hex(3).upper()}"
        cur = conn.execute(
            "INSERT INTO cases (case_no, animal_id, herd_id, owner_id, vet_id, symptoms, severity, "
            "description, reported_through, status) VALUES (?,?,?,?,?,?,?,?,'IVR',?)",
            (case_no, animal["id"], animal["herd_id"] if "herd_id" in animal.keys() else None,
             owner["id"], vet["id"] if vet else None,
             symptoms_txt or "Reported via IVR (no symptoms captured)",
             severity_for_case, description, IVR_STATUS_TO_CASE_STATUS["RECEIVED"]),
        )
        case_id = cur.lastrowid
        conn.execute(
            "INSERT INTO case_updates (case_id, status, note, updated_by) VALUES (?,?,?,?)",
            (case_id, IVR_STATUS_TO_CASE_STATUS["RECEIVED"],
             f"Reported via IVR call {call.get('provider_call_id') or ''} "
             f"({ 'veterinarian consultation' if call.get('flow') == 'VET_CONNECT' else 'automated survey' })",
             "IVR System"),
        )
        conn.execute("UPDATE animals SET status='Under Observation' WHERE id=?", (animal["id"],))

    # ---- duplicate detection --------------------------------------------
    dup_row, dup_reasons = rules.detect_duplicate(conn, call.get("caller_number_hash"), structured)

    session_row = conn.execute(
        "SELECT id FROM ivr_sessions WHERE call_id=? ORDER BY id DESC LIMIT 1", (call_id,)
    ).fetchone()
    session_id = session_row["id"] if session_row else None

    report_no = _next_report_no(conn)
    cur = conn.execute(
        "INSERT INTO ivr_reports (report_no, call_id, session_id, case_id, animal_id, owner_user_id, "
        "assigned_vet_id, language, source, flow, status, completion_state, missing_fields, structured_json, "
        "ai_summary, ai_model, ai_generated, urgency, urgency_source, urgency_rules, original_transcript, "
        "translated_summary, location_source, lat, lng, location_accuracy, village, district, state, "
        "is_duplicate, duplicate_of, duplicate_reasons) "
        "VALUES (?,?,?,?,?,?,?,?,'IVR',?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (report_no, call_id, session_id,
         case_id, animal["id"] if animal else None, owner["id"] if owner else None,
         vet["id"] if vet else None, language, call.get("flow") or "SURVEY",
         "AI_SUMMARIZED" if (result["ai_generated"] or transcript) else "RECEIVED",
         completion, json.dumps(missing) if missing else None,
         json.dumps(structured, ensure_ascii=False),
         result["summary_text"], result["model"], int(result["ai_generated"]),
         urgency, "RULE_BASED", json.dumps(fired), transcript or None,
         result["summary_text"] if language != "en" else None,
         structured["location"]["source"], structured["location"].get("lat"),
         structured["location"].get("lng"), structured["location"].get("accuracy"),
         structured["location"].get("village"), district, structured["location"].get("state"),
         1 if dup_row else 0, dup_row["id"] if dup_row else None,
         json.dumps(dup_reasons) if dup_reasons else None),
    )
    conn.commit()
    report_id = cur.lastrowid

    _log_event(conn, call_id, "REPORT_CREATED", report_id=report_id,
               details={"report_no": report_no, "case_id": case_id, "urgency": urgency,
                        "completion": completion, "missing_fields": missing,
                        "duplicate_of": dup_row["id"] if dup_row else None,
                        "vet_reason": vet_reason, "ai_model": result["model"],
                        "warnings": result["warnings"],
                        "unverified_extractions": result["unverified_extractions"]})
    _audit(conn, context, "IVR_REPORT_CREATED", "ivr_report", report_id,
           details={"report_no": report_no, "case_id": case_id, "urgency": urgency,
                    "language": language, "location_source": structured["location"]["source"]})

    jobs.enqueue(conn, "NOTIFY_VET", {"report_id": report_id}, call_id=call_id,
                 report_id=report_id, dedupe_key=f"notify-vet-{report_id}")
    if settings.NOTIFY_GOV_USERS:
        jobs.enqueue(conn, "NOTIFY_GOV", {"report_id": report_id}, call_id=call_id,
                     report_id=report_id, dedupe_key=f"notify-gov-{report_id}")
    conn.commit()

    return conn.execute("SELECT * FROM ivr_reports WHERE id=?", (report_id,)).fetchone()


# ------------------------------------------------------------ job handlers --
@jobs.register("SUMMARISE")
def job_summarise(conn, payload, context=None):
    call_id = payload.get("call_id")
    if not call_id:
        raise ValueError("SUMMARISE job without call_id")
    report = generate_report(conn, call_id, context=context, partial=payload.get("partial"))
    return {"report_id": report["id"] if report else None}


@jobs.register("GENERATE_REPORT")
def job_generate_report(conn, payload, context=None):
    return job_summarise(conn, payload, context)


@jobs.register("NOTIFY_VET")
def job_notify_vet(conn, payload, context=None):
    return notify_vet(conn, payload["report_id"], context=context)


@jobs.register("NOTIFY_GOV")
def job_notify_gov(conn, payload, context=None):
    return notify_govt(conn, payload["report_id"], context=context)


@jobs.register("TRANSCRIBE")
def job_transcribe(conn, payload, context=None):
    from . import stt
    call_id = payload.get("call_id")
    recording_id = payload.get("recording_id")
    call = conn.execute("SELECT * FROM ivr_calls WHERE id=?", (call_id,)).fetchone()
    if not call:
        raise ValueError("unknown call for transcription")
    row = conn.execute("SELECT * FROM ivr_recordings WHERE id=?", (recording_id,)).fetchone() if recording_id else None

    if not stt.is_configured():
        conn.execute("UPDATE ivr_recordings SET status='AVAILABLE' WHERE call_id=?", (call_id,))
        _log_event(conn, call_id, "TRANSCRIPTION_SKIPPED",
                   details={"reason": stt.unavailability_reason()})
        conn.commit()
        jobs.enqueue(conn, "SUMMARISE", {"call_id": call_id}, call_id=call_id,
                     dedupe_key=f"summarise-{call_id}")
        return {"transcribed": False, "reason": stt.unavailability_reason()}

    provider = (context or {}).get("provider")
    audio = None
    if row and row["provider_recording_id"] and provider:
        try:
            audio, _ctype = provider.fetch_recording(row["provider_recording_id"], url=row["storage_uri"])
        except Exception as exc:
            raise RuntimeError(f"recording download failed: {exc}") from exc

    result = stt.transcribe(audio_bytes=audio, language=call["language"] or "en")
    if not result.get("text"):
        raise RuntimeError("speech-to-text returned no text")
    stt.store_provider_transcript(
        conn, call_id, result["text"], language=call["language"] or "en",
        confidence=result.get("confidence"), participant_role="FARMER",
        provenance="FARMER_REPORTED", provider=result.get("provider") or "stt",
    )
    _log_event(conn, call_id, "TRANSCRIPT_CREATED",
               details={"provider": result.get("provider"), "chars": len(result["text"])})
    conn.commit()
    jobs.enqueue(conn, "SUMMARISE", {"call_id": call_id}, call_id=call_id,
                 dedupe_key=f"summarise-{call_id}")
    return {"transcribed": True, "chars": len(result["text"])}


# ------------------------------------------------------------ notification --
def notify_vet(conn, report_id, context=None):
    report = conn.execute("SELECT * FROM ivr_reports WHERE id=?", (report_id,)).fetchone()
    if not report:
        raise ValueError(f"report {report_id} not found")
    report = dict(report)
    structured = json.loads(report["structured_json"] or "{}")

    vet_id = report.get("assigned_vet_id")
    reason = "already assigned"
    if not vet_id and settings.AUTO_ASSIGN_VET:
        # notification-only assignment: an off-duty veterinarian may still be
        # the right person to pick the report up.
        vet, reason = rules.assign_vet(conn, district=report.get("district"), require_available=False)
        vet_id = vet["id"] if vet else None
        if vet_id:
            conn.execute("UPDATE ivr_reports SET assigned_vet_id=? WHERE id=?", (vet_id, report_id))
            if report.get("case_id"):
                conn.execute("UPDATE cases SET vet_id=COALESCE(vet_id, ?) WHERE id=?", (vet_id, report["case_id"]))

    notified = []
    notify_fn = (context or {}).get("notify")
    if vet_id and notify_fn:
        animal = structured.get("animal", {})
        message = (
            f"IVR report {report['report_no']} (case #{report.get('case_id')}): "
            f"{animal.get('count') or ''} {animal.get('species') or 'animal'} - "
            f"{', '.join(structured.get('symptoms') or []) or structured.get('problem') or 'symptoms not specified'}. "
            f"Urgency: {report['urgency']}. Location: "
            f"{structured.get('location', {}).get('village')}, {structured.get('location', {}).get('district')} "
            f"({structured.get('location', {}).get('source')}). "
            f"Reported: {report['created_at']} via IVR."
            + (" POSSIBLE DUPLICATE - review before action." if report["is_duplicate"] else "")
        )
        notify_fn(conn, vet_id, message, "ivr")
        notified.append(vet_id)
    else:
        # No vet found: notify every veterinarian so nothing is lost.
        if notify_fn:
            for v in conn.execute("SELECT id FROM users WHERE role='vet'").fetchall():
                notify_fn(conn, v["id"], f"IVR report {report['report_no']} needs a veterinarian (none assigned).", "ivr")
                notified.append(v["id"])
        reason = reason or "no veterinarian assigned"

    conn.execute("UPDATE ivr_reports SET notified_vet_at=datetime('now'), status=CASE "
                 "WHEN status='CLOSED' THEN status ELSE 'VET_NOTIFIED' END, updated_at=datetime('now') WHERE id=?",
                 (report_id,))
    if report.get("case_id"):
        _set_case_status(conn, report["case_id"], IVR_STATUS_TO_CASE_STATUS["VET_NOTIFIED"],
                         "Veterinarian notified of IVR report", "IVR System")
    _log_event(conn, report.get("call_id"), "VET_NOTIFIED", report_id=report_id,
               details={"vet_ids": notified, "reason": reason, "urgency": report["urgency"]})
    _audit(conn, context, "IVR_VET_NOTIFIED", "ivr_report", report_id,
           details={"vet_ids": notified, "reason": reason})
    conn.commit()
    return {"notified": notified, "reason": reason}


def notify_govt(conn, report_id, context=None):
    report = dict(conn.execute("SELECT * FROM ivr_reports WHERE id=?", (report_id,)).fetchone() or {})
    if not report:
        raise ValueError(f"report {report_id} not found")
    structured = json.loads(report["structured_json"] or "{}")
    notify_fn = (context or {}).get("notify")
    notified = []
    if notify_fn:
        district = report.get("district")
        rows = conn.execute("SELECT id, district FROM users WHERE role='govt'").fetchall()
        targets = [r["id"] for r in rows if not district or (r["district"] or "").lower() == str(district).lower()]
        if not targets:
            targets = [r["id"] for r in rows]
        for uid in targets:
            notify_fn(conn, uid,
                      f"New IVR livestock report {report['report_no']} - "
                      f"{structured.get('location', {}).get('district') or 'district unknown'}, "
                      f"urgency {report['urgency']}, "
                      f"{(structured.get('animal') or {}).get('count') or 1} animal(s).",
                      "ivr")
            notified.append(uid)
    conn.execute(
        "UPDATE ivr_reports SET notified_govt_at=datetime('now'), updated_at=datetime('now') WHERE id=?",
        (report_id,),
    )
    _log_event(conn, report.get("call_id"), "GOVT_NOTIFIED", report_id=report_id,
               details={"user_ids": notified})
    _audit(conn, context, "IVR_GOVT_NOTIFIED", "ivr_report", report_id, details={"user_ids": notified})
    conn.commit()
    return {"notified": notified}


def _set_case_status(conn, case_id, status, note, updated_by="IVR System"):
    conn.execute("UPDATE cases SET status=?, updated_at=datetime('now') WHERE id=?", (status, case_id))
    conn.execute("INSERT INTO case_updates (case_id, status, note, updated_by) VALUES (?,?,?,?)",
                 (case_id, status, note, updated_by))


# ------------------------------------------------------------- lifecycle ----
def update_status(conn, report_id, new_status, context=None, actor_name="IVR System",
                  actor_role="SYSTEM", note=None):
    report = conn.execute("SELECT * FROM ivr_reports WHERE id=?", (report_id,)).fetchone()
    if not report:
        return None, "report not found"
    new_status = (new_status or "").upper()
    if new_status not in IVR_STATUS_TO_CASE_STATUS:
        return None, f"unknown IVR status '{new_status}'"
    old = report["status"]
    conn.execute("UPDATE ivr_reports SET status=?, updated_at=datetime('now') WHERE id=?", (new_status, report_id))
    if report["case_id"]:
        _set_case_status(conn, report["case_id"], IVR_STATUS_TO_CASE_STATUS[new_status],
                         note or f"IVR report status changed to {new_status}", actor_name)
    _log_event(conn, report["call_id"], "REPORT_STATUS_CHANGED", actor=actor_name,
               actor_type=actor_role.upper() if actor_role.upper() in ("VET", "GOVT", "ADMIN", "FARMER") else "SYSTEM",
               report_id=report_id, details={"from": old, "to": new_status, "note": note})
    _audit(conn, context, "IVR_REPORT_STATUS_CHANGED", "ivr_report", report_id,
           details={"from": old, "to": new_status}, actor_name=actor_name, actor_role=actor_role)
    conn.commit()
    return conn.execute("SELECT * FROM ivr_reports WHERE id=?", (report_id,)).fetchone(), None


def sync_case_status(conn, case_id, new_case_status, context=None, actor_name="System", actor_role="SYSTEM"):
    """Keep the IVR lifecycle in step with the existing case workflow."""
    report = conn.execute("SELECT * FROM ivr_reports WHERE case_id=?", (case_id,)).fetchone()
    if not report:
        return None
    mapped = CASE_STATUS_TO_IVR_STATUS.get((new_case_status or "").upper())
    if not mapped or mapped == report["status"]:
        return report
    conn.execute("UPDATE ivr_reports SET status=?, updated_at=datetime('now') WHERE id=?", (mapped, report["id"]))
    _log_event(conn, report["call_id"], "REPORT_STATUS_SYNCED", actor=actor_name,
               actor_type=actor_role.upper() if actor_role.upper() in ("VET", "GOVT", "ADMIN", "FARMER") else "SYSTEM",
               report_id=report["id"],
               details={"case_status": new_case_status, "ivr_status": mapped})
    conn.commit()
    return conn.execute("SELECT * FROM ivr_reports WHERE id=?", (report["id"],)).fetchone()


def merge_reports(conn, report_id, target_report_id, context=None, actor_name="System", actor_role="GOVT"):
    """Merge a duplicate report into another report (never silently discarded)."""
    src = conn.execute("SELECT * FROM ivr_reports WHERE id=?", (report_id,)).fetchone()
    tgt = conn.execute("SELECT * FROM ivr_reports WHERE id=?", (target_report_id,)).fetchone()
    if not src or not tgt:
        return None, "source or target report not found"
    if src["id"] == tgt["id"]:
        return None, "cannot merge a report into itself"
    conn.execute(
        "UPDATE ivr_reports SET merged_into=?, status='CLOSED', updated_at=datetime('now') WHERE id=?",
        (tgt["id"], src["id"]),
    )
    if src["case_id"] and tgt["case_id"] and src["case_id"] != tgt["case_id"]:
        _set_case_status(conn, src["case_id"], "CLOSED",
                         f"Merged into IVR report {tgt['report_no']}", actor_name)
    _log_event(conn, src["call_id"], "REPORT_MERGED", actor=actor_name, actor_type=actor_role,
               report_id=src["id"], details={"merged_into": tgt["id"], "target_report": tgt["report_no"]})
    _audit(conn, context, "IVR_REPORT_MERGED", "ivr_report", src["id"],
           details={"merged_into": tgt["id"]}, actor_name=actor_name, actor_role=actor_role)
    conn.commit()
    return conn.execute("SELECT * FROM ivr_reports WHERE id=?", (report_id,)).fetchone(), None


# ------------------------------------------------------------- call close ---
def finalise_call(conn, call_id, context=None, status_override=None):
    """Called when the provider reports the call as finished/abandoned."""
    call = conn.execute("SELECT * FROM ivr_calls WHERE id=?", (call_id,)).fetchone()
    if not call:
        return None
    now = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")
    conn.execute(
        "UPDATE ivr_calls SET status=?, ended_at=COALESCE(ended_at, ?), updated_at=datetime('now') WHERE id=?",
        (status_override or "COMPLETED", now, call_id),
    )
    _log_event(conn, call_id, "CALL_ENDED", details={"status": status_override or "COMPLETED"})
    conn.commit()
    jobs.enqueue(conn, "SUMMARISE", {"call_id": call_id, "partial": status_override == "PARTIALLY_COMPLETED"},
                 call_id=call_id, dedupe_key=f"summarise-{call_id}")
    conn.commit()
    return call_id
