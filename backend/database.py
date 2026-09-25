"""
Database layer for the SIH Animal Disease Management platform.
Uses plain sqlite3 (stdlib) - no ORM required, keeps the hackathon
prototype dependency-free and easy to run.
Extended with end-to-end support for:
- QR-based Animal & Sample Identity
- Pregnancy & Reproductive Health
- Medication & Allergy Profiles
- Digital Sample Lifecycle & Chain of Custody
- Laboratory Staff & Testing Workflow
- Structured Treatment Responses
- Farm & National Disease Intelligence
- Real Weather Observations
- Individual Animal AI Decision Support Assessments
- Audit Events & Offline Synchronization
"""
import sqlite3
import os
import secrets
import hashlib
import json
import uuid
from datetime import datetime, date, timedelta

try:
    import fcntl  # Unix-only; Windows dev falls back to unlocked init
except ImportError:  # pragma: no cover
    fcntl = None

DB_PATH = os.environ.get("SIH_DB_PATH") or os.path.join(os.path.dirname(__file__), "animal_health.db")

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    full_name TEXT NOT NULL,
    mobile TEXT UNIQUE NOT NULL,
    email TEXT UNIQUE NOT NULL,
    password_hash TEXT NOT NULL,
    salt TEXT NOT NULL,
    role TEXT NOT NULL CHECK(role IN ('owner','vet','govt','lab')),
    specialization TEXT,
    village TEXT,
    block TEXT,
    district TEXT,
    state TEXT DEFAULT 'Maharashtra',
    preferred_language TEXT,
    availability_status TEXT,
    is_seed INTEGER DEFAULT 0,
    created_at TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS herds (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    herd_code TEXT UNIQUE NOT NULL,
    owner_id INTEGER NOT NULL REFERENCES users(id),
    village TEXT, block TEXT, district TEXT,
    state TEXT DEFAULT 'Maharashtra',
    is_seed INTEGER DEFAULT 0,
    created_at TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS animals (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    animal_code TEXT UNIQUE NOT NULL,
    owner_id INTEGER NOT NULL REFERENCES users(id),
    herd_id INTEGER REFERENCES herds(id),
    animal_name TEXT,
    animal_type TEXT,
    species TEXT NOT NULL,
    breed TEXT,
    gender TEXT,
    sex TEXT,
    age REAL,
    age_years REAL,
    owner_name TEXT,
    mobile TEXT,
    village TEXT, block TEXT, district TEXT,
    state TEXT DEFAULT 'Maharashtra',
    status TEXT DEFAULT 'Healthy',
    is_seed INTEGER DEFAULT 0,
    created_at TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS cases (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    case_no TEXT UNIQUE NOT NULL,
    animal_id INTEGER NOT NULL REFERENCES animals(id),
    herd_id INTEGER REFERENCES herds(id),
    owner_id INTEGER NOT NULL REFERENCES users(id),
    vet_id INTEGER REFERENCES users(id),
    symptoms TEXT,
    disease_suspected TEXT,
    severity TEXT,
    description TEXT,
    reported_through TEXT DEFAULT 'Mobile App',
    status TEXT DEFAULT 'NEW',
    diagnosis TEXT,
    treatment TEXT,
    farm_alert_id INTEGER,
    is_seed INTEGER DEFAULT 0,
    created_at TEXT DEFAULT (datetime('now')),
    updated_at TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS case_updates (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    case_id INTEGER NOT NULL REFERENCES cases(id),
    status TEXT,
    note TEXT,
    updated_by TEXT,
    created_at TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS lab_requests (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    case_id INTEGER NOT NULL REFERENCES cases(id),
    animal_id INTEGER NOT NULL REFERENCES animals(id),
    herd_id INTEGER REFERENCES herds(id),
    sample_type TEXT,
    test_requested TEXT,
    priority TEXT DEFAULT 'Normal',
    notes TEXT,
    status TEXT DEFAULT 'REQUESTED',
    requested_by INTEGER REFERENCES users(id),
    is_seed INTEGER DEFAULT 0,
    created_at TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS lab_reports (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    report_no TEXT UNIQUE NOT NULL,
    lab_request_id INTEGER REFERENCES lab_requests(id),
    case_id INTEGER NOT NULL REFERENCES cases(id),
    animal_id INTEGER NOT NULL REFERENCES animals(id),
    herd_id INTEGER REFERENCES herds(id),
    sample TEXT,
    sample_id INTEGER REFERENCES samples(id),
    test_name TEXT,
    test_type TEXT,
    test_method TEXT,
    result TEXT,
    quantitative_result REAL,
    units TEXT,
    reference_range_min REAL,
    reference_range_max REAL,
    reference_range_text TEXT,
    abnormal_flag TEXT DEFAULT 'Normal',
    technician_name TEXT,
    verification_status TEXT DEFAULT 'UNVERIFIED',
    verified_by INTEGER REFERENCES users(id),
    verified_at TEXT,
    comments TEXT,
    published_at TEXT,
    test_date TEXT,
    notes TEXT,
    entered_by INTEGER REFERENCES users(id),
    is_seed INTEGER DEFAULT 0,
    created_at TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS prescriptions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    case_id INTEGER NOT NULL REFERENCES cases(id),
    animal_id INTEGER NOT NULL REFERENCES animals(id),
    herd_id INTEGER REFERENCES herds(id),
    diagnosis TEXT,
    medicine TEXT,
    dosage TEXT,
    frequency TEXT,
    duration TEXT,
    instructions TEXT,
    follow_up_date TEXT,
    allergy_override INTEGER DEFAULT 0,
    override_reason TEXT,
    vet_id INTEGER REFERENCES users(id),
    is_seed INTEGER DEFAULT 0,
    created_at TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS vaccinations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    animal_id INTEGER NOT NULL REFERENCES animals(id),
    vaccine TEXT NOT NULL,
    date_given TEXT,
    next_due_date TEXT,
    vet_id INTEGER REFERENCES users(id),
    is_seed INTEGER DEFAULT 0,
    created_at TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS notifications (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL REFERENCES users(id),
    message TEXT NOT NULL,
    type TEXT DEFAULT 'info',
    is_read INTEGER DEFAULT 0,
    created_at TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS vaccine_stock (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    district TEXT NOT NULL,
    vaccine TEXT NOT NULL,
    doses_available INTEGER NOT NULL DEFAULT 0,
    updated_at TEXT DEFAULT (datetime('now')),
    UNIQUE(district, vaccine)
);

CREATE TABLE IF NOT EXISTS vaccination_campaigns (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    campaign_code TEXT UNIQUE NOT NULL,
    name TEXT NOT NULL,
    district TEXT,
    vaccine TEXT NOT NULL,
    target_animals INTEGER DEFAULT 0,
    doses_administered INTEGER DEFAULT 0,
    start_date TEXT,
    end_date TEXT,
    status TEXT DEFAULT 'PLANNED',
    notes TEXT,
    created_by INTEGER REFERENCES users(id),
    is_seed INTEGER DEFAULT 0,
    created_at TEXT DEFAULT (datetime('now')),
    updated_at TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS case_visits (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    case_id INTEGER NOT NULL REFERENCES cases(id),
    vet_id INTEGER REFERENCES users(id),
    status TEXT DEFAULT 'ON_THE_WAY',
    from_lat REAL, from_lng REAL,
    to_lat REAL, to_lng REAL,
    travel_seconds INTEGER DEFAULT 240,
    started_at TEXT DEFAULT (datetime('now')),
    arrived_at TEXT,
    completed_at TEXT,
    created_at TEXT DEFAULT (datetime('now'))
);

-- ==================== NEW EXTENDED ENTITIES ====================

CREATE TABLE IF NOT EXISTS animal_qr_codes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    animal_id INTEGER NOT NULL UNIQUE REFERENCES animals(id) ON DELETE CASCADE,
    qr_token TEXT NOT NULL UNIQUE,
    qr_payload TEXT NOT NULL,
    status TEXT DEFAULT 'ACTIVE' CHECK(status IN ('ACTIVE','REVOKED')),
    created_at TEXT DEFAULT (datetime('now')),
    revoked_at TEXT,
    revoked_by INTEGER REFERENCES users(id)
);

CREATE TABLE IF NOT EXISTS animal_reproductive_records (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    animal_id INTEGER NOT NULL REFERENCES animals(id) ON DELETE CASCADE,
    pregnancy_status TEXT NOT NULL DEFAULT 'Not Pregnant' CHECK(pregnancy_status IN ('Not Pregnant','Suspected','Confirmed Pregnant','Lactating','Dry','Miscarried/Aborted')),
    breeding_date TEXT,
    mating_service_date TEXT,
    expected_delivery_date TEXT,
    pregnancy_confirmation_date TEXT,
    previous_pregnancies INTEGER DEFAULT 0,
    offspring_count INTEGER DEFAULT 0,
    event_type TEXT CHECK(event_type IN ('AI','Natural Service','Heat/Estrus','Pregnancy Check','Calving','Abortion','Other')),
    miscarriage_abortion_notes TEXT,
    breeding_notes TEXT,
    recorded_by INTEGER REFERENCES users(id),
    created_at TEXT DEFAULT (datetime('now')),
    updated_at TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS animal_allergies (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    animal_id INTEGER NOT NULL REFERENCES animals(id) ON DELETE CASCADE,
    allergen TEXT NOT NULL,
    allergy_severity TEXT NOT NULL DEFAULT 'Moderate' CHECK(allergy_severity IN ('Mild','Moderate','Severe','Life-Threatening')),
    reaction TEXT NOT NULL,
    date_recorded TEXT DEFAULT (date('now')),
    recorded_by INTEGER REFERENCES users(id),
    status TEXT DEFAULT 'Active' CHECK(status IN ('Active','Inactive')),
    notes TEXT,
    created_at TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS animal_medications (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    animal_id INTEGER NOT NULL REFERENCES animals(id) ON DELETE CASCADE,
    case_id INTEGER REFERENCES cases(id),
    prescription_id INTEGER REFERENCES prescriptions(id),
    medication_name TEXT NOT NULL,
    dosage TEXT,
    frequency TEXT,
    start_date TEXT,
    end_date TEXT,
    status TEXT DEFAULT 'Active' CHECK(status IN ('Active','Completed','Discontinued')),
    prescribed_by INTEGER REFERENCES users(id),
    allergy_override INTEGER DEFAULT 0,
    override_reason TEXT,
    notes TEXT,
    created_at TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS samples (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    sample_code TEXT UNIQUE NOT NULL,
    qr_token TEXT NOT NULL UNIQUE,
    qr_payload TEXT NOT NULL,
    animal_id INTEGER NOT NULL REFERENCES animals(id) ON DELETE CASCADE,
    case_id INTEGER NOT NULL REFERENCES cases(id) ON DELETE CASCADE,
    lab_request_id INTEGER REFERENCES lab_requests(id),
    sample_type TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'COLLECTED' CHECK(status IN ('COLLECTED','READY_FOR_PICKUP','PICKED_UP','IN_TRANSIT','ARRIVED_AT_LAB','LAB_RECEIVED','TESTING','RESULT_READY','COMPLETED','REJECTED')),
    collector_id INTEGER REFERENCES users(id),
    collection_lat REAL,
    collection_lng REAL,
    is_manual_location INTEGER DEFAULT 0,
    collection_notes TEXT,
    transporter_name TEXT,
    transporter_phone TEXT,
    rejection_reason TEXT,
    collected_at TEXT DEFAULT (datetime('now')),
    created_at TEXT DEFAULT (datetime('now')),
    updated_at TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS sample_custody_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    sample_id INTEGER NOT NULL REFERENCES samples(id) ON DELETE CASCADE,
    status TEXT NOT NULL,
    action TEXT NOT NULL,
    actor_id INTEGER REFERENCES users(id),
    actor_name TEXT,
    actor_role TEXT,
    lat REAL,
    lng REAL,
    is_manual_location INTEGER DEFAULT 0,
    notes TEXT,
    timestamp TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS treatment_responses (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    case_id INTEGER NOT NULL REFERENCES cases(id) ON DELETE CASCADE,
    animal_id INTEGER NOT NULL REFERENCES animals(id) ON DELETE CASCADE,
    prescription_id INTEGER REFERENCES prescriptions(id),
    response TEXT NOT NULL CHECK(response IN ('improved','unchanged','worsened','recovered','adverse_reaction','treatment_discontinued','follow_up_required')),
    response_date TEXT DEFAULT (date('now')),
    veterinarian_id INTEGER REFERENCES users(id),
    veterinarian_name TEXT,
    objective_observations TEXT,
    notes TEXT,
    created_at TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS farm_alerts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    herd_id INTEGER REFERENCES herds(id) ON DELETE CASCADE,
    herd_code TEXT NOT NULL,
    district TEXT NOT NULL,
    state TEXT DEFAULT 'Maharashtra',
    disease TEXT NOT NULL,
    affected_animals_count INTEGER DEFAULT 0,
    affected_animals_codes TEXT,
    risk_level TEXT NOT NULL CHECK(risk_level IN ('Low','Moderate','High','Critical')),
    trigger_reason TEXT NOT NULL,
    recommended_action TEXT,
    supporting_evidence TEXT,
    status TEXT DEFAULT 'ACTIVE' CHECK(status IN ('ACTIVE','ACKNOWLEDGED','RESOLVED')),
    acknowledged_by INTEGER REFERENCES users(id),
    acknowledged_at TEXT,
    resolved_by INTEGER REFERENCES users(id),
    resolved_at TEXT,
    created_at TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS national_alerts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    title TEXT NOT NULL,
    state TEXT NOT NULL,
    district TEXT,
    disease TEXT NOT NULL,
    alert_type TEXT CHECK(alert_type IN ('OUTBREAK','CLUSTER','VACCINATION_GAP','CROSS_STATE_TREND')),
    severity TEXT CHECK(severity IN ('MODERATE','HIGH','CRITICAL')),
    affected_count INTEGER DEFAULT 0,
    description TEXT NOT NULL,
    recommended_measures TEXT,
    status TEXT DEFAULT 'ACTIVE' CHECK(status IN ('ACTIVE','RESOLVED')),
    created_at TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS weather_observations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    district TEXT NOT NULL,
    state TEXT DEFAULT 'Maharashtra',
    lat REAL NOT NULL,
    lng REAL NOT NULL,
    temperature REAL NOT NULL,
    rainfall REAL NOT NULL,
    humidity REAL NOT NULL,
    weather_code INTEGER,
    source TEXT NOT NULL,
    recorded_at TEXT NOT NULL,
    fetched_at TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS ai_animal_assessments (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    animal_id INTEGER NOT NULL REFERENCES animals(id) ON DELETE CASCADE,
    case_id INTEGER REFERENCES cases(id),
    model_version TEXT NOT NULL DEFAULT 'v1.0-clinical-cds',
    risk_score REAL NOT NULL,
    risk_level TEXT NOT NULL,
    abnormal_findings TEXT,
    concern_categories TEXT,
    suggested_next_steps TEXT,
    follow_up_recommendations TEXT,
    explanation_factors TEXT,
    disclaimer TEXT NOT NULL DEFAULT 'AI-assisted decision support — veterinary confirmation required.',
    input_summary TEXT,
    confidence REAL NOT NULL,
    created_at TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS audit_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    actor_id INTEGER REFERENCES users(id),
    actor_name TEXT,
    actor_role TEXT,
    action TEXT NOT NULL,
    entity_type TEXT NOT NULL,
    entity_id TEXT,
    details TEXT,
    ip_address TEXT,
    created_at TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS offline_sync_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    client_txn_id TEXT UNIQUE NOT NULL,
    user_id INTEGER REFERENCES users(id),
    action TEXT NOT NULL,
    payload TEXT,
    synced_at TEXT DEFAULT (datetime('now'))
);

-- Indexes for high performance
CREATE INDEX IF NOT EXISTS idx_animal_qr_token ON animal_qr_codes(qr_token);
CREATE INDEX IF NOT EXISTS idx_animal_qr_animal ON animal_qr_codes(animal_id);
CREATE INDEX IF NOT EXISTS idx_samples_token ON samples(qr_token);
CREATE INDEX IF NOT EXISTS idx_samples_code ON samples(sample_code);
CREATE INDEX IF NOT EXISTS idx_samples_case ON samples(case_id);
CREATE INDEX IF NOT EXISTS idx_samples_animal ON samples(animal_id);
CREATE INDEX IF NOT EXISTS idx_custody_sample ON sample_custody_events(sample_id);
CREATE INDEX IF NOT EXISTS idx_repro_animal ON animal_reproductive_records(animal_id);
CREATE INDEX IF NOT EXISTS idx_allergy_animal ON animal_allergies(animal_id);
CREATE INDEX IF NOT EXISTS idx_medication_animal ON animal_medications(animal_id);
CREATE INDEX IF NOT EXISTS idx_treatment_case ON treatment_responses(case_id);
CREATE INDEX IF NOT EXISTS idx_audit_entity ON audit_events(entity_type, entity_id);
CREATE INDEX IF NOT EXISTS idx_notifications_user ON notifications(user_id, is_read);
CREATE INDEX IF NOT EXISTS idx_weather_dist ON weather_observations(district, fetched_at);
"""


def get_db():
    # Ensure the DB directory exists (production persistent disk may be an empty mount).
    _db_dir = os.path.dirname(os.path.abspath(DB_PATH))
    if _db_dir and not os.path.isdir(_db_dir):
        os.makedirs(_db_dir, exist_ok=True)
    conn = sqlite3.connect(DB_PATH, timeout=30.0, check_same_thread=False, isolation_level=None)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA busy_timeout = 5000")
    conn.execute("PRAGMA synchronous = NORMAL")
    return conn


def hash_password(password: str, salt: str = None):
    salt = salt or secrets.token_hex(16)
    pw_hash = hashlib.pbkdf2_hmac("sha256", password.encode(), salt.encode(), 120_000).hex()
    return pw_hash, salt


def verify_password(password: str, salt: str, pw_hash: str) -> bool:
    test_hash, _ = hash_password(password, salt)
    return secrets.compare_digest(test_hash, pw_hash)


def next_code(conn, prefix, table, code_col, pad=6, district="PUN"):
    """Generate a sequential human readable code like CASE-000812 or MH-PUN-000123."""
    cur = conn.execute(f"SELECT COUNT(*) c FROM {table}")
    n = cur.fetchone()["c"] + 1
    if prefix == "MH":
        return f"MH-{district}-{str(n).zfill(pad)}"
    if prefix == "HERD":
        return f"HERD-MH-{district}-{str(1000+n)}"
    if prefix == "SMP":
        return f"SMP-MH-{district}-{str(100+n)}"
    return f"{prefix}-{str(n).zfill(pad)}"


def audit_log(conn, action: str, entity_type: str, entity_id: str,
              actor_id: int = None, actor_name: str = None, actor_role: str = None,
              details: dict | str = None, ip: str = None):
    """Record an audit trail event for full traceability."""
    details_str = json.dumps(details) if isinstance(details, (dict, list)) else (details or "")
    conn.execute(
        "INSERT INTO audit_events (actor_id, actor_name, actor_role, action, entity_type, entity_id, details, ip_address) "
        "VALUES (?,?,?,?,?,?,?,?)",
        (actor_id, actor_name, actor_role, action, entity_type, str(entity_id), details_str, ip)
    )


def calculate_expected_delivery(species: str, breeding_date_str: str) -> str:
    """Calculate expected delivery date based on species-specific gestation periods:
    Cattle: ~283 days, Buffalo: ~310 days, Goat: ~150 days, Sheep: ~147 days."""
    try:
        b_date = datetime.strptime(str(breeding_date_str).strip()[:10], "%Y-%m-%d").date()
    except Exception:
        return ""
    sp = (species or "").strip().lower()
    if "buff" in sp:
        days = 310
    elif "goat" in sp:
        days = 150
    elif "sheep" in sp:
        days = 147
    else:
        days = 283
    return str(b_date + timedelta(days=days))


def ensure_ivr_columns(conn):
    """Additive IVR columns for existing databases (helpline channel, routing)."""
    def _add(table, col, coltype):
        cols = {row["name"] for row in conn.execute(f"PRAGMA table_info({table})").fetchall()}
        if col not in cols:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {col} {coltype}")
    _add("ivr_calls", "channel", "TEXT DEFAULT 'IVR'")
    _add("ivr_sessions", "region", "TEXT")
    _add("ivr_sessions", "routing_status", "TEXT")
    _add("ivr_sessions", "location_source", "TEXT")
    _add("ivr_reports", "source", "TEXT DEFAULT 'IVR'")
    conn.commit()


def migrate_ivr_reports_location_source(conn):
    """Rebuild ivr_reports once to allow PROFILE/DISTRICT_LEVEL/UNKNOWN location
    sources (SQLite cannot ALTER a CHECK constraint). Data-preserving: copies
    every existing column by name. No-op when already migrated."""
    sql_row = conn.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name='ivr_reports'").fetchone()
    if not sql_row or not sql_row["sql"]:
        return
    if "'PROFILE'" in sql_row["sql"]:
        return
    import re
    from ivr.schema import IVR_SCHEMA
    m = re.search(r"CREATE TABLE IF NOT EXISTS ivr_reports \(.*?\);\n", IVR_SCHEMA, re.S)
    if not m:
        print("migrate_ivr_reports_location_source: DDL not found, skipping")
        return
    new_ddl = m.group(0).replace("ivr_reports (", "ivr_reports_new (", 1)
    old_cols = [r["name"] for r in conn.execute("PRAGMA table_info(ivr_reports)").fetchall()]
    collist = ", ".join(old_cols)
    conn.executescript("PRAGMA foreign_keys=OFF;")
    conn.executescript(new_ddl)
    conn.execute(f"INSERT INTO ivr_reports_new ({collist}) SELECT {collist} FROM ivr_reports")
    conn.execute("DROP TABLE ivr_reports")
    conn.execute("ALTER TABLE ivr_reports_new RENAME TO ivr_reports")
    conn.executescript(IVR_SCHEMA)  # recreate indexes dropped with the old table
    conn.executescript("PRAGMA foreign_keys=ON;")
    conn.commit()
    print("migrate_ivr_reports_location_source: CHECK expanded (PROFILE/DISTRICT_LEVEL/UNKNOWN)")


def ensure_seed_vet_availability(conn):
    """Seed demo vets are explicitly AVAILABLE so automated tests and demos are
    deterministic regardless of wall-clock working hours. Real vets default to
    automatic (hours + call-state) availability."""
    conn.execute(
        "UPDATE users SET availability_status='AVAILABLE' "
        "WHERE role='vet' AND is_seed=1 AND availability_status IS NULL")
    conn.commit()


def init_ivr_schema(conn):
    """Initialize IVR extension tables and default configs."""
    try:
        from ivr.schema import IVR_SCHEMA, DEFAULT_SURVEY_CONFIG_JSON
        try:
            conn.executescript(IVR_SCHEMA)
        except sqlite3.OperationalError:
            # Old table versions may lack columns referenced by new indexes
            # (e.g. ivr_reports.source). Add columns first, then re-run; every
            # statement is IF NOT EXISTS so re-running is safe.
            ensure_ivr_columns(conn)
            conn.executescript(IVR_SCHEMA)
        ensure_ivr_columns(conn)
        migrate_ivr_reports_location_source(conn)
        # Insert default survey config if empty
        has_cfg = conn.execute("SELECT COUNT(*) c FROM ivr_survey_config WHERE config_key='survey_definition'").fetchone()["c"]
        if not has_cfg:
            conn.execute(
                "INSERT INTO ivr_survey_config (config_key, config_value, description) VALUES ('survey_definition', ?, 'Default IVR survey question definition')",
                (DEFAULT_SURVEY_CONFIG_JSON,)
            )
        conn.commit()
    except Exception as e:
        print(f"init_ivr_schema warning: {e}")

def init_db(reset=False):
    """Create schema + seeds. Safe under multi-worker WSGI: an exclusive
    cross-process file lock ensures only one worker initialises a fresh DB
    at a time (prevents half-seeded reads and duplicate-seed races)."""
    if fcntl is not None:
        _db_dir = os.path.dirname(os.path.abspath(DB_PATH))
        if _db_dir and not os.path.isdir(_db_dir):
            os.makedirs(_db_dir, exist_ok=True)
        with open(DB_PATH + ".init.lock", "w") as lockf:
            fcntl.flock(lockf, fcntl.LOCK_EX)
            try:
                return _init_db_inner(reset)
            finally:
                try:
                    fcntl.flock(lockf, fcntl.LOCK_UN)
                except Exception:
                    pass
    return _init_db_inner(reset)


def _init_db_inner(reset=False):
    if reset and os.path.exists(DB_PATH):
        os.remove(DB_PATH)
    first_time = not os.path.exists(DB_PATH)
    conn = get_db()
    conn.executescript(SCHEMA)
    ensure_animals_columns(conn)
    migrate_users_role(conn)
    ensure_new_columns(conn)
    conn.commit()
    if first_time:
        seed(conn)
    ensure_govt_and_stock(conn)
    ensure_campaigns(conn)
    ensure_lab_user(conn)
    ensure_qr_for_existing_animals(conn)
    ensure_extended_seeds(conn)
    ensure_seed_vet_availability(conn)
    conn.commit()
    # IVR schema (always ensure, even if not first_time, for upgrades)
    init_ivr_schema(conn)
    conn.close()


def ensure_campaigns(conn):
    """Seed a couple of vaccination campaigns the first time the table is empty."""
    count = conn.execute("SELECT COUNT(*) c FROM vaccination_campaigns").fetchone()["c"]
    if count:
        conn.commit()
        return
    today = date.today()
    rows = [
        ("CAMP-MH-PUN-1001", "FMD Mass Vaccination Drive — Pune", "Pune", "FMD",
         1200, 340, str(today - timedelta(days=10)), str(today + timedelta(days=20)), "ACTIVE",
         "Door-to-door FMD vaccination across Pune district herds."),
        ("CAMP-MH-NAS-1001", "HS & BQ Outbreak Prevention — Nashik", "Nashik", "HS",
         800, 0, str(today + timedelta(days=5)), str(today + timedelta(days=35)), "PLANNED",
         "Pre-monsoon Haemorrhagic Septicaemia prevention campaign."),
    ]
    for r in rows:
        conn.execute(
            "INSERT INTO vaccination_campaigns (campaign_code, name, district, vaccine, target_animals, "
            "doses_administered, start_date, end_date, status, notes, is_seed) VALUES (?,?,?,?,?,?,?,?,?,?,1)",
            r,
        )
    conn.commit()


def migrate_users_role(conn):
    """Ensure users table accepts ('owner','vet','govt','lab') without losing existing data."""
    sql_row = conn.execute("SELECT sql FROM sqlite_master WHERE type='table' AND name='users'").fetchone()
    if not sql_row:
        return
    sql = sql_row["sql"]
    if "'lab'" in sql:
        return
    conn.executescript("""
        PRAGMA foreign_keys=OFF;
        CREATE TABLE users_new (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            full_name TEXT NOT NULL,
            mobile TEXT UNIQUE NOT NULL,
            email TEXT UNIQUE NOT NULL,
            password_hash TEXT NOT NULL,
            salt TEXT NOT NULL,
            role TEXT NOT NULL CHECK(role IN ('owner','vet','govt','lab')),
            specialization TEXT,
            village TEXT, block TEXT, district TEXT,
            state TEXT DEFAULT 'Maharashtra',
            is_seed INTEGER DEFAULT 0,
            created_at TEXT DEFAULT (datetime('now'))
        );
        INSERT INTO users_new (id, full_name, mobile, email, password_hash, salt, role,
                               specialization, village, block, district, state, is_seed, created_at)
        SELECT id, full_name, mobile, email, password_hash, salt, role,
               specialization, village, block, district, state, is_seed, created_at
        FROM users;
        DROP TABLE users;
        ALTER TABLE users_new RENAME TO users;
        PRAGMA foreign_keys=ON;
    """)
    conn.commit()


def ensure_govt_and_stock(conn):
    has_govt = conn.execute("SELECT COUNT(*) c FROM users WHERE role='govt'").fetchone()["c"]
    if not has_govt:
        h, s = hash_password("password123")
        conn.execute(
            "INSERT INTO users (full_name, mobile, email, password_hash, salt, role, village, block, district, is_seed) "
            "VALUES (?,?,?,?,?,?,?,?,?,1)",
            ("Govt Officer Pune", "9800000020", "govt@example.com", h, s, "govt", "Pune", "Haveli", "Pune"),
        )
    stock_rows = conn.execute("SELECT COUNT(*) c FROM vaccine_stock").fetchone()["c"]
    if not stock_rows:
        for district, vaccine, doses in [
            ("Pune", "FMD", 1200), ("Pune", "HS", 800), ("Pune", "BQ", 500),
            ("Nashik", "FMD", 900), ("Nashik", "HS", 650), ("Nashik", "Brucellosis", 300),
        ]:
            conn.execute(
                "INSERT INTO vaccine_stock (district, vaccine, doses_available) VALUES (?,?,?)",
                (district, vaccine, doses),
            )
    conn.commit()


def ensure_lab_user(conn):
    """Ensure a dedicated Laboratory Technician account is seeded."""
    has_lab = conn.execute("SELECT COUNT(*) c FROM users WHERE role='lab'").fetchone()["c"]
    if not has_lab:
        h, s = hash_password("password123")
        conn.execute(
            "INSERT INTO users (full_name, mobile, email, password_hash, salt, role, specialization, village, block, district, is_seed) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,1)",
            ("Dr. Meera Joshi (Lab Tech)", "9800000030", "lab@example.com", h, s, "lab", "Veterinary Pathology", "Shivajinagar", "Haveli", "Pune"),
        )
        conn.commit()


def ensure_animals_columns(conn):
    existing_columns = {
        row["name"] for row in conn.execute("PRAGMA table_info(animals)").fetchall()
    }
    extra_columns = {
        "animal_name": "TEXT",
        "animal_type": "TEXT",
        "gender": "TEXT",
        "age": "REAL",
        "owner_name": "TEXT",
        "mobile": "TEXT",
        "state": "TEXT DEFAULT 'Maharashtra'",
    }
    for column_name, column_type in extra_columns.items():
        if column_name not in existing_columns:
            conn.execute(f"ALTER TABLE animals ADD COLUMN {column_name} {column_type}")

    conn.execute(
        """
        UPDATE animals
        SET
            animal_type = COALESCE(animal_type, species),
            gender = COALESCE(gender, sex),
            age = COALESCE(age, age_years),
            owner_name = COALESCE(
                owner_name,
                (SELECT full_name FROM users WHERE users.id = animals.owner_id)
            ),
            mobile = COALESCE(
                mobile,
                (SELECT mobile FROM users WHERE users.id = animals.owner_id)
            ),
            state = COALESCE(state, 'Maharashtra')
        """
    )
    conn.commit()


def ensure_new_columns(conn):
    """Safely add columns to existing tables if missing."""
    # lab_reports columns
    lr_cols = {row["name"] for row in conn.execute("PRAGMA table_info(lab_reports)").fetchall()}
    lr_needed = {
        "sample_id": "INTEGER",
        "test_type": "TEXT",
        "test_method": "TEXT",
        "quantitative_result": "REAL",
        "units": "TEXT",
        "reference_range_min": "REAL",
        "reference_range_max": "REAL",
        "reference_range_text": "TEXT",
        "abnormal_flag": "TEXT DEFAULT 'Normal'",
        "technician_name": "TEXT",
        "verification_status": "TEXT DEFAULT 'UNVERIFIED'",
        "verified_by": "INTEGER",
        "verified_at": "TEXT",
        "comments": "TEXT",
        "published_at": "TEXT",
    }
    for col, col_t in lr_needed.items():
        if col not in lr_cols:
            conn.execute(f"ALTER TABLE lab_reports ADD COLUMN {col} {col_t}")

    # prescriptions columns
    p_cols = {row["name"] for row in conn.execute("PRAGMA table_info(prescriptions)").fetchall()}
    p_needed = {
        "allergy_override": "INTEGER DEFAULT 0",
        "override_reason": "TEXT",
    }
    for col, col_t in p_needed.items():
        if col not in p_cols:
            conn.execute(f"ALTER TABLE prescriptions ADD COLUMN {col} {col_t}")

    # cases columns
    c_cols = {row["name"] for row in conn.execute("PRAGMA table_info(cases)").fetchall()}
    if "farm_alert_id" not in c_cols:
        conn.execute("ALTER TABLE cases ADD COLUMN farm_alert_id INTEGER")

    # herds columns
    h_cols = {row["name"] for row in conn.execute("PRAGMA table_info(herds)").fetchall()}
    if "state" not in h_cols:
        conn.execute("ALTER TABLE herds ADD COLUMN state TEXT DEFAULT 'Maharashtra'")

    # users columns (helpline: saved language + vet availability override)
    u_cols = {row["name"] for row in conn.execute("PRAGMA table_info(users)").fetchall()}
    if "preferred_language" not in u_cols:
        conn.execute("ALTER TABLE users ADD COLUMN preferred_language TEXT")
    if "availability_status" not in u_cols:
        conn.execute("ALTER TABLE users ADD COLUMN availability_status TEXT")

    conn.commit()


def ensure_qr_for_existing_animals(conn):
    """Ensure every existing registered animal has an active QR identity token."""
    animals = conn.execute("SELECT id, animal_code FROM animals").fetchall()
    for a in animals:
        exists = conn.execute("SELECT id FROM animal_qr_codes WHERE animal_id=?", (a["id"],)).fetchone()
        if not exists:
            token = f"aqr_{uuid.uuid4().hex}"
            payload = f"PASHU:ANIMAL:{token}"
            conn.execute(
                "INSERT INTO animal_qr_codes (animal_id, qr_token, qr_payload, status) VALUES (?,?,?, 'ACTIVE')",
                (a["id"], token, payload)
            )
            audit_log(conn, "CREATE_QR", "animal", a["id"], details={"animal_code": a["animal_code"], "qr_token": token})
    conn.commit()


def ensure_extended_seeds(conn):
    """Ensure reproductive records, allergies, sample records, and alerts have initial data."""
    # Check if a1 has a reproductive record
    a1 = conn.execute("SELECT id FROM animals WHERE animal_code='MH-PUN-000001'").fetchone()
    vet1 = conn.execute("SELECT id FROM users WHERE email='vet1@example.com'").fetchone()
    vet_id = vet1["id"] if vet1 else 1

    if a1:
        aid = a1["id"]
        # Seed reproductive record for cow Gauri
        has_repro = conn.execute("SELECT COUNT(*) c FROM animal_reproductive_records WHERE animal_id=?", (aid,)).fetchone()["c"]
        if not has_repro:
            today = date.today()
            breeding_d = str(today - timedelta(days=120))
            expected_d = calculate_expected_delivery("Cattle", breeding_d)
            conn.execute(
                """
                INSERT INTO animal_reproductive_records
                (animal_id, pregnancy_status, breeding_date, mating_service_date, expected_delivery_date,
                 pregnancy_confirmation_date, previous_pregnancies, offspring_count, event_type, breeding_notes, recorded_by)
                VALUES (?,?,?,?,?,?,2,2,'Pregnancy Check','Artificial Insemination confirmed pregnant via rectal palpation.',?)
                """,
                (aid, "Confirmed Pregnant", breeding_d, breeding_d, expected_d, str(today - timedelta(days=60)), vet_id)
            )

        # Seed allergy for cow Gauri (Penicillin allergy to test conflict checks)
        has_allergy = conn.execute("SELECT COUNT(*) c FROM animal_allergies WHERE animal_id=?", (aid,)).fetchone()["c"]
        if not has_allergy:
            conn.execute(
                """
                INSERT INTO animal_allergies (animal_id, allergen, allergy_severity, reaction, recorded_by, status, notes)
                VALUES (?, 'Penicillin', 'Severe', 'Anaphylactic distress and urticaria observed post-administration', ?, 'Active', 'Cross-reactive with beta-lactams and ampicillin.')
                """,
                (aid, vet_id)
            )

        # Seed active medication
        has_med = conn.execute("SELECT COUNT(*) c FROM animal_medications WHERE animal_id=?", (aid,)).fetchone()["c"]
        if not has_med:
            today = date.today()
            conn.execute(
                """
                INSERT INTO animal_medications (animal_id, medication_name, dosage, frequency, start_date, end_date, status, prescribed_by, notes)
                VALUES (?, 'Meloxicam', '0.5 mg/kg', 'Once daily', ?, ?, 'Active', ?, 'Anti-inflammatory treatment')
                """,
                (aid, str(today - timedelta(days=2)), str(today + timedelta(days=3)), vet_id)
            )

    # Seed initial digital sample for case 1 if not exists
    case1 = conn.execute("SELECT id, animal_id FROM cases WHERE case_no='CASE-000801'").fetchone()
    if case1:
        has_sample = conn.execute("SELECT COUNT(*) c FROM samples WHERE case_id=?", (case1["id"],)).fetchone()["c"]
        if not has_sample:
            stoken = f"sqr_{uuid.uuid4().hex}"
            spayload = f"PASHU:SAMPLE:{stoken}"
            scur = conn.execute(
                """
                INSERT INTO samples (sample_code, qr_token, qr_payload, animal_id, case_id, sample_type,
                                    status, collector_id, collection_lat, collection_lng, is_manual_location,
                                    collection_notes, transporter_name, transporter_phone)
                VALUES (?,?,?,?,?,'Blood Sample','COMPLETED',?,18.5793,73.9787,0,
                        'Sterile EDTA tube collection, 10ml blood','Sanjay Shinde','9822001122')
                """,
                ("SMP-MH-PUN-000101", stoken, spayload, case1["animal_id"], case1["id"], vet_id)
            )
            s_id = scur.lastrowid
            # Custody events
            t0 = datetime.now() - timedelta(hours=8)
            t1 = datetime.now() - timedelta(hours=6)
            t2 = datetime.now() - timedelta(hours=4)
            t3 = datetime.now() - timedelta(hours=2)
            conn.execute(
                "INSERT INTO sample_custody_events (sample_id, status, action, actor_name, actor_role, lat, lng, notes, timestamp) "
                "VALUES (?, 'COLLECTED', 'Sample drawn from jugular vein', 'Dr. Ananya Kulkarni', 'vet', 18.5793, 73.9787, 'Collected in Wagholi farm', ?)",
                (s_id, t0.strftime("%Y-%m-%d %H:%M:%S"))
            )
            conn.execute(
                "INSERT INTO sample_custody_events (sample_id, status, action, actor_name, actor_role, notes, timestamp) "
                "VALUES (?, 'PICKED_UP', 'Transferred to cold-chain courier', 'Sanjay Shinde', 'transporter', 'Cold chain 4°C maintained', ?)",
                (s_id, t1.strftime("%Y-%m-%d %H:%M:%S"))
            )
            conn.execute(
                "INSERT INTO sample_custody_events (sample_id, status, action, actor_name, actor_role, notes, timestamp) "
                "VALUES (?, 'LAB_RECEIVED', 'Received and verified at Pune District Lab', 'Dr. Meera Joshi (Lab Tech)', 'lab', 'Sample integrity verified', ?)",
                (s_id, t2.strftime("%Y-%m-%d %H:%M:%S"))
            )
            conn.execute(
                "INSERT INTO sample_custody_events (sample_id, status, action, actor_name, actor_role, notes, timestamp) "
                "VALUES (?, 'COMPLETED', 'Culture and microscopic examination completed', 'Dr. Meera Joshi (Lab Tech)', 'lab', 'Report LAB-000501 published', ?)",
                (s_id, t3.strftime("%Y-%m-%d %H:%M:%S"))
            )
            # Update existing lab report to link sample_id
            conn.execute(
                """
                UPDATE lab_reports
                SET sample_id=?, test_type='Bacteriology', test_method='Blood Culture & Gram Stain',
                    quantitative_result=0.0, units='CFU/mL', reference_range_text='No bacterial growth in 48h',
                    abnormal_flag='Normal', technician_name='Dr. Meera Joshi (Lab Tech)', verification_status='VERIFIED',
                    verified_at=datetime('now'), comments='Sterile blood culture, no Pasteurella multocida isolated'
                WHERE report_no='LAB-000501'
                """,
                (s_id,)
            )

    # Seed structured treatment response for case 1
    if case1:
        has_tr = conn.execute("SELECT COUNT(*) c FROM treatment_responses WHERE case_id=?", (case1["id"],)).fetchone()["c"]
        if not has_tr:
            conn.execute(
                """
                INSERT INTO treatment_responses (case_id, animal_id, response, response_date, veterinarian_id, veterinarian_name, objective_observations, notes)
                VALUES (?, ?, 'improved', date('now'), ?, 'Dr. Ananya Kulkarni', 'Body temperature normalized to 101.4°F, rumination resumed, feeding normally.', 'Continue oral hydration and monitor for 48 hours.')
                """,
                (case1["id"], case1["animal_id"], vet_id)
            )

    # Seed farm alert if none exists (resolve herd FK by code; skip if herd
    # seeding hasn't completed yet so a concurrent boot can never FK-fail).
    has_fa = conn.execute("SELECT COUNT(*) c FROM farm_alerts").fetchone()["c"]
    if not has_fa:
        _herd = conn.execute(
            "SELECT id FROM herds WHERE herd_code='HERD-MH-PUN-1001'").fetchone()
        if _herd:
            conn.execute(
                """
                INSERT INTO farm_alerts (herd_id, herd_code, district, disease, affected_animals_count, affected_animals_codes, risk_level, trigger_reason, recommended_action, supporting_evidence, status)
                VALUES (?, 'HERD-MH-PUN-1001', 'Pune', 'HS (suspected)', 1, 'MH-PUN-000001', 'Moderate',
                        'Active acute respiratory and fever case detected in Wagholi cluster',
                        'Perform preventive herd ring vaccination and temperature screening',
                        'Case CASE-000801 with medium severity symptoms reported', 'ACTIVE')
                """,
                (_herd["id"],),
            )

    # Seed national alert if none exists
    has_na = conn.execute("SELECT COUNT(*) c FROM national_alerts").fetchone()["c"]
    if not has_na:
        conn.execute(
            """
            INSERT INTO national_alerts (title, state, district, disease, alert_type, severity, affected_count, description, recommended_measures, status)
            VALUES ('Western Maharashtra HS Surveillance Alert', 'Maharashtra', 'Pune', 'HS', 'CLUSTER', 'HIGH', 1,
                    'Pre-monsoon Haemorrhagic Septicaemia surveillance alert across Pune and Satara districts.',
                    'Mandatory ring vaccination in 5km buffer zone around reported cases.', 'ACTIVE')
            """
        )

    conn.commit()


def seed(conn):
    def add_user(name, mobile, email, pw, role, district, village="Haveli", block="Haveli", spec=None):
        h, s = hash_password(pw)
        cur = conn.execute(
            "INSERT INTO users (full_name, mobile, email, password_hash, salt, role, specialization, village, block, district, is_seed) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,1)",
            (name, mobile, email, h, s, role, spec, village, block, district),
        )
        return cur.lastrowid

    owner1 = add_user("Rajesh Patil", "9800000001", "rajesh@example.com", "password123", "owner", "Pune")
    owner2 = add_user("Sunita More", "9800000002", "sunita@example.com", "password123", "owner", "Nashik")
    vet1 = add_user("Dr. Ananya Kulkarni", "9800000010", "vet1@example.com", "password123", "vet", "Pune", spec="Livestock Medicine")
    add_user("Dr. Suresh Deshmukh", "9800000011", "vet2@example.com", "password123", "vet", "Nashik", spec="Epidemiology")
    add_user("Dr. Meera Joshi (Lab Tech)", "9800000030", "lab@example.com", "password123", "lab", "Pune", spec="Veterinary Pathology")

    herd1 = conn.execute(
        "INSERT INTO herds (herd_code, owner_id, village, block, district, is_seed) VALUES (?,?,?,?,?,1)",
        ("HERD-MH-PUN-1001", owner1, "Wagholi", "Haveli", "Pune"),
    ).lastrowid

    a1 = conn.execute(
        "INSERT INTO animals (animal_code, owner_id, herd_id, animal_name, animal_type, species, breed, gender, sex, age, age_years, owner_name, mobile, village, block, district, status, is_seed) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,1)",
        ("MH-PUN-000001", owner1, herd1, "Gauri", "Cattle", "Cattle", "Gir", "Female", "Female", 4, 4,
         "Rajesh Patil", "9800000001", "Wagholi", "Haveli", "Pune", "Under Observation"),
    ).lastrowid
    a2 = conn.execute(
        "INSERT INTO animals (animal_code, owner_id, herd_id, animal_name, animal_type, species, breed, gender, sex, age, age_years, owner_name, mobile, village, block, district, status, is_seed) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,1)",
        ("MH-PUN-000002", owner1, herd1, "Laxmi", "Buffalo", "Buffalo", "Murrah", "Female", "Female", 6, 6,
         "Rajesh Patil", "9800000001", "Wagholi", "Haveli", "Pune", "Healthy"),
    ).lastrowid
    a3 = conn.execute(
        "INSERT INTO animals (animal_code, owner_id, herd_id, animal_name, animal_type, species, breed, gender, sex, age, age_years, owner_name, mobile, village, block, district, status, is_seed) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,1)",
        ("MH-NAS-000001", owner2, None, "Moti", "Goat", "Goat", "Osmanabadi", "Male", "Male", 2, 2,
         "Sunita More", "9800000002", "Deolali", "Nashik", "Nashik", "Healthy"),
    ).lastrowid

    case1 = conn.execute(
        "INSERT INTO cases (case_no, animal_id, herd_id, owner_id, vet_id, symptoms, disease_suspected, severity, description, reported_through, status, is_seed) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?,1)",
        ("CASE-000801", a1, herd1, owner1, vet1, "Fever, Reduced eating", "HS (suspected)", "Medium",
         "Animal appears lethargic since yesterday evening.", "Mobile App", "DIAGNOSED"),
    ).lastrowid

    conn.execute(
        "INSERT INTO case_updates (case_id, status, note, updated_by) VALUES (?,?,?,?)",
        (case1, "NEW", "Case reported by owner", "system"),
    )
    conn.execute(
        "INSERT INTO case_updates (case_id, status, note, updated_by) VALUES (?,?,?,?)",
        (case1, "DIAGNOSED", "Diagnosed as Haemorrhagic Septicaemia (suspected)", "Dr. Ananya Kulkarni"),
    )

    lr = conn.execute(
        "INSERT INTO lab_requests (case_id, animal_id, herd_id, sample_type, test_requested, priority, status, requested_by, is_seed) "
        "VALUES (?,?,?,?,?,?,?,?,1)",
        (case1, a1, herd1, "Blood Sample", "HS Culture Test", "High", "REPORT READY", vet1),
    ).lastrowid

    conn.execute(
        "INSERT INTO lab_reports (report_no, lab_request_id, case_id, animal_id, herd_id, sample, test_name, result, test_date, notes, entered_by, is_seed) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?,1)",
        ("LAB-000501", lr, case1, a1, herd1, "Blood", "HS", "NEGATIVE", str(date.today()),
         "No growth observed.", vet1),
    )

    conn.execute(
        "INSERT INTO prescriptions (case_id, animal_id, herd_id, diagnosis, medicine, dosage, frequency, duration, instructions, follow_up_date, vet_id, is_seed) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?,1)",
        (case1, a1, herd1, "HS (suspected), lab negative - viral fever likely", "Meloxicam", "0.5 mg/kg",
         "Once daily", "3 days", "Ensure animal has access to clean water and shade.",
         str(date.today() + timedelta(days=7)), vet1),
    )

    conn.execute(
        "INSERT INTO vaccinations (animal_id, vaccine, date_given, next_due_date, vet_id, is_seed) VALUES (?,?,?,?,?,1)",
        (a1, "FMD", str(date.today() - timedelta(days=180)), str(date.today() + timedelta(days=5)), vet1),
    )
    conn.execute(
        "INSERT INTO vaccinations (animal_id, vaccine, date_given, next_due_date, vet_id, is_seed) VALUES (?,?,?,?,?,1)",
        (a2, "HS", str(date.today() - timedelta(days=100)), str(date.today() + timedelta(days=60)), vet1),
    )

    conn.execute(
        "INSERT INTO notifications (user_id, message, type) VALUES (?,?,?)",
        (owner1, "Lab report for CASE-000801 is ready.", "lab"),
    )
    conn.execute(
        "INSERT INTO notifications (user_id, message, type) VALUES (?,?,?)",
        (owner1, "E-prescription available for CASE-000801.", "prescription"),
    )
    conn.execute(
        "INSERT INTO notifications (user_id, message, type) VALUES (?,?,?)",
        (vet1, "New user report received: CASE-000801.", "case"),
    )

    conn.commit()
