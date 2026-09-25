"""
Background worker for IVR post-call processing.

    python -m ivr.worker

Consumes the durable ivr_jobs queue: TRANSCRIBE -> SUMMARISE ->
GENERATE_REPORT -> NOTIFY_VET / NOTIFY_GOV. Run at least one worker in
production (see render.yaml: the `pashu-shield-ivr-worker` service).
"""
import signal
import sys
import time

from database import audit_log, get_db, init_db

from . import jobs
from .config import settings
from .providers import get_provider


def _notify(conn, user_id, message, type_="info"):
    """Same semantics as the web app's notify() helper."""
    conn.execute("INSERT INTO notifications (user_id, message, type) VALUES (?,?,?)",
                 (user_id, message, type_))


def build_context():
    return {
        "notify": _notify,
        "audit_log": audit_log,
        "provider": get_provider(),
        "get_db": get_db,
    }


def main():
    init_db()
    context = build_context()
    print(f"[ivr-worker] provider={get_provider().name} "
          f"stt={settings.STT_PROVIDER} ai={settings.AI_PROVIDER} "
          f"recording={settings.RECORDING_ENABLED}")
    running = {"value": True}

    def stop(signum, frame):
        running["value"] = False
        print("[ivr-worker] stopping…")

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)

    processed = 0
    while running["value"]:
        conn = get_db()
        try:
            job = jobs.claim_next(conn)
            if job:
                ok, result = jobs.run_job(conn, job, context)
                processed += 1
                print(f"[ivr-worker] job {job['id']} {job['job_type']} -> "
                      f"{'ok' if ok else 'FAIL'}: {str(result)[:160]}")
                time.sleep(0.2)
            else:
                conn.close()
                conn = None
                time.sleep(settings.WORKER_POLL_SECONDS)
        except Exception as exc:  # keep the worker alive
            print(f"[ivr-worker] error: {exc}")
            time.sleep(settings.WORKER_POLL_SECONDS)
        finally:
            if conn is not None:
                try:
                    conn.close()
                except Exception:
                    pass
    print(f"[ivr-worker] stopped after {processed} job(s)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
