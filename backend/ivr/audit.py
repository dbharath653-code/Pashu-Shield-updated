"""
Audit trail for the IVR channel.

Two complementary records are written for every significant step:

  ivr_events    - IVR specific, call/report scoped, machine readable
  audit_events  - the EXISTING platform audit table, so IVR activity appears
                  in the same audit log as the web application
"""
import json

ACTOR_TYPES = ("SYSTEM", "PROVIDER", "FARMER", "VET", "GOVT", "ADMIN")


def log_event(conn, call_id, event_type, actor="system", actor_type="SYSTEM",
              report_id=None, details=None):
    conn.execute(
        "INSERT INTO ivr_events (call_id, report_id, event_type, actor, actor_type, details) "
        "VALUES (?,?,?,?,?,?)",
        (call_id, report_id, event_type, actor,
         actor_type if actor_type in ACTOR_TYPES else "SYSTEM",
         json.dumps(details, ensure_ascii=False, default=str) if details else None),
    )
    return None


def audit(conn, context, action, entity_type, entity_id, details=None,
          actor_name="IVR System", actor_role="SYSTEM"):
    fn = (context or {}).get("audit_log")
    if not fn:
        return
    try:
        fn(conn, action, entity_type, entity_id, actor_name=actor_name,
           actor_role=actor_role, details=details)
    except Exception:
        # An audit failure must never break a farmer's report.
        pass


def events_for(conn, call_id=None, report_id=None, limit=200):
    query = "SELECT * FROM ivr_events WHERE 1=1"
    params = []
    if call_id:
        query += " AND call_id=?"
        params.append(call_id)
    if report_id:
        query += " AND report_id=?"
        params.append(report_id)
    query += " ORDER BY id DESC LIMIT ?"
    params.append(limit)
    return [dict(r) for r in conn.execute(query, params).fetchall()]
