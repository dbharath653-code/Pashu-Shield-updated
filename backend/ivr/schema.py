"""
IVR Database Schema Extension
Adds IVR-specific tables while preserving existing schema.
All tables use IF NOT EXISTS and integrate with existing users/animals/cases.
"""

IVR_SCHEMA = """
-- IVR Calls: every inbound/outbound call record
CREATE TABLE IF NOT EXISTS ivr_calls (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    call_sid TEXT UNIQUE NOT NULL,
    provider TEXT DEFAULT 'mock',
    provider_call_sid TEXT,
    direction TEXT DEFAULT 'inbound' CHECK(direction IN ('inbound','outbound')),
    from_number TEXT,
    to_number TEXT,
    caller_number TEXT,
    caller_number_normalized TEXT,
    ivr_phone_number TEXT,
    status TEXT DEFAULT 'INITIATED' CHECK(status IN ('INITIATED','RINGING','IN_PROGRESS','COMPLETED','FAILED','NO_ANSWER','BUSY','CANCELED')),
    language TEXT DEFAULT 'en' CHECK(language IN ('en','te','hi','mr')),
    duration_seconds INTEGER DEFAULT 0,
    recording_url TEXT,
    recording_enabled INTEGER DEFAULT 0,
    recording_consent INTEGER DEFAULT 0,
    is_mock INTEGER DEFAULT 0,
    created_at TEXT DEFAULT (datetime('now')),
    updated_at TEXT DEFAULT (datetime('now')),
    ended_at TEXT
);

-- IVR Sessions: tracks state machine for active call
CREATE TABLE IF NOT EXISTS ivr_sessions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    call_sid TEXT NOT NULL REFERENCES ivr_calls(call_sid) ON DELETE CASCADE,
    current_state TEXT NOT NULL DEFAULT 'WELCOME',
    language TEXT DEFAULT 'en',
    menu_choice TEXT,
    vet_id INTEGER REFERENCES users(id),
    vet_connected INTEGER DEFAULT 0,
    vet_call_sid TEXT,
    survey_started INTEGER DEFAULT 0,
    survey_completed INTEGER DEFAULT 0,
    survey_partial INTEGER DEFAULT 0,
    current_question_key TEXT,
    current_question_index INTEGER DEFAULT 0,
    retry_count INTEGER DEFAULT 0,
    caller_user_id INTEGER REFERENCES users(id),
    created_at TEXT DEFAULT (datetime('now')),
    updated_at TEXT DEFAULT (datetime('now'))
);

-- IVR Survey Responses: individual answers per question
CREATE TABLE IF NOT EXISTS ivr_survey_responses (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    call_sid TEXT NOT NULL REFERENCES ivr_calls(call_sid) ON DELETE CASCADE,
    session_id INTEGER REFERENCES ivr_sessions(id) ON DELETE CASCADE,
    question_key TEXT NOT NULL,
    question_text TEXT,
    answer_raw TEXT,
    answer_normalized TEXT,
    answer_source TEXT DEFAULT 'dtmf' CHECK(answer_source IN ('dtmf','speech','manual')),
    confidence REAL DEFAULT 1.0,
    language TEXT DEFAULT 'en',
    dtmf_digit TEXT,
    transcript TEXT,
    is_confirmed INTEGER DEFAULT 0,
    created_at TEXT DEFAULT (datetime('now'))
);

-- IVR Reports: structured report generated from IVR (links to cases)
CREATE TABLE IF NOT EXISTS ivr_reports (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    report_no TEXT UNIQUE NOT NULL,
    call_sid TEXT NOT NULL REFERENCES ivr_calls(call_sid),
    session_id INTEGER REFERENCES ivr_sessions(id),
    case_id INTEGER REFERENCES cases(id),
    caller_number TEXT,
    caller_user_id INTEGER REFERENCES users(id),
    language TEXT DEFAULT 'en',
    status TEXT DEFAULT 'RECEIVED' CHECK(status IN ('RECEIVED','PARTIALLY_COMPLETED','AI_SUMMARIZED','VET_NOTIFIED','UNDER_REVIEW','VET_CONTACTED','ACTION_RECOMMENDED','FOLLOW_UP','RESOLVED','CLOSED','DUPLICATE_FLAGGED')),
    urgency TEXT DEFAULT 'MEDIUM' CHECK(urgency IN ('LOW','MEDIUM','HIGH','CRITICAL')),
    animal_species TEXT,
    animal_breed TEXT,
    animal_age TEXT,
    animal_sex TEXT,
    animal_count INTEGER DEFAULT 1,
    is_pregnant TEXT,
    symptoms TEXT,
    duration TEXT,
    severity TEXT,
    eating_status TEXT,
    drinking_status TEXT,
    temperature TEXT,
    vaccination_status TEXT,
    previous_disease TEXT,
    medicines_given TEXT,
    main_problem TEXT,
    additional_description TEXT,
    location_village TEXT,
    location_block TEXT,
    location_district TEXT,
    location_state TEXT DEFAULT 'Maharashtra',
    location_lat REAL,
    location_lng REAL,
    location_source TEXT DEFAULT 'NOT_AVAILABLE' CHECK(location_source IN ('GPS','NETWORK','FARMER_PROVIDED','SMS_LINK','NOT_AVAILABLE')),
    location_accuracy TEXT,
    farmer_name TEXT,
    is_duplicate INTEGER DEFAULT 0,
    duplicate_of_report_id INTEGER REFERENCES ivr_reports(id),
    ai_summary TEXT,
    ai_structured_json TEXT,
    ai_confidence REAL,
    transcript_full TEXT,
    call_duration_seconds INTEGER DEFAULT 0,
    created_at TEXT DEFAULT (datetime('now')),
    updated_at TEXT DEFAULT (datetime('now'))
);

-- Call Transcripts: full speech-to-text per call
CREATE TABLE IF NOT EXISTS ivr_transcripts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    call_sid TEXT NOT NULL REFERENCES ivr_calls(call_sid) ON DELETE CASCADE,
    segment_index INTEGER DEFAULT 0,
    speaker TEXT DEFAULT 'farmer' CHECK(speaker IN ('farmer','vet','system','ivr')),
    text_original TEXT,
    text_normalized TEXT,
    language TEXT DEFAULT 'en',
    confidence REAL,
    stt_provider TEXT DEFAULT 'whisper',
    is_final INTEGER DEFAULT 1,
    created_at TEXT DEFAULT (datetime('now'))
);

-- Call Recordings metadata
CREATE TABLE IF NOT EXISTS ivr_recordings (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    call_sid TEXT NOT NULL REFERENCES ivr_calls(call_sid) ON DELETE CASCADE,
    recording_sid TEXT UNIQUE,
    provider_recording_sid TEXT,
    url TEXT,
    status TEXT DEFAULT 'IN_PROGRESS' CHECK(status IN ('IN_PROGRESS','COMPLETED','FAILED','DELETED')),
    duration_seconds INTEGER DEFAULT 0,
    consent_obtained INTEGER DEFAULT 0,
    file_path TEXT,
    file_size_bytes INTEGER,
    created_at TEXT DEFAULT (datetime('now'))
);

-- Call Participants: who was on the call
CREATE TABLE IF NOT EXISTS ivr_call_participants (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    call_sid TEXT NOT NULL REFERENCES ivr_calls(call_sid) ON DELETE CASCADE,
    user_id INTEGER REFERENCES users(id),
    phone_number TEXT,
    role TEXT CHECK(role IN ('farmer','vet','system')),
    joined_at TEXT DEFAULT (datetime('now')),
    left_at TEXT,
    duration_seconds INTEGER DEFAULT 0
);

-- IVR Events: audit trail for call lifecycle
CREATE TABLE IF NOT EXISTS ivr_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    call_sid TEXT NOT NULL,
    session_id INTEGER REFERENCES ivr_sessions(id),
    event_type TEXT NOT NULL,
    from_state TEXT,
    to_state TEXT,
    details TEXT,
    actor TEXT DEFAULT 'system',
    created_at TEXT DEFAULT (datetime('now'))
);

-- IVR Jobs: async processing queue
CREATE TABLE IF NOT EXISTS ivr_jobs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    call_sid TEXT NOT NULL,
    job_type TEXT NOT NULL CHECK(job_type IN ('TRANSCRIBE','AI_SUMMARIZE','GENERATE_REPORT','NOTIFY_VET','NOTIFY_GOVT','SEND_SMS_LINK')),
    status TEXT DEFAULT 'PENDING' CHECK(status IN ('PENDING','PROCESSING','COMPLETED','FAILED','RETRYING')),
    payload TEXT,
    result TEXT,
    attempts INTEGER DEFAULT 0,
    max_attempts INTEGER DEFAULT 3,
    error_message TEXT,
    created_at TEXT DEFAULT (datetime('now')),
    updated_at TEXT DEFAULT (datetime('now')),
    completed_at TEXT
);

-- IVR Survey Config: configurable questions (JSON)
CREATE TABLE IF NOT EXISTS ivr_survey_config (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    config_key TEXT UNIQUE NOT NULL,
    config_value TEXT NOT NULL,
    description TEXT,
    updated_by INTEGER REFERENCES users(id),
    created_at TEXT DEFAULT (datetime('now')),
    updated_at TEXT DEFAULT (datetime('now'))
);

-- IVR Analytics daily aggregates
CREATE TABLE IF NOT EXISTS ivr_analytics_daily (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    date TEXT UNIQUE NOT NULL,
    total_calls INTEGER DEFAULT 0,
    completed_surveys INTEGER DEFAULT 0,
    abandoned_calls INTEGER DEFAULT 0,
    vet_connections INTEGER DEFAULT 0,
    vet_unavailable INTEGER DEFAULT 0,
    reports_generated INTEGER DEFAULT 0,
    avg_call_duration_seconds REAL DEFAULT 0,
    high_priority_reports INTEGER DEFAULT 0,
    created_at TEXT DEFAULT (datetime('now')),
    updated_at TEXT DEFAULT (datetime('now'))
);

-- Idempotency for webhooks
CREATE TABLE IF NOT EXISTS ivr_webhook_idempotency (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    idempotency_key TEXT UNIQUE NOT NULL,
    call_sid TEXT,
    endpoint TEXT,
    payload_hash TEXT,
    response_code INTEGER,
    response_body TEXT,
    created_at TEXT DEFAULT (datetime('now'))
);

-- Indexes
CREATE INDEX IF NOT EXISTS idx_ivr_calls_sid ON ivr_calls(call_sid);
CREATE INDEX IF NOT EXISTS idx_ivr_calls_caller ON ivr_calls(caller_number_normalized);
CREATE INDEX IF NOT EXISTS idx_ivr_calls_status ON ivr_calls(status);
CREATE INDEX IF NOT EXISTS idx_ivr_calls_created ON ivr_calls(created_at);
CREATE INDEX IF NOT EXISTS idx_ivr_sessions_call ON ivr_sessions(call_sid);
CREATE INDEX IF NOT EXISTS idx_ivr_responses_call ON ivr_survey_responses(call_sid);
CREATE INDEX IF NOT EXISTS idx_ivr_reports_case ON ivr_reports(case_id);
CREATE INDEX IF NOT EXISTS idx_ivr_reports_status ON ivr_reports(status);
CREATE INDEX IF NOT EXISTS idx_ivr_reports_urgency ON ivr_reports(urgency);
CREATE INDEX IF NOT EXISTS idx_ivr_reports_district ON ivr_reports(location_district);
CREATE INDEX IF NOT EXISTS idx_ivr_reports_caller ON ivr_reports(caller_number);
CREATE INDEX IF NOT EXISTS idx_ivr_transcripts_call ON ivr_transcripts(call_sid);
CREATE INDEX IF NOT EXISTS idx_ivr_events_call ON ivr_events(call_sid);
CREATE INDEX IF NOT EXISTS idx_ivr_jobs_status ON ivr_jobs(status, job_type);
CREATE INDEX IF NOT EXISTS idx_ivr_jobs_call ON ivr_jobs(call_sid);
"""

# Default survey config JSON (inserted on init)
DEFAULT_SURVEY_CONFIG = {
    "questions": [
        {"key": "farmer_name", "type": "speech", "required": False, "dtmf_fallback": True, "conditional": None},
        {"key": "species", "type": "dtmf_choice", "required": True, "options": {"1": "Cattle", "2": "Buffalo", "3": "Goat", "4": "Sheep", "5": "Poultry", "6": "Other"}, "dtmf_fallback": True},
        {"key": "animal_count", "type": "dtmf_number", "required": True},
        {"key": "breed", "type": "speech", "required": False},
        {"key": "age", "type": "dtmf_number", "required": False},
        {"key": "sex", "type": "dtmf_choice", "required": False, "options": {"1": "Female", "2": "Male", "3": "Unknown"}},
        {"key": "pregnancy", "type": "dtmf_choice", "required": False, "conditional": {"species": ["Cattle", "Buffalo", "Goat", "Sheep"]}, "options": {"1": "Yes", "2": "No", "3": "Not Applicable"}},
        {"key": "main_problem", "type": "dtmf_choice_speech", "required": True, "options": {"1": "Fever", "2": "Not eating", "3": "Diarrhea", "4": "Breathing difficulty", "5": "Skin lesions", "6": "Lameness", "7": "Other"}},
        {"key": "symptoms", "type": "speech", "required": False},
        {"key": "duration", "type": "dtmf_number", "required": True},
        {"key": "severity", "type": "dtmf_choice", "required": True, "options": {"1": "Mild", "2": "Medium", "3": "High", "4": "Critical"}},
        {"key": "eating", "type": "dtmf_choice", "required": True, "options": {"1": "Yes", "2": "No"}},
        {"key": "drinking", "type": "dtmf_choice", "required": True, "options": {"1": "Yes", "2": "No"}},
        {"key": "temperature", "type": "dtmf_number", "required": False},
        {"key": "vaccination", "type": "dtmf_choice", "required": False, "options": {"1": "Yes", "2": "No", "3": "Unknown"}},
        {"key": "previous_disease", "type": "dtmf_choice", "required": False, "options": {"1": "Yes", "2": "No"}},
        {"key": "medicines", "type": "speech", "required": False},
        {"key": "location_village", "type": "speech", "required": True},
        {"key": "location_district", "type": "speech", "required": True},
        {"key": "location_state", "type": "dtmf_choice_speech", "required": False, "options": {"1": "Maharashtra", "2": "Other"}},
        {"key": "additional", "type": "speech", "required": False}
    ],
    "dtmf_controls": {"repeat": "9", "go_back": "0", "skip": "#", "confirm_yes": "1", "confirm_no": "2"},
    "max_retries": 3,
    "confirm_important": ["species", "severity", "location_district"]
}

import json as _json
DEFAULT_SURVEY_CONFIG_JSON = _json.dumps(DEFAULT_SURVEY_CONFIG)
