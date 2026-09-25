"""
Additive IVR schema.

Every statement is CREATE TABLE IF NOT EXISTS / CREATE INDEX IF NOT EXISTS so
the migration is idempotent and safe to run on an existing production
database. Existing tables are NEVER altered here except for the three
additive, nullable columns documented in `ensure_ivr_schema()`.
"""

IVR_SCHEMA = """
-- ============================ IVR CALLS ============================
CREATE TABLE IF NOT EXISTS ivr_calls (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    provider TEXT NOT NULL DEFAULT 'none',
    provider_call_id TEXT,
    ivr_phone_number TEXT,
    caller_number_encrypted TEXT,
    caller_number_hash TEXT,
    caller_number_masked TEXT,
    caller_number_status TEXT DEFAULT 'NOT_AVAILABLE'
        CHECK(caller_number_status IN ('CAPTURED','WITHHELD','USER_PROVIDED','NOT_AVAILABLE')),
    caller_country TEXT,
    caller_network TEXT,
    language TEXT DEFAULT 'en',
    language_source TEXT DEFAULT 'DEFAULT' CHECK(language_source IN ('DTMF','SPEECH','DEFAULT','PROFILE')),
    direction TEXT DEFAULT 'inbound',
    flow TEXT,                       -- VET_CONNECT | SURVEY | ABANDONED | UNKNOWN
    status TEXT DEFAULT 'IN_PROGRESS'
        CHECK(status IN ('RINGING','IN_PROGRESS','VET_CONNECTING','VET_CONNECTED',
                         'SURVEY','COMPLETED','PARTIALLY_COMPLETED','FAILED','NO_ANSWER','BUSY')),
    started_at TEXT,
    answered_at TEXT,
    ended_at TEXT,
    duration_seconds INTEGER,
    consent_recording INTEGER DEFAULT 0,
    consent_recording_at TEXT,
    recording_requested INTEGER DEFAULT 0,
    provider_payload TEXT,
    created_at TEXT DEFAULT (datetime('now')),
    updated_at TEXT DEFAULT (datetime('now')),
    UNIQUE(provider, provider_call_id)
);

-- ========================== IVR SESSIONS ===========================
CREATE TABLE IF NOT EXISTS ivr_sessions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    call_id INTEGER REFERENCES ivr_calls(id) ON DELETE CASCADE,
    current_step TEXT NOT NULL DEFAULT 'LANGUAGE_SELECT',
    step_index INTEGER DEFAULT 0,
    survey_id INTEGER,
    language TEXT DEFAULT 'en',
    state TEXT,                       -- JSON blob (pending question, retries, answers in flight)
    completed INTEGER DEFAULT 0,
    created_at TEXT DEFAULT (datetime('now')),
    updated_at TEXT DEFAULT (datetime('now'))
);

-- =========================== IVR SURVEYS ===========================
CREATE TABLE IF NOT EXISTS ivr_surveys (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    code TEXT NOT NULL,
    version INTEGER DEFAULT 1,
    is_active INTEGER DEFAULT 1,
    definition TEXT NOT NULL,         -- JSON: {questions:[...]} with per-language text
    created_at TEXT DEFAULT (datetime('now')),
    updated_at TEXT DEFAULT (datetime('now'))
);

-- ========================== IVR RESPONSES ==========================
CREATE TABLE IF NOT EXISTS ivr_responses (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    call_id INTEGER REFERENCES ivr_calls(id) ON DELETE CASCADE,
    session_id INTEGER REFERENCES ivr_sessions(id) ON DELETE CASCADE,
    question_key TEXT NOT NULL,
    question_text TEXT,
    input_mode TEXT CHECK(input_mode IN ('DTMF','SPEECH','SYSTEM','SKIPPED','UNKNOWN')),
    raw_input TEXT,
    dtmf_value TEXT,
    transcript TEXT,
    normalized_value TEXT,
    option_label TEXT,
    confidence REAL,
    attempt INTEGER DEFAULT 1,
    confirmed INTEGER DEFAULT 0,
    provenance TEXT DEFAULT 'FARMER_REPORTED'
        CHECK(provenance IN ('FARMER_REPORTED','VET_VERIFIED','AI_GENERATED','SYSTEM_GENERATED')),
    created_at TEXT DEFAULT (datetime('now'))
);

-- ========================= IVR TRANSCRIPTS =========================
CREATE TABLE IF NOT EXISTS ivr_transcripts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    call_id INTEGER REFERENCES ivr_calls(id) ON DELETE CASCADE,
    participant_role TEXT DEFAULT 'FARMER' CHECK(participant_role IN ('FARMER','VET','SYSTEM')),
    language TEXT DEFAULT 'en',
    text TEXT NOT NULL,
    translated_text TEXT,
    stt_provider TEXT,
    stt_confidence REAL,
    source TEXT DEFAULT 'STT' CHECK(source IN ('STT','PROVIDER','SYSTEM','MANUAL')),
    provenance TEXT DEFAULT 'FARMER_REPORTED',
    created_at TEXT DEFAULT (datetime('now'))
);

-- ========================== IVR RECORDINGS =========================
CREATE TABLE IF NOT EXISTS ivr_recordings (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    call_id INTEGER REFERENCES ivr_calls(id) ON DELETE CASCADE,
    provider_recording_id TEXT,
    storage_backend TEXT DEFAULT 'provider',   -- provider | local | s3
    storage_uri TEXT,
    sha256 TEXT,
    mime_type TEXT,
    size_bytes INTEGER,
    duration_seconds INTEGER,
    status TEXT DEFAULT 'PENDING' CHECK(status IN ('PENDING','AVAILABLE','FAILED','DELETED','EXPIRED')),
    consent_obtained INTEGER DEFAULT 0,
    encrypted INTEGER DEFAULT 0,
    retention_until TEXT,
    created_at TEXT DEFAULT (datetime('now'))
);

-- ============================ IVR REPORTS ==========================
CREATE TABLE IF NOT EXISTS ivr_reports (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    report_no TEXT UNIQUE NOT NULL,
    call_id INTEGER REFERENCES ivr_calls(id) ON DELETE CASCADE,
    session_id INTEGER REFERENCES ivr_sessions(id),
    case_id INTEGER REFERENCES cases(id),
    animal_id INTEGER REFERENCES animals(id),
    owner_user_id INTEGER REFERENCES users(id),
    assigned_vet_id INTEGER REFERENCES users(id),
    language TEXT DEFAULT 'en',
    source TEXT DEFAULT 'IVR',
    flow TEXT,                                  -- VET_CALL | SURVEY
    status TEXT DEFAULT 'RECEIVED'
        CHECK(status IN ('RECEIVED','AI_SUMMARIZED','VET_NOTIFIED','UNDER_REVIEW',
                         'VET_CONTACTED','ACTION_RECOMMENDED','FOLLOW_UP','RESOLVED','CLOSED')),
    completion_state TEXT DEFAULT 'COMPLETE'
        CHECK(completion_state IN ('COMPLETE','PARTIALLY_COMPLETED')),
    missing_fields TEXT,
    structured_json TEXT,
    ai_summary TEXT,
    ai_model TEXT,
    ai_generated INTEGER DEFAULT 0,
    ai_verified_by_vet INTEGER DEFAULT 0,
    urgency TEXT DEFAULT 'MEDIUM' CHECK(urgency IN ('LOW','MEDIUM','HIGH','CRITICAL')),
    urgency_source TEXT DEFAULT 'RULE_BASED'
        CHECK(urgency_source IN ('RULE_BASED','AI_SUMMARY','VET_VERIFIED')),
    urgency_rules TEXT,
    original_transcript TEXT,
    translated_summary TEXT,
    location_source TEXT DEFAULT 'NOT_AVAILABLE'
        CHECK(location_source IN ('GPS','NETWORK','FARMER_PROVIDED','REGISTRY','NOT_AVAILABLE')),
    lat REAL,
    lng REAL,
    location_accuracy REAL,
    village TEXT,
    district TEXT,
    state TEXT,
    is_duplicate INTEGER DEFAULT 0,
    duplicate_of INTEGER REFERENCES ivr_reports(id),
    duplicate_reasons TEXT,
    merged_into INTEGER REFERENCES ivr_reports(id),
    notified_vet_at TEXT,
    notified_govt_at TEXT,
    created_at TEXT DEFAULT (datetime('now')),
    updated_at TEXT DEFAULT (datetime('now'))
);

-- ======================= IVR LOCATION CAPTURES =====================
CREATE TABLE IF NOT EXISTS ivr_location_captures (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    call_id INTEGER REFERENCES ivr_calls(id) ON DELETE CASCADE,
    report_id INTEGER REFERENCES ivr_reports(id),
    source TEXT NOT NULL CHECK(source IN ('GPS','NETWORK','FARMER_PROVIDED','REGISTRY','NOT_AVAILABLE')),
    lat REAL,
    lng REAL,
    accuracy_meters REAL,
    village TEXT,
    district TEXT,
    state TEXT,
    provider TEXT,
    token_hash TEXT,
    consent INTEGER DEFAULT 0,
    raw TEXT,
    captured_at TEXT DEFAULT (datetime('now')),
    expires_at TEXT
);

-- ============================ IVR EVENTS ===========================
CREATE TABLE IF NOT EXISTS ivr_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    call_id INTEGER REFERENCES ivr_calls(id) ON DELETE CASCADE,
    report_id INTEGER REFERENCES ivr_reports(id),
    event_type TEXT NOT NULL,
    actor TEXT DEFAULT 'system',
    actor_type TEXT DEFAULT 'SYSTEM'
        CHECK(actor_type IN ('SYSTEM','PROVIDER','FARMER','VET','GOVT','ADMIN')),
    details TEXT,
    created_at TEXT DEFAULT (datetime('now'))
);

-- ============================= IVR JOBS ============================
CREATE TABLE IF NOT EXISTS ivr_jobs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    job_type TEXT NOT NULL,
    payload TEXT,
    status TEXT DEFAULT 'PENDING'
        CHECK(status IN ('PENDING','RUNNING','SUCCEEDED','FAILED','DEAD')),
    attempts INTEGER DEFAULT 0,
    max_attempts INTEGER DEFAULT 4,
    run_after TEXT DEFAULT (datetime('now')),
    last_error TEXT,
    result TEXT,
    call_id INTEGER REFERENCES ivr_calls(id),
    report_id INTEGER REFERENCES ivr_reports(id),
    created_at TEXT DEFAULT (datetime('now')),
    updated_at TEXT DEFAULT (datetime('now'))
);

-- ======================= IVR WEBHOOK EVENTS ========================
CREATE TABLE IF NOT EXISTS ivr_webhook_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    provider TEXT NOT NULL,
    provider_event_id TEXT NOT NULL,
    event_type TEXT,
    payload_hash TEXT,
    signature_valid INTEGER DEFAULT 0,
    replay_rejected INTEGER DEFAULT 0,
    received_at TEXT DEFAULT (datetime('now')),
    processed_at TEXT,
    UNIQUE(provider, provider_event_id)
);

-- ===================== IVR VET AVAILABILITY ========================
CREATE TABLE IF NOT EXISTS ivr_vet_availability (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    vet_id INTEGER NOT NULL UNIQUE REFERENCES users(id) ON DELETE CASCADE,
    is_available INTEGER DEFAULT 0,
    available_from TEXT DEFAULT '08:00',
    available_to TEXT DEFAULT '20:00',
    max_open_cases INTEGER DEFAULT 20,
    district TEXT,
    notes TEXT,
    updated_by INTEGER REFERENCES users(id),
    updated_at TEXT DEFAULT (datetime('now'))
);

-- ============================ IVR CONFIG ===========================
CREATE TABLE IF NOT EXISTS ivr_config (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL,
    updated_by TEXT,
    updated_at TEXT DEFAULT (datetime('now'))
);

-- ========================= IVR RATE LIMITS =========================
CREATE TABLE IF NOT EXISTS ivr_rate_limits (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    bucket_key TEXT NOT NULL,
    window_start TEXT NOT NULL,
    count INTEGER DEFAULT 0,
    UNIQUE(bucket_key, window_start)
);

-- ============================= INDEXES =============================
CREATE INDEX IF NOT EXISTS idx_ivr_calls_call_id ON ivr_calls(provider_call_id);
CREATE INDEX IF NOT EXISTS idx_ivr_calls_hash ON ivr_calls(caller_number_hash);
CREATE INDEX IF NOT EXISTS idx_ivr_calls_status ON ivr_calls(status, created_at);
CREATE INDEX IF NOT EXISTS idx_ivr_sessions_call ON ivr_sessions(call_id);
CREATE INDEX IF NOT EXISTS idx_ivr_responses_call ON ivr_responses(call_id, question_key);
CREATE INDEX IF NOT EXISTS idx_ivr_transcripts_call ON ivr_transcripts(call_id);
CREATE INDEX IF NOT EXISTS idx_ivr_reports_status ON ivr_reports(status, created_at);
CREATE INDEX IF NOT EXISTS idx_ivr_reports_case ON ivr_reports(case_id);
CREATE INDEX IF NOT EXISTS idx_ivr_reports_district ON ivr_reports(district);
CREATE INDEX IF NOT EXISTS idx_ivr_events_call ON ivr_events(call_id, event_type);
CREATE INDEX IF NOT EXISTS idx_ivr_jobs_status ON ivr_jobs(status, run_after);
CREATE INDEX IF NOT EXISTS idx_ivr_webhook ON ivr_webhook_events(provider, provider_event_id);
"""


def ensure_ivr_schema(conn):
    """Create/upgrade the IVR tables. Safe to call on every boot."""
    conn.executescript(IVR_SCHEMA)
    _ensure_columns(conn)
    conn.commit()


def _ensure_columns(conn):
    """Additive column migrations for IVR tables created by older builds."""
    needed = {
        "ivr_calls": {
            "caller_number_status": "TEXT DEFAULT 'NOT_AVAILABLE'",
            "flow": "TEXT",
            "consent_recording": "INTEGER DEFAULT 0",
            "consent_recording_at": "TEXT",
            "recording_requested": "INTEGER DEFAULT 0",
            "language_source": "TEXT DEFAULT 'DEFAULT'",
        },
        "ivr_reports": {
            "completion_state": "TEXT DEFAULT 'COMPLETE'",
            "missing_fields": "TEXT",
            "urgency_source": "TEXT DEFAULT 'RULE_BASED'",
            "urgency_rules": "TEXT",
            "is_duplicate": "INTEGER DEFAULT 0",
            "duplicate_of": "INTEGER",
            "duplicate_reasons": "TEXT",
            "merged_into": "INTEGER",
            "ai_verified_by_vet": "INTEGER DEFAULT 0",
            "original_transcript": "TEXT",
            "translated_summary": "TEXT",
        },
        "ivr_sessions": {
            "survey_id": "INTEGER",
            "completed": "INTEGER DEFAULT 0",
        },
    }
    for table, cols in needed.items():
        existing = {r["name"] for r in conn.execute(f"PRAGMA table_info({table})").fetchall()}
        if not existing:  # table missing entirely -> created by IVR_SCHEMA
            continue
        for col, decl in cols.items():
            if col not in existing:
                conn.execute(f"ALTER TABLE {table} ADD COLUMN {col} {decl}")
