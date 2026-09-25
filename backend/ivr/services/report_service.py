"""
Report Service - creates structured IVR reports and links to existing case system.
"""
import json
import uuid
from datetime import datetime
from typing import Dict, Any, Optional

from .phone import normalize_phone
from .location import get_location_from_responses
from .ai_summarizer import llm_summarize_with_fallback, validate_structured_json
from .duplicate import check_duplicate
from .survey import normalize_answer

def generate_report_no(conn) -> str:
    from database import next_code
    return next_code(conn, "IVR", "ivr_reports", "report_no", pad=6, district="PUN")

def get_call_channel(conn, call_sid: str) -> str:
    """HELPLINE when the call arrived on the helpline number, else IVR."""
    try:
        row = conn.execute("SELECT channel FROM ivr_calls WHERE call_sid=?", (call_sid,)).fetchone()
        if row and row["channel"] in ("IVR", "HELPLINE"):
            return row["channel"]
    except Exception:
        pass
    return "IVR"


def create_ivr_report_from_survey(conn, call_sid: str, responses: Dict[str, Any],
                                  language: str = "en", transcript: str = "",
                                  caller_phone: str = "", duration_seconds: int = 0,
                                  is_partial: bool = False) -> Dict[str, Any]:
    normalized = {}
    for k, v in responses.items():
        if isinstance(v, dict) and "normalized" in v:
            normalized[k] = v["normalized"]
        else:
            normalized[k] = v

    channel = get_call_channel(conn, call_sid)
    caller_norm = normalize_phone(caller_phone or normalized.get("caller_number") or normalized.get("farmer_phone") or "")
    loc = get_location_from_responses(normalized)
    # Honest provenance: PROFILE only when village+district in effect both came
    # from the verified farmer profile without farmer override.
    try:
        from .farmer import location_provenance
        if location_provenance(conn, call_sid) == "PROFILE":
            loc["location_source"] = "PROFILE"
            loc["location_accuracy"] = "Verified farmer profile (registration record)"
    except Exception:
        pass
    is_dup, dup_id = check_duplicate(conn, caller_norm, normalized.get("species"), normalized.get("main_problem"), hours=24)
    status = "PARTIALLY_COMPLETED" if is_partial else "RECEIVED"
    if is_dup:
        status = "DUPLICATE_FLAGGED"

    transcript_full = transcript or " ".join([str(v) for v in normalized.values() if v and v != "Not provided"])
    structured, human_summary, method = llm_summarize_with_fallback(normalized, transcript_full, call_sid, language, loc, farmer_phone=caller_norm)
    valid, msg = validate_structured_json(structured)
    if not valid:
        raise ValueError(f"AI output validation failed: {msg}")
    structured_json = json.dumps(structured, ensure_ascii=False)
    urgency = structured.get("urgency", "MEDIUM")
    report_no = generate_report_no(conn)

    # Caller link for farmer-owned report visibility + region history.
    caller_user_id = None
    try:
        sess = conn.execute("SELECT caller_user_id FROM ivr_sessions WHERE call_sid=?", (call_sid,)).fetchone()
        caller_user_id = sess["caller_user_id"] if sess else None
    except Exception:
        pass

    # Insert ivr_reports - 41 columns
    placeholders_41 = ",".join(["?"]*41)
    cur = conn.execute(
        f"""
        INSERT INTO ivr_reports
        (report_no, call_sid, caller_number, caller_user_id, language, status, urgency, source,
         animal_species, animal_breed, animal_age, animal_sex, animal_count, is_pregnant,
         symptoms, duration, severity, eating_status, drinking_status, temperature,
         vaccination_status, previous_disease, medicines_given, main_problem, additional_description,
         location_village, location_block, location_district, location_state,
         location_lat, location_lng, location_source, location_accuracy,
         farmer_name, is_duplicate, duplicate_of_report_id,
         ai_summary, ai_structured_json, ai_confidence,
         transcript_full, call_duration_seconds)
        VALUES ({placeholders_41})
        """,
        (report_no, call_sid, caller_norm, caller_user_id, language, status, urgency, channel,
         normalized.get("species") or "Not provided",
         normalized.get("breed") or "Not provided",
         normalized.get("age") or "Not provided",
         normalized.get("sex") or "Not provided",
         int(normalized.get("animal_count") or 1) if str(normalized.get("animal_count") or "1").isdigit() else 1,
         normalized.get("pregnancy") or "Not provided",
         ", ".join(structured.get("symptoms", [])) if structured.get("symptoms") else (normalized.get("symptoms") or "Not provided"),
         normalized.get("duration") or "Not provided",
         normalized.get("severity") or "Medium",
         normalized.get("eating") or "Not provided",
         normalized.get("drinking") or "Not provided",
         normalized.get("temperature") or "Not provided",
         normalized.get("vaccination") or "Not provided",
         normalized.get("previous_disease") or "Not provided",
         normalized.get("medicines") or "Not provided",
         normalized.get("main_problem") or "Not provided",
         normalized.get("additional") or "Not provided",
         loc.get("village") or normalized.get("location_village") or "Not provided",
         loc.get("block") or "",
         loc.get("district") or normalized.get("location_district") or "Not provided",
         loc.get("state") or "Maharashtra",
         loc.get("lat"),
         loc.get("lng"),
         loc.get("location_source"),
         loc.get("location_accuracy"),
         normalized.get("farmer_name") or "Not provided",
         1 if is_dup else 0,
         dup_id,
         human_summary,
         structured_json,
         0.85,
         transcript_full,
         duration_seconds)
    )
    report_id = cur.lastrowid
    case_id = _create_linked_case(conn, normalized, loc, caller_norm, structured, report_no, call_sid, transcript_full, urgency, language, channel)
    conn.execute("UPDATE ivr_reports SET case_id=? WHERE id=?", (case_id, report_id))
    try:
        from database import audit_log
        audit_log(conn, "CREATE_IVR_REPORT", "ivr_report", report_id, details={"report_no": report_no, "call_sid": call_sid, "urgency": urgency, "is_duplicate": is_dup, "source": channel})
    except Exception:
        pass
    _notify_vet_and_govt(conn, report_id, report_no, normalized, loc, urgency, caller_norm, case_id, channel)
    try:
        from .call_service import set_routing_status
        set_routing_status(conn, call_sid, "REPORT_CREATED", {"report_id": report_id, "report_no": report_no, "source": channel})
        conn.execute("UPDATE ivr_sessions SET location_source=? WHERE call_sid=?", (loc.get("location_source"), call_sid))
        conn.commit()
    except Exception:
        pass
    conn.commit()
    try:
        conn.execute(
            "INSERT INTO ivr_analytics_daily (date, total_calls, completed_surveys, reports_generated, high_priority_reports) "
            "VALUES (date('now'), 1, 1, 1, ?) "
            "ON CONFLICT(date) DO UPDATE SET total_calls=total_calls+1, completed_surveys=completed_surveys+1, reports_generated=reports_generated+1, high_priority_reports=high_priority_reports+?",
            (1 if urgency in ("HIGH", "CRITICAL") else 0, 1 if urgency in ("HIGH", "CRITICAL") else 0)
        )
        conn.commit()
    except Exception:
        pass
    report = conn.execute("SELECT * FROM ivr_reports WHERE id=?", (report_id,)).fetchone()
    case = conn.execute("SELECT * FROM cases WHERE id=?", (case_id,)).fetchone() if case_id else None
    return {"report": dict(report) if report else {}, "case_id": case_id, "report_no": report_no, "urgency": urgency, "is_duplicate": is_dup}

def _create_linked_case(conn, normalized, loc, caller_norm, structured, report_no, call_sid, transcript_full, urgency, language, channel="IVR"):
    channel = channel if channel in ("IVR", "HELPLINE") else "IVR"
    try:
        from database import next_code, audit_log
        owner = None
        if caller_norm:
            owner = conn.execute("SELECT * FROM users WHERE mobile=?", (caller_norm,)).fetchone()
            if not owner:
                last10 = caller_norm[-10:] if len(caller_norm) >= 10 else caller_norm
                owner = conn.execute("SELECT * FROM users WHERE mobile LIKE ?", (f"%{last10}",)).fetchone()
        if not owner and caller_norm:
            import secrets, hashlib
            from database import hash_password
            pw_hash, salt = hash_password(secrets.token_hex(8))
            email = f"ivr_{caller_norm.replace('+','')[-10:]}@ivr.local"
            existing = conn.execute("SELECT id FROM users WHERE email=?", (email,)).fetchone()
            if existing:
                email = f"ivr_{uuid.uuid4().hex[:8]}@ivr.local"
            village = loc.get("village") or normalized.get("location_village") or "Unknown"
            district = loc.get("district") or normalized.get("location_district") or "Pune"
            if district == "Not provided":
                district = "Pune"
            cur = conn.execute(
                "INSERT INTO users (full_name, mobile, email, password_hash, salt, role, village, district, state, is_seed) "
                "VALUES (?,?,?,?,?,?,?,?,?,0)",
                (normalized.get("farmer_name") or f"Caller {caller_norm[-4:] if caller_norm else 'Unknown'}", caller_norm, email, pw_hash, salt, "owner", village, district, loc.get("state") or "Maharashtra")
            )
            owner = conn.execute("SELECT * FROM users WHERE id=?", (cur.lastrowid,)).fetchone()
        if not owner:
            owner = conn.execute("SELECT * FROM users WHERE role='owner' LIMIT 1").fetchone()
            if not owner:
                return None

        animal = conn.execute("SELECT * FROM animals WHERE owner_id=? ORDER BY id DESC LIMIT 1", (owner["id"],)).fetchone()
        species = normalized.get("species") or structured.get("animal", {}).get("species") or "Cattle"
        if species == "Not provided":
            species = "Cattle"
        if not animal:
            from database import next_code
            district_code = (owner["district"] or "PUN")[:3].upper()
            code = next_code(conn, "MH", "animals", "animal_code", district=district_code)
            cur = conn.execute(
                "INSERT INTO animals (animal_code, owner_id, species, breed, gender, sex, age, age_years, owner_name, mobile, village, block, district, state, status) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (code, owner["id"], species, normalized.get("breed") or structured.get("animal", {}).get("breed") or None,
                 normalized.get("sex"), normalized.get("sex"), normalized.get("age"), normalized.get("age"),
                 owner["full_name"], caller_norm, loc.get("village"), loc.get("block"), loc.get("district") or owner["district"],
                 loc.get("state") or "Maharashtra", "Under Observation")
            )
            animal = conn.execute("SELECT * FROM animals WHERE id=?", (cur.lastrowid,)).fetchone()
            try:
                import uuid as _uuid
                token = f"aqr_{_uuid.uuid4().hex}"
                payload = f"PASHU:ANIMAL:{token}"
                conn.execute("INSERT INTO animal_qr_codes (animal_id, qr_token, qr_payload, status) VALUES (?,?,?,'ACTIVE')", (animal["id"], token, payload))
            except Exception:
                pass

        sev_map = {"mild": "Low", "medium": "Medium", "high": "High", "critical": "Critical"}
        raw_sev = (normalized.get("severity") or "Medium").lower()
        severity = sev_map.get(raw_sev, "Medium")

        desc_parts = [
            f"{channel} Automated Survey Report {report_no} (Call {call_sid}, Language: {language})",
            f"Farmer: {normalized.get('farmer_name') or 'Not provided'} | Phone: {caller_norm or 'Not provided'}",
            f"Location: {loc.get('village')}, {loc.get('district')}, {loc.get('state')} (Source: {loc.get('location_source')})",
            f"Animal: {species} x{normalized.get('animal_count') or 1}, Breed: {normalized.get('breed') or 'Not provided'}, Age: {normalized.get('age') or 'Not provided'}, Sex: {normalized.get('sex') or 'Not provided'}",
            f"Main problem: {normalized.get('main_problem') or 'Not provided'} | Symptoms: {', '.join(structured.get('symptoms', [])) if structured.get('symptoms') else (normalized.get('symptoms') or 'Not provided')}",
            f"Duration: {normalized.get('duration') or 'Not provided'} days | Severity: {severity}",
            f"Eating: {normalized.get('eating') or 'Not provided'} | Drinking: {normalized.get('drinking') or 'Not provided'} | Temp: {normalized.get('temperature') or 'Not provided'}",
            f"Vaccination: {normalized.get('vaccination') or 'Not provided'} | Previous disease: {normalized.get('previous_disease') or 'Not provided'} | Medicines: {normalized.get('medicines') or 'Not provided'}",
        ]
        if normalized.get("additional") and normalized.get("additional") != "Not provided":
            desc_parts.append(f"Additional: {normalized.get('additional')}")
        if transcript_full:
            desc_parts.append(f"Transcript: {transcript_full[:800]}")
        description = "\n".join(desc_parts)

        disease_suspected = normalized.get("main_problem") or structured.get("main_problem") or "Unspecified (IVR)"
        if disease_suspected == "Not provided":
            disease_suspected = "Unspecified (IVR)"

        district_for_code = loc.get("district") or owner["district"] or "PUN"
        district_code = district_for_code[:3].upper() if district_for_code != "Not provided" else "PUN"
        district_code = "".join([c for c in district_code if c.isalpha()])[:3].upper() or "PUN"
        case_no = next_code(conn, "CASE", "cases", "case_no", district=district_code)

        cur = conn.execute(
            "INSERT INTO cases (case_no, animal_id, herd_id, owner_id, vet_id, symptoms, disease_suspected, severity, description, reported_through, status) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (case_no, animal["id"], animal["herd_id"], owner["id"], None,
             ", ".join(structured.get("symptoms", [])) if structured.get("symptoms") else (normalized.get("symptoms") or normalized.get("main_problem") or "Not provided"),
             disease_suspected, severity, description, channel, "NEW")
        )
        case_id = cur.lastrowid
        conn.execute("INSERT INTO case_updates (case_id, status, note, updated_by) VALUES (?,?,?,?)",
                     (case_id, "NEW", f"Reported via {channel} automated survey (Report {report_no}, Urgency {urgency})", f"{channel} System"))
        if urgency in ("HIGH", "CRITICAL"):
            conn.execute("INSERT INTO case_updates (case_id, status, note, updated_by) VALUES (?,?,?,?)",
                         (case_id, "NEW", f"⚠️ {channel} auto-escalated as {urgency} priority — immediate vet attention recommended", f"{channel} System"))
        return case_id
    except Exception as e:
        print(f"_create_linked_case failed: {e}")
        import traceback
        traceback.print_exc()
        return None

def _notify_vet_and_govt(conn, report_id, report_no, normalized, loc, urgency, caller_norm, case_id, channel="IVR"):
    channel = channel if channel in ("IVR", "HELPLINE") else "IVR"
    try:
        district = loc.get("district") or normalized.get("location_district") or ""
        if district and district != "Not provided":
            vets = conn.execute("SELECT id FROM users WHERE role='vet' AND LOWER(district)=LOWER(?)", (district,)).fetchall()
        else:
            vets = conn.execute("SELECT id FROM users WHERE role='vet' LIMIT 5").fetchall()
        if not vets:
            vets = conn.execute("SELECT id FROM users WHERE role='vet' LIMIT 3").fetchall()
        species = normalized.get("species") or "Animal"
        main_problem = normalized.get("main_problem") or "Health issue"
        lang_note = ""
        try:
            sess_lang = conn.execute("SELECT language FROM ivr_sessions WHERE call_sid=(SELECT call_sid FROM ivr_reports WHERE id=?)", (report_id,)).fetchone()
            if sess_lang and sess_lang["language"]:
                lang_note = f" | Lang: {sess_lang['language']}"
        except Exception:
            pass
        for v in vets:
            msg = f"📞 New {channel} report {report_no}: {species} - {main_problem} | Urgency: {urgency} | District: {district or 'Unknown'}{lang_note} | Caller: ****{caller_norm[-4:] if caller_norm else 'Unknown'}"
            if case_id:
                row = conn.execute('SELECT case_no FROM cases WHERE id=?', (case_id,)).fetchone()
                if row:
                    msg += f" | Case: {row['case_no']}"
            conn.execute("INSERT INTO notifications (user_id, message, type) VALUES (?,?,?)", (v["id"], msg, "case"))
        govts = conn.execute("SELECT id FROM users WHERE role='govt' LIMIT 5").fetchall()
        for g in govts:
            msg = f"📊 {channel} report {report_no} received: {species} in {district or 'Unknown'} | Urgency: {urgency}{lang_note} | Total affected: {normalized.get('animal_count') or 1}"
            conn.execute("INSERT INTO notifications (user_id, message, type) VALUES (?,?,?)", (g["id"], msg, "case"))
        if caller_norm:
            owner = conn.execute("SELECT id FROM users WHERE mobile=?", (caller_norm,)).fetchone()
            if owner:
                msg = f"✅ Your {channel} report {report_no} has been received and sent to veterinary team. Urgency: {urgency}. You will be contacted if follow-up is needed."
                conn.execute("INSERT INTO notifications (user_id, message, type) VALUES (?,?,?)", (owner["id"], msg, "case"))
    except Exception as e:
        print(f"_notify failed: {e}")

def create_ivr_report_from_vet_transcript(conn, call_sid: str, transcript: str, language: str = "en", caller_phone: str = "", vet_id: int = None, duration_seconds: int = 0):
    """Generate report for vet-connected call using transcript."""
    from .ai_summarizer import transcript_to_structured_vet_summary, validate_structured_json
    from .location import get_location_from_responses
    # Empty responses but we have transcript
    structured, human_summary = transcript_to_structured_vet_summary(transcript, transcript, call_sid, language)
    valid, msg = validate_structured_json(structured)
    if not valid:
        raise ValueError(msg)
    structured_json = json.dumps(structured, ensure_ascii=False)
    urgency = structured.get("urgency", "MEDIUM")
    channel = get_call_channel(conn, call_sid)
    caller_user_id = None
    try:
        sess = conn.execute("SELECT caller_user_id FROM ivr_sessions WHERE call_sid=?", (call_sid,)).fetchone()
        caller_user_id = sess["caller_user_id"] if sess else None
    except Exception:
        pass
    # Try to infer location from caller profile
    loc = {"village": "Not provided", "district": "Not provided", "state": "Maharashtra", "location_source": "NOT_AVAILABLE", "location_accuracy": "No location", "lat": None, "lng": None, "block": ""}
    if caller_phone:
        user = conn.execute("SELECT village, district, state FROM users WHERE mobile=?", (caller_phone,)).fetchone()
        if user:
            loc["village"] = user["village"] or "Not provided"
            loc["district"] = user["district"] or "Not provided"
            loc["state"] = user["state"] or "Maharashtra"
            loc["location_source"] = "FARMER_PROVIDED"
            loc["location_accuracy"] = "From caller profile"
    report_no = generate_report_no(conn)
    placeholders_41 = ",".join(["?"]*41)
    cur = conn.execute(
        f"""
        INSERT INTO ivr_reports
        (report_no, call_sid, caller_number, caller_user_id, language, status, urgency, source,
         animal_species, animal_breed, animal_age, animal_sex, animal_count, is_pregnant,
         symptoms, duration, severity, eating_status, drinking_status, temperature,
         vaccination_status, previous_disease, medicines_given, main_problem, additional_description,
         location_village, location_block, location_district, location_state,
         location_lat, location_lng, location_source, location_accuracy,
         farmer_name, is_duplicate, duplicate_of_report_id,
         ai_summary, ai_structured_json, ai_confidence,
         transcript_full, call_duration_seconds)
        VALUES ({placeholders_41})
        """,
        (report_no, call_sid, caller_phone, caller_user_id, language, "RECEIVED", urgency, channel,
         structured["animal"]["species"], structured["animal"]["breed"], structured["animal"]["age"], structured["animal"]["sex"],
         1, "Not provided",
         ", ".join(structured.get("symptoms", [])) if structured.get("symptoms") else "Not provided",
         structured.get("duration", "Not provided"),
         structured.get("severity", "Not provided"),
         structured.get("eating_status", "Not provided") if "eating_status" in structured else "Not provided",
         structured.get("drinking_status", "Not provided") if "drinking_status" in structured else "Not provided",
         structured.get("temperature", "Not provided") if "temperature" in structured else "Not provided",
         structured.get("vaccination_status", "Not provided"),
         structured.get("previous_disease", "Not provided") if "previous_disease" in structured else "Not provided",
         ", ".join(structured.get("previous_treatment", [])) if structured.get("previous_treatment") else "Not provided",
         structured.get("main_problem", "Not provided") if "main_problem" in structured else "Not provided",
         transcript[:500] if transcript else "Not provided",
         loc["village"], loc["block"], loc["district"], loc["state"],
         loc["lat"], loc["lng"], loc["location_source"], loc["location_accuracy"],
         "Not provided", 0, None,
         human_summary + "\n\n[Transcript] " + (transcript[:800] if transcript else ""),
         structured_json,
         0.85,
         transcript,
         duration_seconds)
    )
    report_id = cur.lastrowid
    # Link to case as well
    # Build normalized dict for case creation
    normalized = {"species": structured["animal"]["species"], "main_problem": structured.get("main_problem") or "Vet Consultation"}
    case_id = _create_linked_case(conn, normalized, loc, caller_phone, structured, report_no, call_sid, transcript, urgency, language, channel)
    conn.execute("UPDATE ivr_reports SET case_id=? WHERE id=?", (case_id, report_id))
    try:
        from database import audit_log
        audit_log(conn, "CREATE_IVR_VET_REPORT", "ivr_report", report_id, details={"report_no": report_no, "vet_id": vet_id})
    except Exception:
        pass
    # Notify govt/vet
    _notify_vet_and_govt(conn, report_id, report_no, normalized, loc, urgency, caller_phone, case_id, channel)
    conn.commit()
    return {"report_id": report_id, "report_no": report_no}

def update_report_status(conn, report_id: int, new_status: str, actor: str = "system", note: str = ""):
    valid = ["RECEIVED","PARTIALLY_COMPLETED","AI_SUMMARIZED","VET_NOTIFIED","UNDER_REVIEW","VET_CONTACTED","ACTION_RECOMMENDED","FOLLOW_UP","RESOLVED","CLOSED","DUPLICATE_FLAGGED"]
    if new_status not in valid:
        raise ValueError(f"Invalid status {new_status}")
    conn.execute("UPDATE ivr_reports SET status=?, updated_at=datetime('now') WHERE id=?", (new_status, report_id))
    report = conn.execute("SELECT case_id FROM ivr_reports WHERE id=?", (report_id,)).fetchone()
    if report and report["case_id"]:
        case_status_map = {
            "UNDER_REVIEW": "ASSIGNED",
            "VET_CONTACTED": "UNDER INVESTIGATION",
            "ACTION_RECOMMENDED": "TREATMENT",
            "FOLLOW_UP": "FOLLOW-UP",
            "RESOLVED": "RECOVERED",
            "CLOSED": "CLOSED",
        }
        case_status = case_status_map.get(new_status)
        if case_status:
            conn.execute("UPDATE cases SET status=?, updated_at=datetime('now') WHERE id=?", (case_status, report["case_id"]))
            conn.execute("INSERT INTO case_updates (case_id, status, note, updated_by) VALUES (?,?,?,?)",
                         (report["case_id"], case_status, note or f"IVR report status -> {new_status}", actor))
    try:
        from database import audit_log
        audit_log(conn, "UPDATE_IVR_REPORT_STATUS", "ivr_report", report_id, details={"new_status": new_status, "note": note}, actor_name=actor)
    except Exception:
        pass
    try:
        conn.execute("INSERT INTO ivr_events (call_sid, event_type, to_state, details, actor) VALUES ((SELECT call_sid FROM ivr_reports WHERE id=?), 'STATUS_CHANGE', ?, ?, ?)",
                     (report_id, new_status, note or "", actor))
    except Exception:
        pass
    conn.commit()
