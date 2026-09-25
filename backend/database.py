"""
Database layer for the SIH Animal Disease Management platform.
Uses plain sqlite3 (stdlib) - no ORM required, keeps the hackathon
prototype dependency-free and easy to run.
"""
import sqlite3
import os
import secrets
import hashlib
from datetime import datetime, date

DB_PATH = os.path.join(os.path.dirname(__file__), "animal_health.db")

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    full_name TEXT NOT NULL,
    mobile TEXT UNIQUE NOT NULL,
    email TEXT UNIQUE NOT NULL,
    password_hash TEXT NOT NULL,
    salt TEXT NOT NULL,
    role TEXT NOT NULL CHECK(role IN ('owner','vet','govt')),
    specialization TEXT,
    village TEXT,
    block TEXT,
    district TEXT,
    state TEXT DEFAULT 'Maharashtra',
    is_seed INTEGER DEFAULT 0,
    created_at TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS herds (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    herd_code TEXT UNIQUE NOT NULL,
    owner_id INTEGER NOT NULL REFERENCES users(id),
    village TEXT, block TEXT, district TEXT,
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
    test_name TEXT,
    result TEXT,
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
"""


def get_db():
    conn = sqlite3.connect(DB_PATH, timeout=30.0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
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
    return f"{prefix}-{str(n).zfill(pad)}"


def init_db(reset=False):
    if reset and os.path.exists(DB_PATH):
        os.remove(DB_PATH)
    first_time = not os.path.exists(DB_PATH)
    conn = get_db()
    conn.executescript(SCHEMA)
    ensure_animals_columns(conn)
    migrate_users_role(conn)
    conn.commit()
    if first_time:
        seed(conn)
    ensure_govt_and_stock(conn)
    ensure_campaigns(conn)
    conn.close()


def ensure_campaigns(conn):
    """Seed a couple of vaccination campaigns the first time the table is empty.
    Uses CREATE TABLE IF NOT EXISTS in SCHEMA, so this never touches existing rows."""
    count = conn.execute("SELECT COUNT(*) c FROM vaccination_campaigns").fetchone()["c"]
    if count:
        conn.commit()
        return
    from datetime import timedelta
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
    """Older databases constrain role to ('owner','vet'); rebuild the table
    so the government role is accepted without losing any existing users."""
    sql = conn.execute("SELECT sql FROM sqlite_master WHERE type='table' AND name='users'").fetchone()["sql"]
    if "'govt'" in sql:
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
            role TEXT NOT NULL CHECK(role IN ('owner','vet','govt')),
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
            )
        """
    )


def seed(conn):
    from datetime import timedelta

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
