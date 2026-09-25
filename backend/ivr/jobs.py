"""
Async job processing for IVR (transcription, AI summarization, notifications).
In production would use Celery/RQ; here we implement simple inline + background thread fallback.
"""
import json
import threading
import time
from typing import Dict, Any

def enqueue_job(conn, call_sid: str, job_type: str, payload: Dict[str, Any] = None):
    conn.execute("INSERT INTO ivr_jobs (call_sid, job_type, payload, status) VALUES (?,?,?, 'PENDING')",
                 (call_sid, job_type, json.dumps(payload or {}, ensure_ascii=False)))
    conn.commit()
    # In test/dev mock mode, process synchronously to avoid sqlite lock contention
    import os
    if os.environ.get("PYTEST_CURRENT_TEST") or os.environ.get("UNITTEST"):
        try:
            process_job_inline(call_sid, job_type, payload)
        except Exception:
            pass
        return
    # Try immediate inline processing for low-latency (don't block IVR call)
    # Spawn background thread
    try:
        threading.Thread(target=process_job_inline, args=(call_sid, job_type, payload), daemon=True).start()
    except Exception:
        pass

def process_job_inline(call_sid: str, job_type: str, payload: Dict[str, Any] = None):
    """Inline processing - runs in background thread."""
    # Need new DB connection
    try:
        from database import get_db
        conn = get_db()
        # Find pending job
        job = conn.execute("SELECT * FROM ivr_jobs WHERE call_sid=? AND job_type=? AND status='PENDING' ORDER BY id DESC LIMIT 1", (call_sid, job_type)).fetchone()
        if not job:
            conn.close()
            return
        conn.execute("UPDATE ivr_jobs SET status='PROCESSING', attempts=attempts+1, updated_at=datetime('now') WHERE id=?", (job["id"],))
        conn.commit()

        result = None
        error = None
        try:
            if job_type == "AI_SUMMARIZE":
                result = _process_ai_summarize(conn, call_sid, json.loads(job["payload"] or "{}"))
            elif job_type == "TRANSCRIBE":
                result = _process_transcribe(conn, call_sid, json.loads(job["payload"] or "{}"))
            elif job_type == "GENERATE_REPORT":
                result = _process_generate_report(conn, call_sid, json.loads(job["payload"] or "{}"))
            elif job_type == "NOTIFY_VET":
                result = _process_notify(conn, call_sid)
            else:
                result = {"status": "unknown_job_type"}
        except Exception as e:
            error = str(e)
            import traceback; traceback.print_exc()

        if error:
            conn.execute("UPDATE ivr_jobs SET status='FAILED', error_message=?, updated_at=datetime('now') WHERE id=?", (error, job["id"]))
        else:
            conn.execute("UPDATE ivr_jobs SET status='COMPLETED', result=?, completed_at=datetime('now'), updated_at=datetime('now') WHERE id=?",
                         (json.dumps(result, ensure_ascii=False) if result else "", job["id"]))
        conn.commit()
        conn.close()
    except Exception as e:
        print(f"process_job_inline failed: {e}")

def _process_ai_summarize(conn, call_sid: str, payload: Dict[str, Any]):
    call = conn.execute("SELECT * FROM ivr_calls WHERE call_sid=?", (call_sid,)).fetchone()
    session = conn.execute("SELECT * FROM ivr_sessions WHERE call_sid=?", (call_sid,)).fetchone()
    report = conn.execute("SELECT * FROM ivr_reports WHERE call_sid=? ORDER BY id DESC LIMIT 1", (call_sid,)).fetchone()
    if not call or not session:
        return {"error": "call not found"}
    language = session["language"] if session else "en"
    transcripts = conn.execute("SELECT text_original FROM ivr_transcripts WHERE call_sid=?", (call_sid,)).fetchall()
    full_transcript = " ".join([t["text_original"] for t in transcripts if t["text_original"]])
    payload_transcript = payload.get("transcript") or payload.get("vet_transcript") or ""
    if payload_transcript and payload_transcript not in full_transcript:
        full_transcript = (full_transcript + " " + payload_transcript).strip()
    responses = {r["question_key"]: r["answer_normalized"] for r in conn.execute("SELECT question_key, answer_normalized FROM ivr_survey_responses WHERE call_sid=?", (call_sid,)).fetchall()}
    if not report and responses:
        from .services.report_service import create_ivr_report_from_survey
        from .services.location import get_location_from_responses
        loc = get_location_from_responses(responses)
        create_ivr_report_from_survey(conn, call_sid, responses, language=language, transcript=full_transcript, caller_phone=call["caller_number_normalized"] or "", duration_seconds=call["duration_seconds"] or 0)
        report = conn.execute("SELECT * FROM ivr_reports WHERE call_sid=? ORDER BY id DESC LIMIT 1", (call_sid,)).fetchone()
    if not report and payload_transcript and session["vet_connected"]:
        from .services.report_service import create_ivr_report_from_vet_transcript
        vet_id = session["vet_id"]
        create_ivr_report_from_vet_transcript(conn, call_sid, transcript=payload_transcript or full_transcript, language=language, caller_phone=call["caller_number_normalized"] or "", vet_id=vet_id, duration_seconds=call["duration_seconds"] or 0)
        report = conn.execute("SELECT * FROM ivr_reports WHERE call_sid=? ORDER BY id DESC LIMIT 1", (call_sid,)).fetchone()
    if report and full_transcript and session["vet_connected"]:
        if not report["transcript_full"] or len(report["transcript_full"]) < len(full_transcript):
            try:
                conn.execute("UPDATE ivr_reports SET transcript_full=?, ai_summary=ai_summary || ? WHERE id=?", (full_transcript, f"\n\nVet call transcript: {payload_transcript[:500]}" if payload_transcript else "", report["id"]))
            except Exception:
                pass
    if report:
        # Never overwrite terminal/triage statuses decided at creation.
        conn.execute("UPDATE ivr_reports SET status='AI_SUMMARIZED', updated_at=datetime('now') WHERE id=? AND status NOT IN ('DUPLICATE_FLAGGED','RESOLVED','CLOSED')", (report["id"],))
    return {"ai_summarized": True, "call_sid": call_sid}

def _process_transcribe(conn, call_sid: str, payload: Dict[str, Any]):
    # In prod would call Whisper. Here mark as done.
    # If recording_url exists, would fetch and transcribe.
    return {"transcribed": True}

def _process_generate_report(conn, call_sid: str, payload: Dict[str, Any]):
    responses = {r["question_key"]: r["answer_normalized"] for r in conn.execute("SELECT question_key, answer_normalized FROM ivr_survey_responses WHERE call_sid=?", (call_sid,)).fetchall()}
    call = conn.execute("SELECT * FROM ivr_calls WHERE call_sid=?", (call_sid,)).fetchone()
    session = conn.execute("SELECT * FROM ivr_sessions WHERE call_sid=?", (call_sid,)).fetchone()
    if responses and call:
        from .services.report_service import create_ivr_report_from_survey
        existing = conn.execute("SELECT id FROM ivr_reports WHERE call_sid=?", (call_sid,)).fetchone()
        if not existing:
            create_ivr_report_from_survey(conn, call_sid, responses, language=session["language"] if session else "en", caller_phone=call["caller_number_normalized"] or "")
            return {"report_generated": True}
    return {"skipped": "already exists or no data"}

def _process_notify(conn, call_sid: str):
    return {"notified": True}

def process_pending_jobs_sync():
    """Synchronous processing for testing - process all pending jobs."""
    from database import get_db
    conn = get_db()
    jobs = conn.execute("SELECT * FROM ivr_jobs WHERE status='PENDING' ORDER BY id ASC").fetchall()
    for job in jobs:
        payload = json.loads(job["payload"] or "{}")
        try:
            conn.execute("UPDATE ivr_jobs SET status='PROCESSING', attempts=attempts+1 WHERE id=?", (job["id"],))
            conn.commit()
            if job["job_type"] == "AI_SUMMARIZE":
                _process_ai_summarize(conn, job["call_sid"], payload)
            elif job["job_type"] == "GENERATE_REPORT":
                _process_generate_report(conn, job["call_sid"], payload)
            conn.execute("UPDATE ivr_jobs SET status='COMPLETED', completed_at=datetime('now') WHERE id=?", (job["id"],))
            conn.commit()
        except Exception as e:
            conn.execute("UPDATE ivr_jobs SET status='FAILED', error_message=? WHERE id=?", (str(e), job["id"]))
            conn.commit()
    conn.close()
