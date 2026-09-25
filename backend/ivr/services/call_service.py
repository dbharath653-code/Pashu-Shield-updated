"""
IVR Call Service - state machine for call lifecycle.
Handles welcome -> language -> menu -> vet connect OR survey -> report generation.
"""
import uuid
import json
import re
from datetime import datetime
from typing import Dict, Any, Optional

from ..locales import t
from ..config import SUPPORTED_LANGUAGES, DEFAULT_LANGUAGE, IVR_MAX_CALL_DURATION_SECONDS
from .phone import normalize_phone, extract_caller_from_request
from .survey import SURVEY_QUESTION_KEYS, should_skip_question, get_next_question_index, normalize_answer, CHOICE_MAPS
from .location import get_location_from_responses

# State definitions
STATE_WELCOME = "WELCOME"
STATE_LANGUAGE = "LANGUAGE_SELECT"
STATE_MENU = "MAIN_MENU"
STATE_VET_CONNECT = "VET_CONNECT"
STATE_VET_TALK = "VET_TALK"
STATE_SURVEY = "SURVEY"
STATE_SURVEY_CONFIRM = "SURVEY_CONFIRM"
STATE_COMPLETED = "COMPLETED"
STATE_FAILED = "FAILED"

LANGUAGE_DTMF = {"1": "en", "2": "te", "3": "hi", "4": "mr", "5": "en"}
LANGUAGE_SPEECH = {
    "english": "en", "angrezi": "en",
    "telugu": "te", "telegu": "te",
    "hindi": "hi", "hindhi": "hi",
    "marathi": "mr", "maharashtra": "mr",
}

def create_call_record(conn, provider: str, from_number: str, to_number: str, call_sid: str = None, is_mock: bool = False) -> str:
    if not call_sid:
        call_sid = f"IVR-{uuid.uuid4().hex[:12].upper()}"
    normalized = normalize_phone(from_number)
    ivr_num = to_number or ""
    conn.execute(
        "INSERT INTO ivr_calls (call_sid, provider, from_number, to_number, caller_number, caller_number_normalized, ivr_phone_number, status, language, is_mock) VALUES (?,?,?,?,?,?,?,?,?,?)",
        (call_sid, provider, from_number, to_number, from_number, normalized, ivr_num, "IN_PROGRESS", DEFAULT_LANGUAGE, 1 if is_mock else 0)
    )
    conn.execute("INSERT INTO ivr_sessions (call_sid, current_state, language) VALUES (?,?,?)", (call_sid, STATE_WELCOME, DEFAULT_LANGUAGE))
    conn.execute("INSERT INTO ivr_events (call_sid, event_type, from_state, to_state, details, actor) VALUES (?,?,?,?,?,?)",
                 (call_sid, "CALL_INITIATED", None, STATE_WELCOME, json.dumps({"from": from_number, "to": to_number, "provider": provider}), "system"))
    conn.commit()
    return call_sid

def get_session(conn, call_sid: str) -> Optional[Dict[str, Any]]:
    row = conn.execute("SELECT * FROM ivr_sessions WHERE call_sid=? ORDER BY id DESC LIMIT 1", (call_sid,)).fetchone()
    return dict(row) if row else None

def get_call(conn, call_sid: str) -> Optional[Dict[str, Any]]:
    row = conn.execute("SELECT * FROM ivr_calls WHERE call_sid=?", (call_sid,)).fetchone()
    return dict(row) if row else None

def update_session_state(conn, call_sid: str, new_state: str, language: str = None, extra: Dict[str, Any] = None):
    session = get_session(conn, call_sid)
    if not session:
        return
    old_state = session["current_state"]
    fields = ["current_state=?", "updated_at=datetime('now')"]
    params = [new_state]
    if language:
        fields.append("language=?")
        params.append(language)
        conn.execute("UPDATE ivr_calls SET language=?, updated_at=datetime('now') WHERE call_sid=?", (language, call_sid))
    if extra:
        for k, v in extra.items():
            if k in ("menu_choice", "vet_id", "vet_connected", "survey_started", "survey_completed", "current_question_key", "current_question_index", "retry_count", "caller_user_id"):
                fields.append(f"{k}=?")
                params.append(v)
    params.append(call_sid)
    conn.execute(f"UPDATE ivr_sessions SET {', '.join(fields)} WHERE call_sid=?", params)
    conn.execute("INSERT INTO ivr_events (call_sid, session_id, event_type, from_state, to_state, details, actor) VALUES (?,?,?,?,?,?,?)",
                 (call_sid, session["id"], "STATE_TRANSITION", old_state, new_state, json.dumps(extra or {}), "system"))
    conn.commit()

def set_language(conn, call_sid: str, lang_input: str) -> str:
    """Resolve language from DTMF or speech, persist."""
    lang_input = (lang_input or "").strip().lower()
    lang = None
    if lang_input in LANGUAGE_DTMF:
        lang = LANGUAGE_DTMF[lang_input]
    elif lang_input in LANGUAGE_SPEECH:
        lang = LANGUAGE_SPEECH[lang_input]
    elif lang_input in SUPPORTED_LANGUAGES:
        lang = lang_input
    else:
        # Try digit extraction
        m = re.search(r"[1-4]", lang_input)
        if m and m.group(0) in LANGUAGE_DTMF:
            lang = LANGUAGE_DTMF[m.group(0)]
    if not lang or lang not in SUPPORTED_LANGUAGES:
        lang = DEFAULT_LANGUAGE
    update_session_state(conn, call_sid, STATE_MENU, language=lang)
    # Also insert event
    conn.execute("INSERT INTO ivr_events (call_sid, event_type, details) VALUES (?,?,?)",
                 (call_sid, "LANGUAGE_SELECTED", json.dumps({"language": lang, "raw_input": lang_input})))
    conn.commit()
    return lang

def handle_menu_choice(conn, call_sid: str, choice: str, language: str) -> str:
    """Handle main menu: 1=vet, 2=survey."""
    choice = (choice or "").strip()
    # Extract digit
    m = re.search(r"[12]", choice)
    digit = m.group(0) if m else choice
    if digit == "1":
        update_session_state(conn, call_sid, STATE_VET_CONNECT, extra={"menu_choice": "1"})
        return "vet"
    elif digit == "2":
        update_session_state(conn, call_sid, STATE_SURVEY, extra={"menu_choice": "2", "survey_started": 1, "current_question_key": SURVEY_QUESTION_KEYS[0], "current_question_index": 0, "retry_count": 0})
        return "survey"
    else:
        # Invalid -> stay in menu
        conn.execute("INSERT INTO ivr_events (call_sid, event_type, details) VALUES (?,?,?)",
                     (call_sid, "INVALID_MENU_CHOICE", json.dumps({"input": choice})))
        conn.commit()
        return "invalid"

def find_available_vet(conn, district: str = None) -> Optional[Dict[str, Any]]:
    """Query vet availability. Reuse existing assignment logic."""
    # Prefer district-specific vet, otherwise any vet
    vet = None
    if district and district != "Not provided":
        vet = conn.execute("SELECT id, full_name, mobile, district FROM users WHERE role='vet' AND LOWER(district)=LOWER(?) LIMIT 1", (district,)).fetchone()
    if not vet:
        # Fallback: any vet who is not overloaded? Choose most recent vet with few active cases
        # Simple: pick vet with least active cases
        vets = conn.execute("SELECT id, full_name, mobile, district FROM users WHERE role='vet'").fetchall()
        if not vets:
            return None
        # Count active cases per vet
        best = None
        best_load = float('inf')
        for v in vets:
            cnt = conn.execute("SELECT COUNT(*) c FROM cases WHERE vet_id=? AND status NOT IN ('CLOSED','RECOVERED')", (v["id"],)).fetchone()["c"]
            if cnt < best_load:
                best_load = cnt
                best = v
        vet = best
    return dict(vet) if vet else None

def is_vet_available(conn, vet: Dict[str, Any]) -> bool:
    # Simple availability: if vet exists and not too overloaded (e.g., <10 active cases) consider available
    # In real prod, would check duty roster / presence. For now, deterministic.
    if not vet:
        return False
    cnt = conn.execute("SELECT COUNT(*) c FROM cases WHERE vet_id=? AND status NOT IN ('CLOSED','RECOVERED')", (vet["id"],)).fetchone()["c"]
    # If no active overload, available. Also check time-based: use business hours? For demo, always consider available unless overloaded >10
    if cnt >= 10:
        return False
    return True

def record_survey_answer(conn, call_sid: str, question_key: str, raw_answer: str, source: str = "dtmf", confidence: float = 1.0, language: str = "en"):
    """Store survey answer with normalization and confirmation."""
    session = get_session(conn, call_sid)
    if not session:
        return None
    normalized, src, digit = normalize_answer(question_key, raw_answer, source=source, language=language)
    # Get question text for storage
    q_text = t(f"q_{question_key}", language)
    conn.execute(
        "INSERT INTO ivr_survey_responses (call_sid, session_id, question_key, question_text, answer_raw, answer_normalized, answer_source, confidence, language, dtmf_digit, transcript, is_confirmed) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
        (call_sid, session["id"], question_key, q_text, raw_answer, normalized, src, confidence, language, digit, raw_answer if source == "speech" else "", 0)
    )
    # Also store transcript if speech
    if source == "speech" and raw_answer:
        conn.execute("INSERT INTO ivr_transcripts (call_sid, speaker, text_original, text_normalized, language, confidence, stt_provider) VALUES (?,?,?,?,?,?,?)",
                     (call_sid, "farmer", raw_answer, normalized, language, confidence, "whisper"))
    conn.commit()
    return normalized

def advance_survey(conn, call_sid: str, current_index: int, responses: Dict[str, Any] = None) -> Optional[str]:
    """Advance to next question, handling conditionals."""
    session = get_session(conn, call_sid)
    if not session:
        return None
    # Build responses dict from DB if not provided
    if responses is None:
        rows = conn.execute("SELECT question_key, answer_normalized FROM ivr_survey_responses WHERE call_sid=?", (call_sid,)).fetchall()
        responses = {r["question_key"]: r["answer_normalized"] for r in rows}
    next_idx = get_next_question_index(current_index, responses)
    if next_idx >= len(SURVEY_QUESTION_KEYS):
        # Survey complete
        update_session_state(conn, call_sid, STATE_COMPLETED, extra={"survey_completed": 1, "current_question_key": None, "current_question_index": next_idx})
        return None
    next_key = SURVEY_QUESTION_KEYS[next_idx]
    update_session_state(conn, call_sid, STATE_SURVEY, extra={"current_question_key": next_key, "current_question_index": next_idx, "retry_count": 0})
    return next_key

def get_all_responses_dict(conn, call_sid: str) -> Dict[str, Any]:
    rows = conn.execute("SELECT question_key, answer_normalized, answer_raw FROM ivr_survey_responses WHERE call_sid=?", (call_sid,)).fetchall()
    return {r["question_key"]: r["answer_normalized"] for r in rows}

def handle_call_end(conn, call_sid: str, duration_seconds: int = 0, reason: str = "completed"):
    """Handle call hangup / disconnect - save partial if needed and trigger report."""
    call = get_call(conn, call_sid)
    session = get_session(conn, call_sid)
    if not call:
        return
    # Update call status
    conn.execute("UPDATE ivr_calls SET status='COMPLETED', duration_seconds=?, ended_at=datetime('now'), updated_at=datetime('now') WHERE call_sid=?",
                 (duration_seconds or 0, call_sid))
    # If survey was started but not completed, mark partial
    if session and session.get("survey_started") and not session.get("survey_completed"):
        # Check how many answers we have
        cnt = conn.execute("SELECT COUNT(*) c FROM ivr_survey_responses WHERE call_sid=?", (call_sid,)).fetchone()["c"]
        if cnt > 0:
            # Trigger partial report generation
            responses = get_all_responses_dict(conn, call_sid)
            # Determine if enough info for partial (at least species or district)
            is_partial = cnt < 5  # heuristic
            # Generate report asynchronously or sync
            try:
                from .report_service import create_ivr_report_from_survey
                create_ivr_report_from_survey(conn, call_sid, responses, language=session.get("language","en"), caller_phone=call["caller_number_normalized"], duration_seconds=duration_seconds, is_partial=is_partial)
            except Exception as e:
                print(f"handle_call_end report failed: {e}")
                import traceback; traceback.print_exc()
        update_session_state(conn, call_sid, STATE_COMPLETED, extra={"survey_partial": 1})
    elif session and session.get("survey_completed"):
        # Already completed, ensure report exists
        pass
    # Record participant leave
    conn.execute("UPDATE ivr_call_participants SET left_at=datetime('now') WHERE call_sid=? AND left_at IS NULL", (call_sid,))
    conn.execute("INSERT INTO ivr_events (call_sid, event_type, details) VALUES (?,?,?)",
                 (call_sid, "CALL_ENDED", json.dumps({"reason": reason, "duration": duration_seconds})))
    conn.commit()
    # Update analytics
    try:
        # Increment abandoned if not completed?
        pass
    except Exception:
        pass

def handle_disconnect_mid_survey(conn, call_sid: str):
    """Save PARTIALLY_COMPLETED data on unexpected disconnect."""
    return handle_call_end(conn, call_sid, reason="disconnected")

def get_survey_progress(conn, call_sid: str) -> Dict[str, Any]:
    session = get_session(conn, call_sid)
    if not session:
        return {}
    total = len(SURVEY_QUESTION_KEYS)
    current = session.get("current_question_index", 0)
    answered = conn.execute("SELECT COUNT(*) c FROM ivr_survey_responses WHERE call_sid=?", (call_sid,)).fetchone()["c"]
    return {"total": total, "current_index": current, "answered": answered, "current_key": session.get("current_question_key"), "language": session.get("language"), "state": session.get("current_state")}

def vet_call_connected(conn, call_sid: str, vet_id: int, vet_call_sid: str = None):
    conn.execute("UPDATE ivr_sessions SET vet_id=?, vet_connected=1, vet_call_sid=? WHERE call_sid=?", (vet_id, vet_call_sid, call_sid))
    conn.execute("UPDATE ivr_calls SET status='IN_PROGRESS' WHERE call_sid=?", (call_sid,))
    # Add participant
    vet = conn.execute("SELECT mobile FROM users WHERE id=?", (vet_id,)).fetchone()
    phone = vet["mobile"] if vet else ""
    conn.execute("INSERT INTO ivr_call_participants (call_sid, user_id, phone_number, role) VALUES (?,?,?,?)", (call_sid, vet_id, phone, "vet"))
    conn.execute("INSERT INTO ivr_events (call_sid, event_type, details) VALUES (?,?,?)", (call_sid, "VET_CONNECTED", json.dumps({"vet_id": vet_id})))
    conn.commit()

def vet_call_ended(conn, call_sid: str, duration_seconds: int = 0, transcript: str = ""):
    # After vet call, we will trigger AI summarization job
    conn.execute("UPDATE ivr_sessions SET current_state=? WHERE call_sid=?", (STATE_COMPLETED, call_sid))
    if transcript:
        conn.execute("INSERT INTO ivr_transcripts (call_sid, speaker, text_original, language) VALUES (?,?,?,?)",
                     (call_sid, "vet", transcript, "en"))
    # Create async job for AI summarization
    conn.execute("INSERT INTO ivr_jobs (call_sid, job_type, payload) VALUES (?, 'AI_SUMMARIZE', ?)",
                 (call_sid, json.dumps({"transcript": transcript})))
    conn.commit()
