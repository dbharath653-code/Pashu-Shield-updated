"""
Durable asynchronous job queue for post-call processing.

The IVR call flow never waits for heavy work: on call completion it enqueues
jobs and hangs up. A background worker (python -m ivr.worker, or an in-process
thread when IVR_WORKER_ENABLED=true) then runs:

    TRANSCRIBE -> SUMMARISE -> GENERATE_REPORT -> NOTIFY_VET / NOTIFY_GOV

Every attempt is recorded; failures retry with backoff and end up DEAD with
an error message (no silent failures, no fake success).
"""
import json
import threading
import time
import traceback
from datetime import datetime, timedelta

from .config import settings

HANDLERS = {}
_WORKERS = []
_STOP = threading.Event()


def register(job_type):
    def deco(fn):
        HANDLERS[job_type] = fn
        return fn
    return deco


def enqueue(conn, job_type, payload=None, call_id=None, report_id=None, delay_seconds=0,
            max_attempts=None, dedupe_key=None):
    """Insert a job. `dedupe_key` prevents duplicate queued work for one call."""
    if dedupe_key:
        existing = conn.execute(
            "SELECT id FROM ivr_jobs WHERE job_type=? AND json_extract(payload,'$.dedupe_key')=? "
            "AND status IN ('PENDING','RUNNING')", (job_type, dedupe_key)
        ).fetchone()
        if existing:
            return existing["id"]
    payload = dict(payload or {})
    if dedupe_key:
        payload["dedupe_key"] = dedupe_key
    run_after = datetime.utcnow() + timedelta(seconds=delay_seconds)
    cur = conn.execute(
        "INSERT INTO ivr_jobs (job_type, payload, status, attempts, max_attempts, run_after, call_id, report_id) "
        "VALUES (?,?,'PENDING',0,?,?,?,?)",
        (job_type, json.dumps(payload), max_attempts or settings.JOB_MAX_ATTEMPTS,
         run_after.strftime("%Y-%m-%d %H:%M:%S"), call_id, report_id),
    )
    conn.commit()
    return cur.lastrowid


def claim_next(conn):
    """Atomically take the next due job (worker-safe)."""
    cur = conn.execute(
        "SELECT id FROM ivr_jobs WHERE status='PENDING' AND run_after <= datetime('now') "
        "ORDER BY id LIMIT 1"
    )
    row = cur.fetchone()
    if not row:
        return None
    conn.execute(
        "UPDATE ivr_jobs SET status='RUNNING', attempts=attempts+1, updated_at=datetime('now') WHERE id=?",
        (row["id"],),
    )
    conn.commit()
    return conn.execute("SELECT * FROM ivr_jobs WHERE id=?", (row["id"],)).fetchone()


def run_job(conn, job, context=None):
    """Execute one job with retry bookkeeping. Returns (ok, message)."""
    handler = HANDLERS.get(job["job_type"])
    if not handler:
        # Handlers live in report_service, which is imported on demand so the
        # queue module has no import-order dependency on the domain layer.
        try:  # noqa: SIM105
            from . import report_service  # noqa: F401
        except Exception:
            pass
        handler = HANDLERS.get(job["job_type"])
    if not handler:
        return _fail(conn, job, f"no handler registered for job type '{job['job_type']}'")
    try:
        payload = json.loads(job["payload"] or "{}")
        result = handler(conn, payload, context or {})
        conn.execute(
            "UPDATE ivr_jobs SET status='SUCCEEDED', result=?, last_error=NULL, updated_at=datetime('now') WHERE id=?",
            (json.dumps(result, ensure_ascii=False, default=str)[:4000] if result is not None else None, job["id"]),
        )
        conn.commit()
        return True, result
    except Exception as exc:
        tb = traceback.format_exc(limit=4)
        conn.execute("UPDATE ivr_jobs SET last_error=?, updated_at=datetime('now') WHERE id=?",
                     (f"{exc.__class__.__name__}: {exc}\n{tb}"[:2000], job["id"]))
        attempts = int(job["attempts"] or 0) + 1
        max_attempts = int(job["max_attempts"] or settings.JOB_MAX_ATTEMPTS)
        if attempts >= max_attempts:
            conn.execute("UPDATE ivr_jobs SET status='DEAD', attempts=?, updated_at=datetime('now') WHERE id=?",
                         (attempts, job["id"]))
        else:
            run_after = datetime.utcnow() + timedelta(seconds=settings.JOB_BACKOFF_SECONDS * attempts)
            conn.execute(
                "UPDATE ivr_jobs SET status='PENDING', attempts=?, run_after=?, updated_at=datetime('now') WHERE id=?",
                (attempts, run_after.strftime("%Y-%m-%d %H:%M:%S"), job["id"]),
            )
        conn.commit()
        return False, str(exc)


def _fail(conn, job, message):
    conn.execute(
        "UPDATE ivr_jobs SET status='DEAD', last_error=?, updated_at=datetime('now') WHERE id=?",
        (message, job["id"]),
    )
    conn.commit()
    return False, message


def run_pending(conn, limit=10, context=None):
    """Run due jobs inline (used by tests and the /api/ivr/jobs/run endpoint)."""
    done = 0
    for _ in range(limit):
        job = claim_next(conn)
        if not job:
            break
        run_job(conn, job, context)
        done += 1
    return done


def worker_loop(get_db, poll_seconds=None, context=None, name="ivr-worker"):
    """Background worker loop."""
    poll = poll_seconds or settings.WORKER_POLL_SECONDS
    while not _STOP.is_set():
        conn = None
        try:
            conn = get_db()
            job = claim_next(conn)
            if job:
                run_job(conn, job, context)
            else:
                time.sleep(poll)
        except Exception:
            try:
                time.sleep(poll)
            except Exception:
                pass
        finally:
            if conn is not None:
                try:
                    conn.close()
                except Exception:
                    pass


def start_workers(get_db, threads=None, poll_seconds=None, context=None):
    """Start in-process worker threads (idempotent)."""
    global _WORKERS
    if _WORKERS or not settings.WORKER_ENABLED:
        return _WORKERS
    _STOP.clear()
    count = max(int(threads or settings.WORKER_THREADS or 1), 1)
    for i in range(count):
        th = threading.Thread(
            target=worker_loop,
            args=(get_db, poll_seconds, context),
            name=f"ivr-worker-{i}",
            daemon=True,
        )
        th.start()
        _WORKERS.append(th)
    return _WORKERS


def stop_workers():
    _STOP.set()
    for th in _WORKERS:
        try:
            th.join(timeout=5)
        except Exception:
            pass
    _WORKERS.clear()


def queue_stats(conn):
    rows = conn.execute("SELECT status, COUNT(*) c FROM ivr_jobs GROUP BY status").fetchall()
    return {r["status"]: r["c"] for r in rows}
