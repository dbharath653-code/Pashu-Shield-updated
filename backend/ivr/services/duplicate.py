"""
Duplicate detection for IVR reports.
Same caller + same animal (species) + same problem + short time window
"""
from datetime import datetime, timedelta
from typing import Optional, Tuple

def check_duplicate(conn, caller_normalized: str, species: str, main_problem: str, hours: int = 24) -> Tuple[bool, Optional[int]]:
    if not caller_normalized or not species or not main_problem:
        return False, None
    # Normalize for comparison
    species_norm = (species or "").strip().lower()
    problem_norm = (main_problem or "").strip().lower()
    if species_norm in ("not provided", "") or problem_norm in ("not provided", ""):
        return False, None

    # Look for recent reports from same caller
    # Use window_hours param, default 24
    rows = conn.execute(
        """
        SELECT id, animal_species, main_problem, created_at
        FROM ivr_reports
        WHERE caller_number = ? AND datetime(created_at) >= datetime('now', ?)
        ORDER BY id DESC
        LIMIT 10
        """,
        (caller_normalized, f"-{hours} hours")
    ).fetchall()
    for r in rows:
        r_species = (r["animal_species"] or "").strip().lower()
        r_problem = (r["main_problem"] or "").strip().lower()
        if r_species == species_norm and r_problem == problem_norm:
            return True, r["id"]
        # Also fuzzy: if both species matches and problem contains keyword
        if r_species == species_norm and (problem_norm in r_problem or r_problem in problem_norm):
            return True, r["id"]
    return False, None

def merge_reports(conn, keep_id: int, duplicate_id: int, actor: str = "system"):
    """Merge duplicate into keep - mark duplicate as resolved and link."""
    conn.execute("UPDATE ivr_reports SET status='DUPLICATE_FLAGGED', duplicate_of_report_id=?, updated_at=datetime('now') WHERE id=?", (keep_id, duplicate_id))
    try:
        from database import audit_log
        audit_log(conn, "MERGE_IVR_DUPLICATE", "ivr_report", duplicate_id, details={"merged_into": keep_id}, actor_name=actor)
    except Exception:
        pass
    conn.commit()
