import os
import json
import math
import re
import uuid
import jwt
import sqlite3
import requests
import io
import base64
from datetime import datetime, timedelta, date
from functools import wraps
from flask import Flask, request, jsonify, g, send_from_directory, Response, stream_with_context
from PIL import Image
import cv2
import numpy as np
import qrcode
from sklearn.cluster import DBSCAN

from database import (
    get_db, init_db, hash_password, verify_password, next_code,
    audit_log, calculate_expected_delivery
)
import weather
import animal_ai
import secrets as _secrets_for_ivr
from notifications import SMSProvider, provider_health

# IVR integration - production-grade IVR reporting channel
try:
    from ivr.routes import register_ivr_routes
    from ivr.config import IVR_PHONE_NUMBER, TELEPHONY_PROVIDER
    HAS_IVR = True
except Exception as e:
    print(f"IVR module not loaded: {e}")
    HAS_IVR = False

SECRET_KEY = os.environ.get("SIH_SECRET_KEY", "sih-hackathon-dev-secret-change-me")
TOKEN_EXP_HOURS = 12

FRONTEND_DIR = os.path.join(os.path.dirname(__file__), "..", "frontend")
PORT = int(os.environ.get("PORT", "5001"))

app = Flask(__name__, static_folder=FRONTEND_DIR, static_url_path="")

# Production hardening: initialise the database at import time so WSGI servers
# (gunicorn) that never execute the `__main__` block still get schema + seeds.
# init_db() is idempotent (CREATE TABLE IF NOT EXISTS); retry once to survive
# multi-worker cold-start races on a fresh persistent disk.
try:
    init_db()
    import database as _dbmod
    print(f"Database ready at {_dbmod.DB_PATH}")
except Exception as _init_err:
    import time as _time
    print(f"init_db first attempt failed ({_init_err}); retrying once...")
    _time.sleep(2)
    init_db()
    print("Database ready after retry.")

CASE_STATUSES = [
    "NEW", "ASSIGNED", "UNDER INVESTIGATION", "SAMPLE COLLECTED", "LAB PENDING",
    "DIAGNOSED", "TREATMENT", "FOLLOW-UP", "RECOVERED", "CLOSED"
]

SAMPLE_STATUSES = [
    "COLLECTED", "READY_FOR_PICKUP", "PICKED_UP", "IN_TRANSIT",
    "ARRIVED_AT_LAB", "LAB_RECEIVED", "TESTING", "RESULT_READY",
    "COMPLETED", "REJECTED"
]

ALLERGY_MED_CLASSES = {
    "penicillin": ["penicillin", "amoxicillin", "ampicillin", "pen-strep", "cloxacillin", "amoxiclav", "beta-lactam"],
    "sulfa": ["sulphonamide", "sulfamethoxazole", "trimethoprim", "co-trimoxazole", "cotrimoxazole", "sulfadiazine"],
    "tetracycline": ["tetracycline", "oxytetracycline", "chlortetracycline", "doxycycline"],
    "nsaid": ["meloxicam", "flunixin", "ketoprofen", "phenylbutazone", "carprofen", "diclofenac", "aspirin", "paracetamol"],
    "aminoglycoside": ["gentamicin", "streptomycin", "neomycin", "amikacin"],
    "fluoroquinolone": ["enrofloxacin", "ciprofloxacin", "marbofloxacin", "levofloxacin"],
}


# ---------------------------------------------------------------- helpers --
def make_token(user):
    payload = {
        "uid": user["id"],
        "role": user["role"],
        "name": user["full_name"],
        "exp": datetime.utcnow() + timedelta(hours=TOKEN_EXP_HOURS),
    }
    return jwt.encode(payload, SECRET_KEY, algorithm="HS256")


def decode_token(token):
    try:
        return jwt.decode(token, SECRET_KEY, algorithms=["HS256"])
    except Exception:
        return None


def auth_required(roles=None):
    def deco(fn):
        @wraps(fn)
        def wrapper(*args, **kwargs):
            auth = request.headers.get("Authorization", "")
            token = auth.split(" ", 1)[1] if auth.startswith("Bearer ") else ""
            if not token:
                # Fallback for intermediaries that strip the Authorization
                # header (observed: login 200 then dashboard 401 "Missing or
                # invalid Authorization header" via proxied preview). The
                # frontend only sends ?access_token= after a header-based 401,
                # and the header form is always preferred when present.
                token = request.args.get("access_token", "")
            if not token:
                return jsonify({"error": "Missing or invalid Authorization header"}), 401
            payload = decode_token(token)
            if not payload:
                return jsonify({"error": "Invalid or expired token"}), 401
            if roles and payload["role"] not in roles:
                return jsonify({"error": "Forbidden for this role"}), 403
            g.user = payload
            return fn(*args, **kwargs)
        return wrapper
    return deco


def row_to_dict(row):
    return dict(row) if row else None


EVENT_SCHEMA_VERSION = 1


def emit_event(conn, event_type: str, payload: dict | None = None, *, case_id=None,
               actor_id=None, actor_role=None, idempotency_key=None):
    """Persist one validated, replayable realtime event.

    SQLite is the source of truth for the SSE event stream. INSERT OR IGNORE
    makes webhook retries and offline retries idempotent; consumers can safely
    reconnect using Last-Event-ID.
    """
    allowed = re.compile(r"^[A-Z][A-Z0-9_]{2,80}$")
    event_type = str(event_type or "").upper()
    if not allowed.match(event_type):
        raise ValueError("invalid event_type")
    event_id = str(uuid.uuid4())
    payload = payload or {}
    conn.execute(
        "INSERT OR IGNORE INTO realtime_events (event_id,event_type,schema_version,case_id,actor_id,actor_role,payload,idempotency_key) VALUES (?,?,?,?,?,?,?,?)",
        (event_id, event_type, EVENT_SCHEMA_VERSION, case_id, actor_id, actor_role,
         json.dumps(payload, ensure_ascii=False), idempotency_key),
    )
    if idempotency_key:
        row = conn.execute("SELECT event_id FROM realtime_events WHERE idempotency_key=?", (idempotency_key,)).fetchone()
        return row["event_id"] if row else event_id
    return event_id


def notify(conn, user_id, message, type_="info", *, case_id=None,
           event_type="NOTIFICATION_CREATED", data=None, idempotency_key=None):
    """Create an in-app notification and its realtime event.

    Existing callers keep the original four-argument contract. External
    channels are opt-in through the unified notification endpoint, so ordinary
    case/lab writes never claim that SMS was delivered.
    """
    if not user_id:
        return None
    recipient = conn.execute("SELECT role FROM users WHERE id=?", (user_id,)).fetchone()
    role = recipient["role"] if recipient else None
    idem = idempotency_key or f"notif:{event_type}:{user_id}:{case_id or ''}:{message}"
    event_id = emit_event(conn, event_type, {"message": message, "type": type_, **(data or {}), "recipient_id": user_id},
                          case_id=case_id, actor_id=None, actor_role="system",
                          idempotency_key=idem)
    existing = conn.execute("SELECT id FROM notifications WHERE idempotency_key=?", (idem,)).fetchone()
    if existing:
        return existing["id"]
    cur = conn.execute(
        "INSERT INTO notifications (user_id,message,type,event_id,idempotency_key,recipient_role,channel,delivery_status,case_id,data_json) VALUES (?,?,?,?,?,?,?,?,?,?)",
        (user_id, message, type_, event_id, idem, role, "in_app", "DELIVERED", case_id,
         json.dumps(data or {}, ensure_ascii=False)),
    )
    return cur.lastrowid


def case_json(conn, row):
    d = dict(row)
    animal = conn.execute("SELECT * FROM animals WHERE id=?", (d["animal_id"],)).fetchone()
    herd = conn.execute("SELECT * FROM herds WHERE id=?", (d["herd_id"],)).fetchone() if d["herd_id"] else None
    owner = conn.execute("SELECT full_name, mobile FROM users WHERE id=?", (d["owner_id"],)).fetchone()
    vet = conn.execute("SELECT full_name FROM users WHERE id=?", (d["vet_id"],)).fetchone() if d["vet_id"] else None
    d["animal"] = row_to_dict(animal)
    d["herd"] = row_to_dict(herd)
    d["owner"] = row_to_dict(owner)
    d["vet_name"] = vet["full_name"] if vet else None
    return d


def public_user(row):
    d = dict(row)
    d.pop("password_hash", None)
    d.pop("salt", None)
    return d


def make_qr_image_data_url(payload: str) -> str:
    """Generate high-contrast PNG QR Code as a base64 data-URL."""
    qr = qrcode.QRCode(version=1, box_size=8, border=2)
    qr.add_data(payload)
    qr.make(fit=True)
    img = qr.make_image(fill_color="black", back_color="white")
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    b64 = base64.b64encode(buf.getvalue()).decode("utf-8")
    return f"data:image/png;base64,{b64}"


def decode_qr_image(img_bytes_or_base64: str) -> str:
    """Decode QR code image using OpenCV QRCodeDetector."""
    try:
        raw_b64 = img_bytes_or_base64
        if "," in raw_b64:
            raw_b64 = raw_b64.split(",", 1)[1]
        img_bytes = base64.b64decode(raw_b64)
        pil_img = Image.open(io.BytesIO(img_bytes)).convert("RGB")
        np_img = np.array(pil_img)
        detector = cv2.QRCodeDetector()
        val, pts, _ = detector.detectAndDecode(np_img)
        return (val or "").strip()
    except Exception:
        return ""


def check_allergy_conflict(medicine_name: str, active_allergies: list) -> dict | None:
    """Check if the prescribed medication matches any documented animal allergy."""
    if not medicine_name or not active_allergies:
        return None
    med_clean = medicine_name.strip().lower()
    for allergy in active_allergies:
        allergen = (allergy.get("allergen") or "").strip().lower()
        if not allergen:
            continue
        # Direct match or substring match
        if allergen in med_clean or med_clean in allergen:
            return allergy
        # Class match
        for cls_name, drugs in ALLERGY_MED_CLASSES.items():
            allergen_in_class = cls_name in allergen or any(d in allergen for d in drugs)
            med_in_class = cls_name in med_clean or any(d in med_clean for d in drugs)
            if allergen_in_class and med_in_class:
                return allergy
    return None


# -------------------------------------------------------------- frontend --
@app.after_request
def no_cache_static(resp):
    if request.path in ("/", "/index.html") or request.path.endswith((".js", ".css")):
        resp.headers["Cache-Control"] = "no-cache, no-store, must-revalidate, max-age=0"
        resp.headers["Pragma"] = "no-cache"
        resp.headers["Expires"] = "0"
    # Optional CORS for split deployments (frontend hosted separately).
    # Same-origin (default: Flask serves frontend) needs nothing. Set
    # CORS_ORIGINS="https://app.example.com,https://other.example.com" to enable.
    _cors = os.environ.get("CORS_ORIGINS", "").strip()
    if _cors:
        _allowed = [o.strip() for o in _cors.split(",") if o.strip()]
        _origin = request.headers.get("Origin", "")
        if _origin in _allowed:
            resp.headers["Access-Control-Allow-Origin"] = _origin
            resp.headers["Vary"] = "Origin"
            resp.headers["Access-Control-Allow-Credentials"] = "true"
            resp.headers["Access-Control-Allow-Headers"] = "Authorization, Content-Type"
            resp.headers["Access-Control-Allow-Methods"] = "GET, POST, PUT, DELETE, OPTIONS"
    return resp


@app.route("/")
def index():
    return send_from_directory(FRONTEND_DIR, "index.html")


# ------------------------------------------------------------------ auth --
@app.post("/api/auth/register")
def register():
    data = request.get_json(force=True) or {}
    required = ["full_name", "mobile", "email", "password", "confirm_password", "role"]
    missing = [f for f in required if not data.get(f)]
    if missing:
        return jsonify({"error": f"Missing fields: {', '.join(missing)}"}), 400
    if data["password"] != data["confirm_password"]:
        return jsonify({"error": "Passwords do not match"}), 400
    if len(data["password"]) < 6:
        return jsonify({"error": "Password must be at least 6 characters"}), 400
    if data["role"] not in ("owner", "vet", "govt", "lab"):
        return jsonify({"error": "Invalid role"}), 400
    preferred_language = (data.get("preferred_language") or "").strip().lower() or None
    if preferred_language and preferred_language not in ("en", "te", "hi", "mr"):
        return jsonify({"error": "Invalid preferred_language (use en, te, hi, mr)"}), 400
    # Normalise identity fields so later logins match what the user typed here.
    email_norm = str(data["email"]).strip().lower()
    mobile_norm = re.sub(r"[\s\-]", "", str(data["mobile"]).strip())
    name_norm = str(data["full_name"]).strip()

    conn = get_db()
    try:
        existing = conn.execute(
            "SELECT id FROM users WHERE LOWER(email)=LOWER(?) OR REPLACE(REPLACE(mobile,' ',''),'-','')=?",
            (email_norm, mobile_norm),
        ).fetchone()
        if existing:
            return jsonify({"error": "An account with this email or mobile already exists"}), 409

        pw_hash, salt = hash_password(data["password"])
        cur = conn.execute(
            "INSERT INTO users (full_name, mobile, email, password_hash, salt, role, specialization, village, block, district, state, preferred_language) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            (name_norm, mobile_norm, email_norm, pw_hash, salt, data["role"],
             (data.get("specialization") or None) and str(data.get("specialization")).strip(),
             (data.get("village") or None) and str(data.get("village")).strip(),
             (data.get("block") or None) and str(data.get("block")).strip(),
             str(data.get("district") or "").strip(),
             str(data.get("state") or "Maharashtra").strip(), preferred_language),
        )
        user_id = cur.lastrowid
        audit_log(conn, "REGISTER_USER", "user", user_id, actor_id=user_id,
                  actor_name=data["full_name"], actor_role=data["role"],
                  details={"email": data["email"], "role": data["role"]})
        conn.commit()
        user = conn.execute("SELECT * FROM users WHERE id=?", (user_id,)).fetchone()
        token = make_token(user)
        return jsonify({"token": token, "user": public_user(user)}), 201
    except sqlite3.IntegrityError:
        return jsonify({"error": "An account with this email or mobile already exists"}), 409
    finally:
        conn.close()


@app.post("/api/auth/login")
def login():
    data = request.get_json(force=True) or {}
    # Tolerant identifier: phone keyboards add trailing spaces / autocapitalise.
    # Email matches case-insensitively; mobile matches ignoring spaces/dashes.
    identifier = str(data.get("identifier") or data.get("email") or data.get("mobile") or "").strip()
    password = data.get("password")
    if not identifier or not password:
        return jsonify({"error": "Email/mobile and password are required"}), 400
    mobile_norm = re.sub(r"[\s\-]", "", identifier)

    conn = get_db()
    user = conn.execute(
        "SELECT * FROM users WHERE LOWER(email)=LOWER(?) OR REPLACE(REPLACE(mobile,' ',''),'-','')=?",
        (identifier, mobile_norm),
    ).fetchone()
    if not user or not verify_password(password, user["salt"], user["password_hash"]):
        conn.close()
        return jsonify({"error": "Invalid credentials"}), 401

    token = make_token(user)
    audit_log(conn, "LOGIN", "user", user["id"], actor_id=user["id"],
              actor_name=user["full_name"], actor_role=user["role"],
              details={"identifier": identifier})
    conn.commit()
    conn.close()
    return jsonify({"token": token, "user": public_user(user)})


@app.get("/api/users/me")
@auth_required()
def me():
    conn = get_db()
    user = conn.execute("SELECT * FROM users WHERE id=?", (g.user["uid"],)).fetchone()
    conn.close()
    return jsonify(public_user(user))


@app.put("/api/users/me")
@auth_required()
def update_me():
    """Self-service profile update (strict whitelist): preferred language for
    all roles; helpline availability override for vets only."""
    data = request.get_json(force=True) or {}
    updates, params = [], []
    if "preferred_language" in data:
        lang = (data.get("preferred_language") or "").strip().lower() or None
        if lang and lang not in ("en", "te", "hi", "mr"):
            return jsonify({"error": "Invalid preferred_language (use en, te, hi, mr)"}), 400
        updates.append("preferred_language=?")
        params.append(lang)
    if "availability_status" in data:
        if g.user["role"] != "vet":
            return jsonify({"error": "Only veterinarians can set availability_status"}), 403
        status = (data.get("availability_status") or "").strip().upper() or None
        if status and status not in ("AVAILABLE", "BUSY", "OFFLINE"):
            return jsonify({"error": "Invalid availability_status (use AVAILABLE, BUSY, OFFLINE)"}), 400
        updates.append("availability_status=?")
        params.append(status)
    if not updates:
        return jsonify({"error": "Nothing to update (allowed: preferred_language, availability_status)"}), 400
    conn = get_db()
    params.append(g.user["uid"])
    conn.execute(f"UPDATE users SET {', '.join(updates)} WHERE id=?", params)
    audit_log(conn, "UPDATE_PROFILE", "user", g.user["uid"], actor_id=g.user["uid"],
              actor_name=g.user["name"], actor_role=g.user["role"],
              details={k: v for k, v in data.items() if k in ("preferred_language", "availability_status")})
    conn.commit()
    user = conn.execute("SELECT * FROM users WHERE id=?", (g.user["uid"],)).fetchone()
    conn.close()
    return jsonify(public_user(user))


# --------------------------------------------------------------- herds ---
@app.post("/api/herds")
@auth_required(roles=["owner"])
def create_herd():
    data = request.get_json(force=True) or {}
    conn = get_db()
    owner = conn.execute("SELECT * FROM users WHERE id=?", (g.user["uid"],)).fetchone()
    district = data.get("district") or owner["district"] or "PUN"
    code = next_code(conn, "HERD", "herds", "herd_code", district=district[:3].upper())
    cur = conn.execute(
        "INSERT INTO herds (herd_code, owner_id, village, block, district, state) VALUES (?,?,?,?,?,?)",
        (code, g.user["uid"], data.get("village", owner["village"]), data.get("block", owner["block"]),
         district, data.get("state", "Maharashtra")),
    )
    herd_id = cur.lastrowid
    audit_log(conn, "CREATE_HERD", "herd", herd_id, actor_id=g.user["uid"],
              actor_name=g.user["name"], actor_role=g.user["role"], details={"herd_code": code})
    conn.commit()
    herd = conn.execute("SELECT * FROM herds WHERE id=?", (herd_id,)).fetchone()
    conn.close()
    return jsonify(row_to_dict(herd)), 201


@app.get("/api/herds")
@auth_required()
def list_herds():
    conn = get_db()
    if g.user["role"] == "owner":
        herds = conn.execute("SELECT * FROM herds WHERE owner_id=? ORDER BY id DESC", (g.user["uid"],)).fetchall()
    else:
        herds = conn.execute("SELECT * FROM herds ORDER BY id DESC").fetchall()
    out = []
    for h in herds:
        hd = dict(h)
        hd["animal_count"] = conn.execute("SELECT COUNT(*) c FROM animals WHERE herd_id=?", (h["id"],)).fetchone()["c"]
        hd["active_cases"] = conn.execute(
            "SELECT COUNT(*) c FROM cases WHERE herd_id=? AND status NOT IN ('CLOSED','RECOVERED')",
            (h["id"],)
        ).fetchone()["c"]
        out.append(hd)
    conn.close()
    return jsonify(out)


# ------------------------------------------------------------- animals ---
@app.post("/api/animals")
@auth_required(roles=["owner"])
def create_animal():
    data = request.get_json(force=True) or {}
    animal_type = data.get("animal_type") or data.get("species")
    if not animal_type:
        return jsonify({"error": "Animal type is required"}), 400
    conn = get_db()
    try:
        owner = conn.execute("SELECT * FROM users WHERE id=?", (g.user["uid"],)).fetchone()
        district = data.get("district") or owner["district"] or "PUN"
        code = next_code(conn, "MH", "animals", "animal_code", district=district[:3].upper())
        herd_id = data.get("herd_id") or None
        if herd_id:
            herd = conn.execute("SELECT * FROM herds WHERE id=? AND owner_id=?", (herd_id, g.user["uid"])).fetchone()
            if not herd:
                return jsonify({"error": "Invalid herd ID"}), 400
        age = data.get("age") or data.get("age_years")
        owner_name = data.get("owner_name") or owner["full_name"]
        mobile = data.get("mobile") or owner["mobile"]
        cur = conn.execute(
            "INSERT INTO animals (animal_code, owner_id, herd_id, animal_name, animal_type, species, breed, gender, sex, age, age_years, owner_name, mobile, village, block, district, state, status) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (code, g.user["uid"], herd_id, data.get("animal_name"), animal_type, animal_type, data.get("breed"),
             data.get("gender"), data.get("gender"), age, age, owner_name, mobile,
             data.get("village", owner["village"]), data.get("block", owner["block"]), district,
             data.get("state", "Maharashtra"), data.get("status", "Healthy")),
        )
        animal_id = cur.lastrowid

        # Generate secure QR code identity
        token = f"aqr_{uuid.uuid4().hex}"
        payload = f"PASHU:ANIMAL:{token}"
        conn.execute(
            "INSERT INTO animal_qr_codes (animal_id, qr_token, qr_payload, status) VALUES (?,?,?,'ACTIVE')",
            (animal_id, token, payload)
        )
        audit_log(conn, "CREATE_ANIMAL", "animal", animal_id, actor_id=g.user["uid"],
                  actor_name=g.user["name"], actor_role=g.user["role"],
                  details={"animal_code": code, "qr_token": token})

        conn.commit()
        animal = conn.execute("SELECT * FROM animals WHERE id=?", (animal_id,)).fetchone()
        res = row_to_dict(animal)
        res["qr_token"] = token
        res["qr_payload"] = payload
        res["qr_image"] = make_qr_image_data_url(payload)
        return jsonify(res), 201
    except sqlite3.IntegrityError:
        conn.rollback()
        return jsonify({"error": "Could not save this animal. Please check the details and try again."}), 400
    finally:
        conn.close()


@app.delete("/api/animals/<int:animal_id>")
@auth_required(roles=["owner"])
def delete_animal(animal_id):
    conn = get_db()
    try:
        animal = conn.execute(
            "SELECT * FROM animals WHERE id=? AND owner_id=?", (animal_id, g.user["uid"])
        ).fetchone()
        if not animal:
            return jsonify({"error": "Animal not found"}), 404

        conn.execute("DELETE FROM animal_qr_codes WHERE animal_id=?", (animal_id,))
        conn.execute("DELETE FROM animal_reproductive_records WHERE animal_id=?", (animal_id,))
        conn.execute("DELETE FROM animal_allergies WHERE animal_id=?", (animal_id,))
        conn.execute("DELETE FROM animal_medications WHERE animal_id=?", (animal_id,))
        conn.execute("DELETE FROM ai_animal_assessments WHERE animal_id=?", (animal_id,))
        conn.execute("DELETE FROM treatment_responses WHERE animal_id=?", (animal_id,))
        conn.execute("DELETE FROM sample_custody_events WHERE sample_id IN (SELECT id FROM samples WHERE animal_id=?)", (animal_id,))
        conn.execute("DELETE FROM samples WHERE animal_id=?", (animal_id,))
        conn.execute("DELETE FROM case_updates WHERE case_id IN (SELECT id FROM cases WHERE animal_id=?)", (animal_id,))
        conn.execute("DELETE FROM lab_reports WHERE animal_id=?", (animal_id,))
        conn.execute("DELETE FROM lab_requests WHERE animal_id=?", (animal_id,))
        conn.execute("DELETE FROM prescriptions WHERE animal_id=?", (animal_id,))
        conn.execute("DELETE FROM vaccinations WHERE animal_id=?", (animal_id,))
        conn.execute("DELETE FROM cases WHERE animal_id=?", (animal_id,))
        conn.execute("DELETE FROM animals WHERE id=?", (animal_id,))

        audit_log(conn, "DELETE_ANIMAL", "animal", animal_id, actor_id=g.user["uid"],
                  actor_name=g.user["name"], actor_role=g.user["role"],
                  details={"animal_code": animal["animal_code"]})
        conn.commit()
        return jsonify({"ok": True, "deleted": animal["animal_code"]})
    finally:
        conn.close()


@app.get("/api/animals")
@auth_required()
def list_animals():
    conn = get_db()
    if g.user["role"] == "owner":
        animals = conn.execute("SELECT * FROM animals WHERE owner_id=? ORDER BY id DESC", (g.user["uid"],)).fetchall()
    else:
        animals = conn.execute("SELECT * FROM animals ORDER BY id DESC").fetchall()
    conn.close()
    return jsonify([row_to_dict(a) for a in animals])


@app.get("/api/animals/<int:animal_id>")
@auth_required()
def get_animal(animal_id):
    conn = get_db()
    animal = conn.execute("SELECT * FROM animals WHERE id=?", (animal_id,)).fetchone()
    if not animal:
        conn.close()
        return jsonify({"error": "Animal not found"}), 404
    if g.user["role"] == "owner" and animal["owner_id"] != g.user["uid"]:
        conn.close()
        return jsonify({"error": "Not authorized to view this animal"}), 403

    cases = conn.execute("SELECT * FROM cases WHERE animal_id=? ORDER BY id DESC", (animal_id,)).fetchall()
    vaccinations = conn.execute(
        "SELECT v.*, u.full_name vet_name FROM vaccinations v LEFT JOIN users u ON u.id=v.vet_id "
        "WHERE animal_id=? ORDER BY date_given DESC", (animal_id,)
    ).fetchall()
    lab_reports = conn.execute("SELECT * FROM lab_reports WHERE animal_id=? ORDER BY id DESC", (animal_id,)).fetchall()
    prescriptions = conn.execute(
        "SELECT p.*, u.full_name vet_name FROM prescriptions p LEFT JOIN users u ON u.id=p.vet_id "
        "WHERE animal_id=? ORDER BY id DESC", (animal_id,)
    ).fetchall()
    herd = conn.execute("SELECT * FROM herds WHERE id=?", (animal["herd_id"],)).fetchone() if animal["herd_id"] else None

    # Extended entities
    qr_row = conn.execute("SELECT * FROM animal_qr_codes WHERE animal_id=? AND status='ACTIVE' ORDER BY id DESC LIMIT 1", (animal_id,)).fetchone()
    qr_data = row_to_dict(qr_row)
    if qr_data:
        qr_data["qr_image"] = make_qr_image_data_url(qr_data["qr_payload"])

    reproductive_records = conn.execute(
        "SELECT r.*, u.full_name recorded_by_name FROM animal_reproductive_records r LEFT JOIN users u ON u.id=r.recorded_by "
        "WHERE animal_id=? ORDER BY id DESC", (animal_id,)
    ).fetchall()
    allergies = conn.execute(
        "SELECT a.*, u.full_name recorded_by_name FROM animal_allergies a LEFT JOIN users u ON u.id=a.recorded_by "
        "WHERE animal_id=? ORDER BY id DESC", (animal_id,)
    ).fetchall()
    medications = conn.execute(
        "SELECT m.*, u.full_name prescribed_by_name FROM animal_medications m LEFT JOIN users u ON u.id=m.prescribed_by "
        "WHERE animal_id=? ORDER BY id DESC", (animal_id,)
    ).fetchall()

    result = row_to_dict(animal)
    result["herd"] = row_to_dict(herd)
    result["cases"] = [row_to_dict(c) for c in cases]
    result["vaccinations"] = [row_to_dict(v) for v in vaccinations]
    result["lab_reports"] = [row_to_dict(l) for l in lab_reports]
    result["prescriptions"] = [row_to_dict(p) for p in prescriptions]
    result["qr"] = qr_data
    result["reproductive_records"] = [row_to_dict(r) for r in reproductive_records]
    result["allergies"] = [row_to_dict(a) for a in allergies]
    result["medications"] = [row_to_dict(m) for m in medications]

    # Precompute animal AI decision support
    try:
        cds = animal_ai.evaluate_animal_cds(
            dict(animal), [dict(c) for c in cases], [dict(v) for v in vaccinations],
            [dict(l) for l in lab_reports], [dict(r) for r in reproductive_records],
            [dict(al) for al in allergies], [dict(m) for m in medications]
        )
        result["ai_decision_support"] = cds
    except Exception:
        result["ai_decision_support"] = None

    conn.close()
    return jsonify(result)


# ----------------------------------------------- QR animal identity endpoints --
@app.get("/api/animals/<int:animal_id>/qr")
@auth_required()
def get_animal_qr(animal_id):
    conn = get_db()
    animal = conn.execute("SELECT * FROM animals WHERE id=?", (animal_id,)).fetchone()
    if not animal:
        conn.close()
        return jsonify({"error": "Animal not found"}), 404
    qr = conn.execute("SELECT * FROM animal_qr_codes WHERE animal_id=? AND status='ACTIVE' ORDER BY id DESC LIMIT 1", (animal_id,)).fetchone()
    if not qr:
        token = f"aqr_{uuid.uuid4().hex}"
        payload = f"PASHU:ANIMAL:{token}"
        conn.execute("INSERT INTO animal_qr_codes (animal_id, qr_token, qr_payload, status) VALUES (?,?,?,'ACTIVE')", (animal_id, token, payload))
        conn.commit()
        qr = conn.execute("SELECT * FROM animal_qr_codes WHERE animal_id=? AND status='ACTIVE'", (animal_id,)).fetchone()
    conn.close()
    res = row_to_dict(qr)
    res["qr_image"] = make_qr_image_data_url(res["qr_payload"])
    res["animal_code"] = animal["animal_code"]
    res["species"] = animal["species"]
    return jsonify(res)


@app.post("/api/animals/<int:animal_id>/qr")
@auth_required(roles=["owner", "vet", "govt"])
def regenerate_animal_qr(animal_id):
    conn = get_db()
    try:
        animal = conn.execute("SELECT * FROM animals WHERE id=?", (animal_id,)).fetchone()
        if not animal:
            return jsonify({"error": "Animal not found"}), 404

        new_token = f"aqr_{uuid.uuid4().hex}"
        new_payload = f"PASHU:ANIMAL:{new_token}"
        existing = conn.execute("SELECT id FROM animal_qr_codes WHERE animal_id=?", (animal_id,)).fetchone()
        if existing:
            conn.execute(
                "UPDATE animal_qr_codes SET qr_token=?, qr_payload=?, status='ACTIVE', created_at=datetime('now') WHERE animal_id=?",
                (new_token, new_payload, animal_id)
            )
            qr_id = existing["id"]
        else:
            cur = conn.execute(
                "INSERT INTO animal_qr_codes (animal_id, qr_token, qr_payload, status) VALUES (?,?,?,'ACTIVE')",
                (animal_id, new_token, new_payload)
            )
            qr_id = cur.lastrowid

        audit_log(conn, "REGENERATE_QR", "animal", animal_id, actor_id=g.user["uid"],
                  actor_name=g.user["name"], actor_role=g.user["role"],
                  details={"animal_code": animal["animal_code"], "new_qr_token": new_token})
        conn.commit()
        qr = conn.execute("SELECT * FROM animal_qr_codes WHERE id=?", (qr_id,)).fetchone()
        res = row_to_dict(qr)
        res["qr_image"] = make_qr_image_data_url(res["qr_payload"])
        return jsonify(res), 201
    finally:
        conn.close()


@app.post("/api/animals/<int:animal_id>/qr/revoke")
@auth_required(roles=["owner", "vet", "govt"])
def revoke_animal_qr(animal_id):
    conn = get_db()
    conn.execute("UPDATE animal_qr_codes SET status='REVOKED', revoked_at=datetime('now'), revoked_by=? WHERE animal_id=?", (g.user["uid"], animal_id))
    audit_log(conn, "REVOKE_QR", "animal", animal_id, actor_id=g.user["uid"],
              actor_name=g.user["name"], actor_role=g.user["role"])
    conn.commit()
    conn.close()
    return jsonify({"ok": True, "message": "Animal QR identity revoked successfully."})


@app.get("/api/animals/lookup-qr")
@auth_required()
def lookup_animal_qr():
    raw_query = (request.args.get("token") or request.args.get("code") or request.args.get("payload") or "").strip()
    if not raw_query:
        return jsonify({"error": "Missing token or code parameter"}), 400

    token = raw_query
    if token.startswith("PASHU:ANIMAL:"):
        token = token[len("PASHU:ANIMAL:"):]

    conn = get_db()
    # Search by token or animal_code (manual fallback)
    qr = conn.execute(
        "SELECT a.*, q.qr_token, q.status as qr_status FROM animal_qr_codes q JOIN animals a ON a.id=q.animal_id WHERE q.qr_token=? AND q.status='ACTIVE'",
        (token,)
    ).fetchone()

    if not qr:
        # Manual fallback by animal_code
        qr = conn.execute("SELECT * FROM animals WHERE UPPER(animal_code)=UPPER(?)", (token,)).fetchone()

    if not qr:
        conn.close()
        return jsonify({"error": f"No active animal record matched query: '{raw_query}'"}), 404

    animal_id = qr["id"]
    conn.close()
    # Delegate to standard get_animal for full rich record
    return get_animal(animal_id)


@app.post("/api/qr/decode")
@auth_required()
def decode_qr():
    """Decode a QR image provided as Base64 payload using OpenCV."""
    data = request.get_json(force=True) or {}
    img_b64 = data.get("image") or ""
    if not img_b64:
        return jsonify({"error": "Missing image data"}), 400
    val = decode_qr_image(img_b64)
    if not val:
        return jsonify({"decoded": False, "error": "No clear QR code detected in image"}), 422

    qr_type = "unknown"
    if val.startswith("PASHU:ANIMAL:"):
        qr_type = "animal"
    elif val.startswith("PASHU:SAMPLE:"):
        qr_type = "sample"

    return jsonify({"decoded": True, "payload": val, "type": qr_type})


# --------------------------------- pregnancy & reproductive health endpoints --
@app.get("/api/animals/<int:animal_id>/reproductive")
@auth_required()
def get_reproductive_records(animal_id):
    conn = get_db()
    records = conn.execute(
        "SELECT r.*, u.full_name recorded_by_name FROM animal_reproductive_records r LEFT JOIN users u ON u.id=r.recorded_by "
        "WHERE animal_id=? ORDER BY id DESC", (animal_id,)
    ).fetchall()
    conn.close()
    return jsonify([row_to_dict(r) for r in records])


@app.post("/api/animals/<int:animal_id>/reproductive")
@auth_required(roles=["vet", "owner"])
def add_reproductive_record(animal_id):
    data = request.get_json(force=True) or {}
    conn = get_db()
    animal = conn.execute("SELECT * FROM animals WHERE id=?", (animal_id,)).fetchone()
    if not animal:
        conn.close()
        return jsonify({"error": "Animal not found"}), 404

    breeding_date = data.get("breeding_date") or data.get("mating_service_date")
    expected_delivery = data.get("expected_delivery_date")
    if breeding_date and not expected_delivery:
        expected_delivery = calculate_expected_delivery(animal["species"], breeding_date)

    status = data.get("pregnancy_status", "Suspected")
    cur = conn.execute(
        """
        INSERT INTO animal_reproductive_records
        (animal_id, pregnancy_status, breeding_date, mating_service_date, expected_delivery_date,
         pregnancy_confirmation_date, previous_pregnancies, offspring_count, event_type,
         miscarriage_abortion_notes, breeding_notes, recorded_by)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?)
        """,
        (animal_id, status, breeding_date, breeding_date, expected_delivery,
         data.get("pregnancy_confirmation_date"), data.get("previous_pregnancies", 0),
         data.get("offspring_count", 0), data.get("event_type", "Pregnancy Check"),
         data.get("miscarriage_abortion_notes"), data.get("breeding_notes"), g.user["uid"])
    )
    rec_id = cur.lastrowid
    audit_log(conn, "RECORD_REPRODUCTIVE_HEALTH", "animal", animal_id, actor_id=g.user["uid"],
              actor_name=g.user["name"], actor_role=g.user["role"],
              details={"status": status, "breeding_date": breeding_date, "expected_delivery": expected_delivery})
    conn.commit()
    rec = conn.execute("SELECT * FROM animal_reproductive_records WHERE id=?", (rec_id,)).fetchone()
    conn.close()
    return jsonify(row_to_dict(rec)), 201


# --------------------------------------- medication & allergy profile endpoints --
@app.get("/api/animals/<int:animal_id>/allergies")
@auth_required()
def get_animal_allergies(animal_id):
    conn = get_db()
    rows = conn.execute(
        "SELECT a.*, u.full_name recorded_by_name FROM animal_allergies a LEFT JOIN users u ON u.id=a.recorded_by "
        "WHERE animal_id=? ORDER BY id DESC", (animal_id,)
    ).fetchall()
    conn.close()
    return jsonify([row_to_dict(r) for r in rows])


@app.post("/api/animals/<int:animal_id>/allergies")
@auth_required(roles=["vet"])
def add_animal_allergy(animal_id):
    data = request.get_json(force=True) or {}
    allergen = (data.get("allergen") or "").strip()
    reaction = (data.get("reaction") or "").strip()
    if not allergen or not reaction:
        return jsonify({"error": "Allergen and reaction description are required"}), 400

    conn = get_db()
    cur = conn.execute(
        "INSERT INTO animal_allergies (animal_id, allergen, allergy_severity, reaction, date_recorded, recorded_by, status, notes) "
        "VALUES (?,?,?,?,?,?,'Active',?)",
        (animal_id, allergen, data.get("allergy_severity", "Moderate"), reaction,
         data.get("date_recorded", str(date.today())), g.user["uid"], data.get("notes"))
    )
    allergy_id = cur.lastrowid
    audit_log(conn, "RECORD_ALLERGY", "animal", animal_id, actor_id=g.user["uid"],
              actor_name=g.user["name"], actor_role=g.user["role"],
              details={"allergen": allergen, "severity": data.get("allergy_severity")})
    conn.commit()
    al = conn.execute("SELECT * FROM animal_allergies WHERE id=?", (allergy_id,)).fetchone()
    conn.close()
    return jsonify(row_to_dict(al)), 201


@app.get("/api/animals/<int:animal_id>/medications")
@auth_required()
def get_animal_medications(animal_id):
    conn = get_db()
    rows = conn.execute(
        "SELECT m.*, u.full_name prescribed_by_name FROM animal_medications m LEFT JOIN users u ON u.id=m.prescribed_by "
        "WHERE animal_id=? ORDER BY id DESC", (animal_id,)
    ).fetchall()
    conn.close()
    return jsonify([row_to_dict(r) for r in rows])


# --------------------------------------------------------------- cases ---
@app.post("/api/cases")
@auth_required(roles=["owner"])
def create_case():
    data = request.get_json(force=True) or {}
    if not data.get("animal_id"):
        return jsonify({"error": "animal_id is required"}), 400
    conn = get_db()
    animal = conn.execute("SELECT * FROM animals WHERE id=? AND owner_id=?", (data["animal_id"], g.user["uid"])).fetchone()
    if not animal:
        conn.close()
        return jsonify({"error": "Animal not found"}), 404

    district = animal["district"] or "PUN"
    case_no = next_code(conn, "CASE", "cases", "case_no", district=district[:3].upper())
    vet_id = data.get("vet_id") or None
    cur = conn.execute(
        "INSERT INTO cases (case_no, animal_id, herd_id, owner_id, vet_id, symptoms, disease_suspected, severity, description, reported_through, status) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
        (case_no, animal["id"], animal["herd_id"], g.user["uid"], vet_id,
         data.get("symptoms"), data.get("disease_suspected"), data.get("severity", "Medium"),
         data.get("description"), data.get("reported_through", "Mobile App"), "NEW"),
    )
    case_id = cur.lastrowid
    conn.execute("INSERT INTO case_updates (case_id, status, note, updated_by) VALUES (?,?,?,?)",
                 (case_id, "NEW", "Case reported by owner", g.user["name"]))
    conn.execute("UPDATE animals SET status='Under Observation' WHERE id=?", (animal["id"],))
    emit_event(conn, "CASE_CREATED", {"case_id": case_id, "case_no": case_no, "severity": data.get("severity", "Medium")}, case_id=case_id, actor_id=g.user["uid"], actor_role="owner", idempotency_key=f"case-created:{case_id}")
    # Persist only a location explicitly supplied by the farmer. Village and
    # district fields remain textual and never become fabricated coordinates.
    if any(data.get(k) is not None for k in ("lat", "lng", "latitude", "longitude")) or any(data.get(k) for k in ("village", "block", "district")):
        try:
            _record_case_location(conn, case_id, data, g.user["uid"], default_source=("GPS" if data.get("lat", data.get("latitude")) is not None else "FARMER_PROVIDED"))
        except (TypeError, ValueError) as exc:
            conn.rollback(); conn.close(); return jsonify({"error": f"Invalid case location: {exc}"}), 400

    if vet_id:
        notify(conn, vet_id, f"New case assigned: {case_no} for {animal['animal_code']}", "case", case_id=case_id, event_type="VET_ASSIGNED")
    else:
        vets = conn.execute("SELECT id FROM users WHERE role='vet' AND district=?", (district,)).fetchall()
        for v in vets:
            notify(conn, v["id"], f"New report in your district: {case_no}", "case", case_id=case_id, event_type="VET_ASSIGNED")
    if str(data.get("severity", "Medium")).lower() in {"high", "critical"} or not vet_id:
        for official in conn.execute("SELECT id FROM users WHERE role='govt'").fetchall():
            notify(conn, official["id"], f"Authorised case alert {case_no}: priority {data.get('severity', 'Medium')} in {district}.", "case", case_id=case_id, event_type="NOTIFICATION_CREATED")

    audit_log(conn, "CREATE_CASE", "case", case_id, actor_id=g.user["uid"],
              actor_name=g.user["name"], actor_role=g.user["role"],
              details={"case_no": case_no, "animal_code": animal["animal_code"]})
    conn.commit()
    case = conn.execute("SELECT * FROM cases WHERE id=?", (case_id,)).fetchone()
    conn.close()
    return jsonify(case_json(get_db(), case)), 201


@app.post("/api/ivr/report")
def ivr_report():
    data = request.get_json(force=True) or {}
    caller_mobile = data.get("mobile")
    transcript = data.get("transcript") or data.get("symptoms") or ""
    if not caller_mobile:
        return jsonify({"error": "caller mobile is required"}), 400

    conn = get_db()
    owner = conn.execute("SELECT * FROM users WHERE mobile=?", (caller_mobile,)).fetchone()
    if not owner:
        h, s = hash_password(secrets.token_hex(8))
        cur = conn.execute(
            "INSERT INTO users (full_name, mobile, email, password_hash, salt, role, village, district, is_seed) "
            "VALUES (?,?,?,?,?,?,?,?,0)",
            (f"Caller {caller_mobile[-4:]}", caller_mobile, f"caller_{caller_mobile}@ivr.local", h, s, "owner",
             data.get("village", "Unknown"), data.get("district", "Pune")),
        )
        owner = conn.execute("SELECT * FROM users WHERE id=?", (cur.lastrowid,)).fetchone()

    animal = conn.execute("SELECT * FROM animals WHERE owner_id=? ORDER BY id DESC LIMIT 1", (owner["id"],)).fetchone()
    if not animal:
        code = next_code(conn, "MH", "animals", "animal_code", district=(owner["district"] or "PUN")[:3].upper())
        cur = conn.execute(
            "INSERT INTO animals (animal_code, owner_id, species, status) VALUES (?,?,?,'Under Observation')",
            (code, owner["id"], data.get("species", "Cattle")),
        )
        animal = conn.execute("SELECT * FROM animals WHERE id=?", (cur.lastrowid,)).fetchone()

    case_no = next_code(conn, "CASE", "cases", "case_no")
    cur = conn.execute(
        "INSERT INTO cases (case_no, animal_id, herd_id, owner_id, symptoms, severity, description, reported_through, status) "
        "VALUES (?,?,?,?,?,?,?,?,'NEW')",
        (case_no, animal["id"], animal["herd_id"], owner["id"], transcript, "Medium",
         f"IVR Call Transcript: {transcript}", "IVR"),
    )
    case_id = cur.lastrowid
    conn.execute("INSERT INTO case_updates (case_id, status, note, updated_by) VALUES (?,?,?,?)",
                 (case_id, "NEW", "Reported via Automated Voice/IVR", "IVR Bot"))
    conn.commit()
    case = conn.execute("SELECT * FROM cases WHERE id=?", (case_id,)).fetchone()
    conn.close()
    return jsonify({"ok": True, "case_no": case_no, "case": case_json(get_db(), case)}), 201


@app.get("/api/cases")
@auth_required()
def list_cases():
    conn = get_db()
    role = g.user["role"]
    if role == "owner":
        rows = conn.execute("SELECT * FROM cases WHERE owner_id=? ORDER BY id DESC", (g.user["uid"],)).fetchall()
    elif role == "vet":
        rows = conn.execute(
            "SELECT * FROM cases WHERE vet_id=? OR vet_id IS NULL ORDER BY id DESC", (g.user["uid"],)
        ).fetchall()
        rows = [r for r in rows if _can_access_case(conn, r, allow_govt=False)]
    elif role == "govt":
        rows = conn.execute("SELECT * FROM cases ORDER BY id DESC").fetchall()
        rows = [r for r in rows if _can_access_case(conn, r)]
    else:
        # Laboratory staff use the sample/report APIs, not case-level access.
        rows = []
    out = [case_json(conn, r) for r in rows]
    conn.close()
    return jsonify(out)


@app.get("/api/cases/<int:case_id>")
@auth_required()
def get_case(case_id):
    conn = get_db()
    case = conn.execute("SELECT * FROM cases WHERE id=?", (case_id,)).fetchone()
    if not case:
        conn.close()
        return jsonify({"error": "Case not found"}), 404
    if g.user["role"] == "owner" and case["owner_id"] != g.user["uid"]:
        conn.close()
        return jsonify({"error": "Not authorized"}), 403
    if g.user["role"] == "vet" and not _can_access_case(conn, case, allow_govt=False):
        conn.close()
        return jsonify({"error": "Not authorized for this case"}), 403
    if g.user["role"] == "lab":
        conn.close()
        return jsonify({"error": "Laboratory staff must use sample processing APIs"}), 403

    result = case_json(conn, case)
    updates = conn.execute("SELECT * FROM case_updates WHERE case_id=? ORDER BY id", (case_id,)).fetchall()
    lab_requests = conn.execute("SELECT * FROM lab_requests WHERE case_id=? ORDER BY id DESC", (case_id,)).fetchall()
    lab_reports = conn.execute("SELECT * FROM lab_reports WHERE case_id=? ORDER BY id DESC", (case_id,)).fetchall()
    prescriptions = conn.execute("SELECT * FROM prescriptions WHERE case_id=? ORDER BY id DESC", (case_id,)).fetchall()

    # Extended digital samples and treatment responses
    samples = conn.execute("SELECT * FROM samples WHERE case_id=? ORDER BY id DESC", (case_id,)).fetchall()
    samples_out = []
    for s in samples:
        sd = dict(s)
        sd["qr_image"] = make_qr_image_data_url(s["qr_payload"])
        custody = conn.execute("SELECT * FROM sample_custody_events WHERE sample_id=? ORDER BY id ASC", (s["id"],)).fetchall()
        sd["custody"] = [dict(c) for c in custody]
        samples_out.append(sd)

    treatment_responses = conn.execute("SELECT * FROM treatment_responses WHERE case_id=? ORDER BY id DESC", (case_id,)).fetchall()
    allergies = conn.execute("SELECT * FROM animal_allergies WHERE animal_id=? AND status='Active'", (case["animal_id"],)).fetchall()

    result["updates"] = [row_to_dict(u) for u in updates]
    result["lab_requests"] = [row_to_dict(l) for l in lab_requests]
    result["lab_reports"] = [row_to_dict(l) for l in lab_reports]
    result["prescriptions"] = [row_to_dict(p) for p in prescriptions]
    result["samples"] = samples_out
    result["treatment_responses"] = [row_to_dict(t) for t in treatment_responses]
    result["animal_allergies"] = [row_to_dict(a) for a in allergies]
    result["location"] = _case_location(conn, case_id)
    result["visit"] = None
    latest_visit = conn.execute("SELECT id FROM case_visits WHERE case_id=? ORDER BY id DESC LIMIT 1", (case_id,)).fetchone()
    if latest_visit and _can_access_case(conn, case):
        result["visit"] = _visit_response(conn, latest_visit["id"])

    conn.close()
    return jsonify(result)


@app.put("/api/cases/<int:case_id>")
@auth_required(roles=["vet"])
def update_case(case_id):
    data = request.get_json(force=True) or {}
    conn = get_db()
    case = conn.execute("SELECT * FROM cases WHERE id=?", (case_id,)).fetchone()
    if not case:
        conn.close()
        return jsonify({"error": "Case not found"}), 404
    if not _can_access_case(conn, case, allow_govt=False):
        conn.close()
        return jsonify({"error": "This case is outside your authorized veterinarian jurisdiction"}), 403

    new_status = data.get("status") or case["status"]
    diagnosis = data.get("diagnosis", case["diagnosis"])
    treatment = data.get("treatment", case["treatment"])
    note = data.get("note") or f"Status changed to {new_status}"

    conn.execute(
        "UPDATE cases SET status=?, diagnosis=?, treatment=?, vet_id=COALESCE(vet_id, ?), updated_at=datetime('now') WHERE id=?",
        (new_status, diagnosis, treatment, g.user["uid"], case_id),
    )
    conn.execute("INSERT INTO case_updates (case_id, status, note, updated_by) VALUES (?,?,?,?)",
                 (case_id, new_status, note, g.user["name"]))

    if new_status == "RECOVERED":
        conn.execute("UPDATE animals SET status='Healthy' WHERE id=?", (case["animal_id"],))

    notify(conn, case["owner_id"], f"Case {case['case_no']} updated: {new_status}", "case")
    audit_log(conn, "UPDATE_CASE", "case", case_id, actor_id=g.user["uid"],
              actor_name=g.user["name"], actor_role=g.user["role"],
              details={"status": new_status, "diagnosis": diagnosis})
    conn.commit()
    updated = conn.execute("SELECT * FROM cases WHERE id=?", (case_id,)).fetchone()
    conn.close()
    return jsonify(case_json(get_db(), updated))


@app.delete("/api/cases/<int:case_id>")
@auth_required(roles=["vet"])
def delete_case(case_id):
    conn = get_db()
    try:
        case = conn.execute("SELECT * FROM cases WHERE id=?", (case_id,)).fetchone()
        if not case:
            return jsonify({"error": "Case not found"}), 404
        if case["status"] not in ("RECOVERED", "CLOSED"):
            return jsonify({"error": "Only RECOVERED or CLOSED reports can be deleted."}), 400

        conn.execute("DELETE FROM case_updates WHERE case_id=?", (case_id,))
        conn.execute("DELETE FROM lab_reports WHERE case_id=?", (case_id,))
        conn.execute("DELETE FROM lab_requests WHERE case_id=?", (case_id,))
        conn.execute("DELETE FROM prescriptions WHERE case_id=?", (case_id,))
        conn.execute("DELETE FROM case_visits WHERE case_id=?", (case_id,))
        conn.execute("DELETE FROM treatment_responses WHERE case_id=?", (case_id,))
        conn.execute("DELETE FROM sample_custody_events WHERE sample_id IN (SELECT id FROM samples WHERE case_id=?)", (case_id,))
        conn.execute("DELETE FROM samples WHERE case_id=?", (case_id,))
        conn.execute("DELETE FROM cases WHERE id=?", (case_id,))
        audit_log(conn, "DELETE_CASE", "case", case_id, actor_id=g.user["uid"],
                  actor_name=g.user["name"], actor_role=g.user["role"],
                  details={"case_no": case["case_no"]})
        conn.commit()
        return jsonify({"ok": True, "deleted": case["case_no"]})
    finally:
        conn.close()


@app.post("/api/cases/<int:case_id>/location")
@auth_required(roles=["owner", "vet", "govt"])
def update_case_location(case_id):
    conn = get_db()
    case = _case_for_visit(conn, case_id)
    if not case or not _can_access_case(conn, case):
        conn.close(); return jsonify({"error": "Case not found or not authorized"}), 404
    data = request.get_json(force=True) or {}
    # A report location is a submission-time snapshot, not a live/current
    # position. Once captured, it must remain attached to this case.
    if conn.execute("SELECT 1 FROM case_locations WHERE case_id=? LIMIT 1", (case_id,)).fetchone():
        conn.close(); return jsonify({"error": "report location is immutable once captured"}), 409
    try:
        _record_case_location(conn, case_id, data, g.user["uid"], default_source="GPS")
    except (TypeError, ValueError) as exc:
        conn.close(); return jsonify({"error": str(exc)}), 400
    loc = _case_location(conn, case_id)
    emit_event(conn, "CASE_LOCATION_UPDATED", {"case_id": case_id, "source": loc["source"], "has_coordinates": loc["latitude"] is not None}, case_id=case_id, actor_id=g.user["uid"], actor_role=g.user["role"], idempotency_key=f"case-location:{case_id}:{loc['id']}")
    conn.commit(); conn.close(); return jsonify(loc), 201


@app.get("/api/cases/<int:case_id>/location")
@auth_required()
def get_case_location(case_id):
    conn = get_db(); case = _case_for_visit(conn, case_id)
    if not case or not _can_access_case(conn, case):
        conn.close(); return jsonify({"error": "Case not found or not authorized"}), 404
    loc = _case_location(conn, case_id); conn.close()
    return jsonify(loc or {"source": "NOT_AVAILABLE", "latitude": None, "longitude": None})


@app.post("/api/telephony/location")
@auth_required(roles=["owner", "vet", "govt"])
def telephony_location():
    """Attach authenticated farmer-app GPS to an active helpline call.

    A PSTN carrier does not provide GPS here. The farmer's signed-in browser or
    app explicitly posts the observation while the call session is active.
    """
    data = request.get_json(force=True) or {}
    call_sid = str(data.get("call_sid") or "").strip()
    if not call_sid: return jsonify({"error": "call_sid is required"}), 400
    conn = get_db()
    call = conn.execute("SELECT * FROM ivr_calls WHERE call_sid=?", (call_sid,)).fetchone()
    if not call: conn.close(); return jsonify({"error": "Call session not found"}), 404
    report = conn.execute("SELECT * FROM ivr_reports WHERE call_sid=? ORDER BY id DESC LIMIT 1", (call_sid,)).fetchone()
    session = conn.execute("SELECT * FROM ivr_sessions WHERE call_sid=? ORDER BY id DESC LIMIT 1", (call_sid,)).fetchone()
    if g.user["role"] == "owner":
        if not ((report and report["caller_user_id"] == g.user["uid"]) or (session and session["caller_user_id"] == g.user["uid"])):
            conn.close(); return jsonify({"error": "This call is not associated with your account"}), 403
    case = _case_for_visit(conn, report["case_id"]) if report and report["case_id"] else None
    if case and not _can_access_case(conn, case):
        conn.close(); return jsonify({"error": "Not authorized for this case"}), 403
    if not case:
        conn.close(); return jsonify({"error": "The helpline case has not been created yet"}), 409
    try:
        payload = {**data, "source": "GPS"}
        _record_case_location(conn, case["id"], payload, g.user["uid"], default_source="GPS")
        lat, lng = float(payload.get("lat", payload.get("latitude"))), float(payload.get("lng", payload.get("longitude")))
    except (TypeError, ValueError) as exc:
        conn.close(); return jsonify({"error": f"valid GPS coordinates are required: {exc}"}), 400
    conn.execute("UPDATE ivr_reports SET location_lat=?,location_lng=?,location_source='GPS',location_accuracy=? WHERE id=?", (lat, lng, data.get("accuracy", "farmer app GPS"), report["id"]))
    emit_event(conn, "HELPLINE_LOCATION_UPDATED", {"call_sid": call_sid, "case_id": case["id"], "source": "GPS"}, case_id=case["id"], actor_id=g.user["uid"], actor_role=g.user["role"], idempotency_key=f"helpline-location:{call_sid}:{data.get('timestamp', '')}:{lat}:{lng}")
    if case["vet_id"]:
        notify(conn, case["vet_id"], f"GPS location shared for helpline case {case['case_no']}.", "case", case_id=case["id"], event_type="HELPLINE_LOCATION_UPDATED")
    for official in conn.execute("SELECT id FROM users WHERE role='govt'").fetchall():
        notify(conn, official["id"], f"Authorised GPS location received for helpline case {case['case_no']}.", "case", case_id=case["id"], event_type="HELPLINE_LOCATION_UPDATED")
    conn.commit(); loc = _case_location(conn, case["id"]); conn.close()
    return jsonify({"ok": True, "location": loc})


# ------------------------------------------------ case-based visit tracking ---
# Coordinates are NEVER derived from a district or a clock.  A map position is
# present only after an authorised device posts a validated GPS observation.
VISIT_STATUSES = {
    "ASSIGNED", "ACCEPTED", "PREPARING", "ON_THE_WAY", "NEARBY", "ARRIVED",
    "CONSULTATION_STARTED", "COMPLETED", "CANCELLED", "DELAYED",
}
STOP_TRACKING_STATUSES = {"ARRIVED", "COMPLETED", "CANCELLED"}


def _case_for_visit(conn, case_id):
    return conn.execute("SELECT * FROM cases WHERE id=?", (case_id,)).fetchone()


def _can_access_case(conn, case, *, allow_govt=True):
    return _can_access_case_for_user(conn, case, g.user, allow_govt=allow_govt)


def _can_access_case_for_user(conn, case, user, *, allow_govt=True):
    if not case:
        return False
    role = user["role"]
    if role == "owner":
        return case["owner_id"] == user["uid"]
    animal = conn.execute("SELECT district FROM animals WHERE id=?", (case["animal_id"],)).fetchone()
    case_district = (animal["district"] if animal else None) or ""
    actor = conn.execute("SELECT district FROM users WHERE id=?", (user["uid"],)).fetchone()
    actor_district = (actor["district"] if actor else None) or ""
    if role == "vet":
        if case["vet_id"] == user["uid"]:
            return True
        # Unassigned cases are visible to veterinarians only within their
        # registered jurisdiction; assignment itself is a stronger grant.
        return case["vet_id"] is None and (not actor_district or not case_district or actor_district.lower() == case_district.lower())
    if role == "govt":
        return allow_govt and (not actor_district or not case_district or actor_district.lower() == case_district.lower())
    return False


def _can_access_sample(conn, sample):
    """Apply case-level authorization to every sample read/write path."""
    if not sample:
        return False
    if g.user["role"] == "lab":
        return True  # laboratory staff are restricted to sample/report APIs
    case = _case_for_visit(conn, sample["case_id"])
    return _can_access_case(conn, case)


def _case_location(conn, case_id):
    row = conn.execute(
        "SELECT * FROM case_locations WHERE case_id=? AND is_current=1 ORDER BY id DESC LIMIT 1",
        (case_id,),
    ).fetchone()
    return dict(row) if row else None


def _visit_response(conn, visit_id, include_history=False, include_vet_location=False):
    visit = conn.execute("SELECT * FROM case_visits WHERE id=?", (visit_id,)).fetchone()
    if not visit:
        return None
    out = dict(visit)
    case = conn.execute("SELECT case_no,owner_id,vet_id FROM cases WHERE id=?", (visit["case_id"],)).fetchone()
    out["case_no"] = case["case_no"] if case else None
    out["farmer_location"] = _case_location(conn, visit["case_id"])
    session = conn.execute(
        "SELECT * FROM visit_tracking_sessions WHERE visit_id=? ORDER BY id DESC LIMIT 1", (visit_id,)
    ).fetchone()
    out["tracking"] = dict(session) if session else None
    latest = conn.execute(
        "SELECT * FROM visit_locations WHERE visit_id=? ORDER BY captured_at DESC, id DESC LIMIT 1", (visit_id,)
    ).fetchone()
    # Veterinarian GPS is private. It is only returned to the veterinarian
    # who owns this visit; farmer and government clients must not receive it.
    if include_vet_location:
        out["latest_location"] = dict(latest) if latest else None
        if include_history:
            out["location_history"] = [dict(r) for r in conn.execute(
                "SELECT * FROM visit_locations WHERE visit_id=? ORDER BY captured_at ASC, id ASC", (visit_id,)
            ).fetchall()]
    else:
        out["latest_location"] = None
        # Visit routing origin is also veterinarian location data.
        out["from_lat"] = None
        out["from_lng"] = None
    return out


def _stop_tracking_for_visit(conn, visit_id, reason):
    conn.execute(
        "UPDATE visit_tracking_sessions SET status='STOPPED', stopped_at=datetime('now'), stop_reason=? WHERE visit_id=? AND status='ACTIVE'",
        (reason, visit_id),
    )


def _record_case_location(conn, case_id, data, actor_id, *, default_source="NOT_AVAILABLE"):
    lat, lng = data.get("latitude", data.get("lat")), data.get("longitude", data.get("lng"))
    source = (data.get("source") or default_source or "NOT_AVAILABLE").upper()
    if source not in {"GPS", "NETWORK", "FARMER_PROVIDED", "PROFILE", "MANUAL", "NOT_AVAILABLE"}:
        raise ValueError("invalid location source")
    if lat is None or lng is None:
        lat = lng = None
        source = "NOT_AVAILABLE" if source in {"GPS", "NETWORK"} else source
    else:
        lat, lng = float(lat), float(lng)
        if not (-90 <= lat <= 90 and -180 <= lng <= 180):
            raise ValueError("coordinates are out of range")
    conn.execute("UPDATE case_locations SET is_current=0 WHERE case_id=?", (case_id,))
    conn.execute(
        "INSERT INTO case_locations (case_id,source,latitude,longitude,accuracy_m,village,block,district,state,captured_by) VALUES (?,?,?,?,?,?,?,?,?,?)",
        (case_id, source, lat, lng, data.get("accuracy", data.get("accuracy_m")),
         data.get("village"), data.get("block"), data.get("district"), data.get("state", "Maharashtra"), actor_id),
    )


@app.post("/api/cases/<int:case_id>/visit")
@auth_required(roles=["vet"])
def start_visit(case_id):
    conn = get_db()
    case = _case_for_visit(conn, case_id)
    if not case:
        conn.close()
        return jsonify({"error": "Case not found"}), 404
    if not _can_access_case(conn, case, allow_govt=False):
        conn.close()
        return jsonify({"error": "This case is outside your authorized veterinarian jurisdiction"}), 403
    # Serialize acceptance and assignment. The frontend guard prevents double
    # clicks, but the database must also be safe for two concurrent requests.
    conn.execute("BEGIN IMMEDIATE")
    existing = conn.execute(
        "SELECT id FROM case_visits WHERE case_id=? AND vet_id=? AND status NOT IN ('COMPLETED','CANCELLED') ORDER BY id DESC LIMIT 1",
        (case_id, g.user["uid"]),
    ).fetchone()
    if existing:
        conn.commit()
        result = _visit_response(conn, existing["id"])
        conn.close()
        return jsonify(result)
    # A case already accepted by another veterinarian is not reassigned.
    if case["vet_id"] is not None and case["vet_id"] != g.user["uid"]:
        conn.rollback(); conn.close()
        return jsonify({"error": "This case has already been accepted by another veterinarian"}), 409
    data = request.get_json(silent=True) or {}
    # Optional coordinates are accepted only as an actual device observation.
    # They are not replaced with district centroids when absent.
    from_lat, from_lng = data.get("from_lat"), data.get("from_lng")
    to_loc = _case_location(conn, case_id)
    to_lat = to_lng = None
    if to_loc and to_loc.get("latitude") is not None:
        to_lat, to_lng = to_loc["latitude"], to_loc["longitude"]
    cur = conn.execute(
        "INSERT INTO case_visits (case_id,vet_id,status,from_lat,from_lng,to_lat,to_lng,travel_seconds,started_at) VALUES (?,?,?,?,?,?,?,NULL,datetime('now'))",
        (case_id, g.user["uid"], "ON_THE_WAY", from_lat, from_lng, to_lat, to_lng),
    )
    assigned = conn.execute(
        "UPDATE cases SET vet_id=?, status='UNDER INVESTIGATION', updated_at=datetime('now') WHERE id=? AND (vet_id IS NULL OR vet_id=?)",
        (g.user["uid"], case_id, g.user["uid"]),
    )
    if assigned.rowcount != 1:
        conn.rollback(); conn.close()
        return jsonify({"error": "This case has already been accepted by another veterinarian"}), 409
    conn.execute("INSERT INTO case_updates (case_id,status,note,updated_by) VALUES (?,?,?,?)",
                 (case_id, "ON_THE_WAY", "Veterinarian accepted the case and started the visit.", g.user["name"]))
    emit_event(conn, "VET_STARTED_VISIT", {"visit_id": cur.lastrowid, "status": "ON_THE_WAY"},
               case_id=case_id, actor_id=g.user["uid"], actor_role="vet",
               idempotency_key=f"visit-start:{case_id}:{g.user['uid']}")
    notify(conn, case["owner_id"], f"Dr. {g.user['name']} is on the way for case {case['case_no']}.", "case", case_id=case_id, event_type="VET_STARTED_VISIT")
    conn.commit()
    result = _visit_response(conn, cur.lastrowid)
    conn.close()
    return jsonify(result), 201


@app.post("/api/visits")
@auth_required(roles=["vet"])
def create_visit():
    data = request.get_json(force=True) or {}
    case_id = data.get("case_id")
    if not case_id:
        return jsonify({"error": "case_id is required"}), 400
    return start_visit(int(case_id))


@app.patch("/api/visits/<int:visit_id>/status")
@auth_required(roles=["vet"])
def patch_visit_status(visit_id):
    data = request.get_json(force=True) or {}
    status = str(data.get("status") or "").upper()
    if status not in VISIT_STATUSES:
        return jsonify({"error": f"Invalid visit status. Allowed: {', '.join(sorted(VISIT_STATUSES))}"}), 400
    conn = get_db()
    visit = conn.execute("SELECT * FROM case_visits WHERE id=?", (visit_id,)).fetchone()
    if not visit:
        conn.close()
        return jsonify({"error": "Visit not found"}), 404
    if visit["vet_id"] != g.user["uid"]:
        conn.close()
        return jsonify({"error": "Only the assigned veterinarian can update this visit"}), 403
    updates = ["status=?"]
    params = [status]
    if status == "ARRIVED": updates.append("arrived_at=datetime('now')")
    if status == "COMPLETED": updates.append("completed_at=datetime('now')")
    conn.execute(f"UPDATE case_visits SET {', '.join(updates)} WHERE id=?", (*params, visit_id))
    if status in STOP_TRACKING_STATUSES:
        _stop_tracking_for_visit(conn, visit_id, status.lower())
    case = _case_for_visit(conn, visit["case_id"])
    note = data.get("note") or f"Veterinarian visit status changed to {status}."
    conn.execute("INSERT INTO case_updates (case_id,status,note,updated_by) VALUES (?,?,?,?)",
                 (visit["case_id"], status, note, g.user["name"]))
    event_type = {"ARRIVED": "VET_ARRIVED", "COMPLETED": "VISIT_COMPLETED", "CANCELLED": "VISIT_CANCELLED"}.get(status, "VISIT_STATUS_UPDATED")
    emit_event(conn, event_type, {"visit_id": visit_id, "status": status}, case_id=visit["case_id"], actor_id=g.user["uid"], actor_role="vet",
               idempotency_key=f"visit-status:{visit_id}:{status}:{data.get('client_txn_id','')}")
    if case:
        notify(conn, case["owner_id"], f"Veterinary visit for {case['case_no']} is now {status.replace('_',' ')}.", "case", case_id=visit["case_id"], event_type=event_type)
    conn.commit()
    result = _visit_response(conn, visit_id)
    conn.close()
    return jsonify(result)


@app.put("/api/cases/<int:case_id>/visit")
@auth_required(roles=["vet"])
def update_visit(case_id):
    data = request.get_json(force=True) or {}
    status = str(data.get("status") or "").upper()
    mapping = {"ON_THE_WAY": "ON_THE_WAY", "ARRIVED": "ARRIVED", "COMPLETED": "COMPLETED", "CANCELLED": "CANCELLED", "DELAYED": "DELAYED", "NEARBY": "NEARBY", "CONSULTATION_STARTED": "CONSULTATION_STARTED"}
    if status not in mapping:
        return jsonify({"error": "Invalid status"}), 400
    conn = get_db()
    visit = conn.execute("SELECT id FROM case_visits WHERE case_id=? AND vet_id=? ORDER BY id DESC LIMIT 1", (case_id, g.user["uid"])).fetchone()
    conn.close()
    if not visit:
        return jsonify({"error": "No visit found for this case"}), 404
    # The current request body already contains the status and g.user was
    # authenticated by this route; call the shared implementation without
    # creating a second Flask request context.
    return patch_visit_status.__wrapped__(visit["id"])


@app.post("/api/visits/<int:visit_id>/start-tracking")
@auth_required(roles=["vet"])
def start_visit_tracking(visit_id):
    conn = get_db()
    visit = conn.execute("SELECT * FROM case_visits WHERE id=?", (visit_id,)).fetchone()
    if not visit:
        conn.close(); return jsonify({"error": "Visit not found"}), 404
    if visit["vet_id"] != g.user["uid"]:
        conn.close(); return jsonify({"error": "Only the assigned veterinarian can start tracking"}), 403
    if visit["status"] in STOP_TRACKING_STATUSES:
        conn.close(); return jsonify({"error": "Tracking cannot start for a completed or cancelled visit"}), 409
    active = conn.execute("SELECT * FROM visit_tracking_sessions WHERE visit_id=? AND status='ACTIVE'", (visit_id,)).fetchone()
    if active:
        result = _visit_response(conn, visit_id); conn.close(); return jsonify(result)
    data = request.get_json(silent=True) or {}
    minutes = max(5, min(int(data.get("duration_minutes", os.environ.get("VISIT_TRACKING_MAX_MINUTES", "120"))), 240))
    cur = conn.execute(
        "INSERT INTO visit_tracking_sessions (visit_id,case_id,vet_id,status,expires_at) VALUES (?,?,?,'ACTIVE',datetime('now',?))",
        (visit_id, visit["case_id"], g.user["uid"], f"+{minutes} minutes"),
    )
    emit_event(conn, "TRACKING_STARTED", {"visit_id": visit_id, "tracking_session_id": cur.lastrowid, "expires_in_minutes": minutes},
               case_id=visit["case_id"], actor_id=g.user["uid"], actor_role="vet", idempotency_key=f"tracking-start:{visit_id}:{cur.lastrowid}")
    case = _case_for_visit(conn, visit["case_id"])
    if case:
        notify(conn, case["owner_id"], f"Veterinarian location sharing started for case {case['case_no']}.", "case", case_id=visit["case_id"], event_type="TRACKING_STARTED")
    conn.commit()
    result = _visit_response(conn, visit_id); conn.close(); return jsonify(result), 201


@app.post("/api/visits/<int:visit_id>/stop-tracking")
@auth_required(roles=["vet"])
def stop_visit_tracking(visit_id):
    conn = get_db()
    visit = conn.execute("SELECT * FROM case_visits WHERE id=?", (visit_id,)).fetchone()
    if not visit:
        conn.close(); return jsonify({"error": "Visit not found"}), 404
    if visit["vet_id"] != g.user["uid"]:
        conn.close(); return jsonify({"error": "Only the assigned veterinarian can stop tracking"}), 403
    reason = (request.get_json(silent=True) or {}).get("reason") or "veterinarian_stopped"
    _stop_tracking_for_visit(conn, visit_id, reason)
    emit_event(conn, "TRACKING_STOPPED", {"visit_id": visit_id, "reason": reason}, case_id=visit["case_id"], actor_id=g.user["uid"], actor_role="vet",
               idempotency_key=f"tracking-stop:{visit_id}:{reason}")
    conn.commit(); result = _visit_response(conn, visit_id); conn.close(); return jsonify(result)


@app.post("/api/visits/<int:visit_id>/location")
@auth_required(roles=["vet"])
def post_visit_location(visit_id):
    data = request.get_json(force=True) or {}
    try:
        lat = float(data.get("latitude", data.get("lat")))
        lng = float(data.get("longitude", data.get("lng")))
        if not (-90 <= lat <= 90 and -180 <= lng <= 180): raise ValueError
        accuracy = float(data["accuracy"]) if data.get("accuracy") is not None else None
        speed = float(data["speed"]) if data.get("speed") is not None else None
        heading = float(data["heading"]) if data.get("heading") is not None else None
    except (TypeError, ValueError):
        return jsonify({"error": "valid latitude and longitude are required"}), 400
    conn = get_db()
    visit = conn.execute("SELECT * FROM case_visits WHERE id=?", (visit_id,)).fetchone()
    if not visit:
        conn.close(); return jsonify({"error": "Visit not found"}), 404
    if visit["vet_id"] != g.user["uid"]:
        conn.close(); return jsonify({"error": "Only the assigned veterinarian can post location"}), 403
    session = conn.execute("SELECT * FROM visit_tracking_sessions WHERE visit_id=? AND status='ACTIVE' AND expires_at > datetime('now')", (visit_id,)).fetchone()
    if not session:
        conn.execute("UPDATE visit_tracking_sessions SET status='EXPIRED', stop_reason='session_expired' WHERE visit_id=? AND status='ACTIVE'", (visit_id,))
        conn.commit(); conn.close(); return jsonify({"error": "location sharing is not active or has expired"}), 409
    captured_at = str(data.get("timestamp") or datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ"))
    idem = str(data.get("idempotency_key") or f"gps:{visit_id}:{captured_at}:{lat:.6f}:{lng:.6f}")
    try:
        conn.execute("INSERT INTO visit_locations (tracking_session_id,visit_id,case_id,vet_id,latitude,longitude,accuracy_m,speed_mps,heading_deg,captured_at,idempotency_key) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                     (session["id"], visit_id, visit["case_id"], g.user["uid"], lat, lng, accuracy, speed, heading, captured_at, idem))
    except sqlite3.IntegrityError:
        existing = conn.execute("SELECT * FROM visit_locations WHERE idempotency_key=?", (idem,)).fetchone()
        conn.close(); return jsonify({"ok": True, "duplicate": True, "location": dict(existing) if existing else None})
    emit_event(conn, "VET_LOCATION_UPDATED", {"visit_id": visit_id, "location_id": conn.execute("SELECT last_insert_rowid() id").fetchone()["id"], "captured_at": captured_at}, case_id=visit["case_id"], actor_id=g.user["uid"], actor_role="vet", idempotency_key=f"event:{idem}")
    conn.commit()
    latest = conn.execute("SELECT * FROM visit_locations WHERE idempotency_key=?", (idem,)).fetchone()
    conn.close(); return jsonify({"ok": True, "location": dict(latest)}), 201


@app.get("/api/visits/<int:visit_id>/location")
@auth_required()
def get_visit_location(visit_id):
    conn = get_db()
    visit = conn.execute("SELECT * FROM case_visits WHERE id=?", (visit_id,)).fetchone()
    if not visit:
        conn.close(); return jsonify({"error": "Visit not found"}), 404
    case = _case_for_visit(conn, visit["case_id"])
    if not _can_access_case(conn, case, allow_govt=True):
        conn.close(); return jsonify({"error": "Not authorized to view this location"}), 403
    result = _visit_response(conn, visit_id, include_history=(g.user["role"] == "vet"), include_vet_location=(g.user["role"] == "vet" and visit["vet_id"] == g.user["uid"]))
    conn.close(); return jsonify(result)


@app.get("/api/cases/<int:case_id>/track")
@auth_required()
def track_visit(case_id):
    conn = get_db()
    case = _case_for_visit(conn, case_id)
    if not _can_access_case(conn, case):
        conn.close(); return jsonify({"error": "Not authorized"}), 403
    visit = conn.execute("SELECT id FROM case_visits WHERE case_id=? ORDER BY id DESC LIMIT 1", (case_id,)).fetchone()
    if not visit:
        conn.close(); return jsonify({"visit": None, "location_status": "NOT_STARTED", "current_position": None})
    result = _visit_response(conn, visit["id"], include_history=False, include_vet_location=(g.user["role"] == "vet" and visit["vet_id"] == g.user["uid"]))
    result["visit"] = {k: result.get(k) for k in ("id", "case_id", "vet_id", "status", "started_at", "arrived_at", "completed_at", "from_lat", "from_lng", "to_lat", "to_lng", "travel_seconds")}
    vet = conn.execute("SELECT full_name,mobile,specialization FROM users WHERE id=?", (result.get("vet_id"),)).fetchone()
    result["vet"] = dict(vet) if vet else None
    result["location_status"] = "AVAILABLE" if result["latest_location"] else "UNAVAILABLE"
    result["current_position"] = ({"lat": result["latest_location"]["latitude"], "lng": result["latest_location"]["longitude"], "accuracy_m": result["latest_location"]["accuracy_m"], "captured_at": result["latest_location"]["captured_at"]} if result["latest_location"] else None)
    result["destination"] = ({"lat": result["farmer_location"]["latitude"], "lng": result["farmer_location"]["longitude"]} if result["farmer_location"] and result["farmer_location"]["latitude"] is not None else None)
    result["eta_seconds"] = None
    conn.close(); return jsonify(result)


@app.get("/api/vet/reports")
@auth_required(roles=["vet"])
def vet_reports():
    conn = get_db()
    rows = conn.execute("SELECT * FROM cases WHERE vet_id=? OR vet_id IS NULL ORDER BY id DESC", (g.user["uid"],)).fetchall()
    out = [case_json(conn, r) for r in rows]
    conn.close()
    return jsonify(out)


@app.get("/api/vets")
@auth_required()
def list_vets():
    conn = get_db()
    district = request.args.get("district")
    if district:
        vets = conn.execute("SELECT id, full_name, mobile, email, specialization, village, block, district FROM users WHERE role='vet' AND district=?", (district,)).fetchall()
    else:
        vets = conn.execute("SELECT id, full_name, mobile, email, specialization, village, block, district FROM users WHERE role='vet'").fetchall()
    conn.close(); return jsonify([row_to_dict(v) for v in vets])


@app.get("/api/vet/search")
@auth_required(roles=["vet", "govt"])
def vet_search():
    q = (request.args.get("q") or "").strip()
    if not q: return jsonify({"animals": [], "herds": []})
    conn = get_db()
    animals = conn.execute("SELECT * FROM animals WHERE animal_code LIKE ? OR mobile LIKE ? OR owner_name LIKE ? OR animal_name LIKE ?", (f"%{q}%", f"%{q}%", f"%{q}%", f"%{q}%")).fetchall()
    herds = conn.execute("SELECT * FROM herds WHERE herd_code LIKE ?", (f"%{q}%",)).fetchall()
    conn.close(); return jsonify({"animals": [row_to_dict(a) for a in animals], "herds": [row_to_dict(h) for h in herds]})


# ----------------------------------- digital sample & transport tracking -----
@app.post("/api/samples")
@auth_required(roles=["vet", "owner", "lab"])
def create_sample():
    """Create a digital laboratory specimen with unique Sample QR, GPS and timestamp."""
    data = request.get_json(force=True) or {}
    case_id = data.get("case_id")
    sample_type = data.get("sample_type", "Blood Sample")

    conn = get_db()
    case = conn.execute("SELECT * FROM cases WHERE id=?", (case_id,)).fetchone()
    if not case:
        conn.close()
        return jsonify({"error": "Invalid case ID"}), 404
    if g.user["role"] == "vet" and not _can_access_case(conn, case, allow_govt=False):
        conn.close()
        return jsonify({"error": "This case is assigned outside your authorized jurisdiction"}), 403
    if g.user["role"] == "owner" and case["owner_id"] != g.user["uid"]:
        conn.close()
        return jsonify({"error": "Not authorized"}), 403

    animal = conn.execute("SELECT * FROM animals WHERE id=?", (case["animal_id"],)).fetchone()
    district = animal["district"] or "PUN"
    sample_code = next_code(conn, "SMP", "samples", "sample_code", district=district[:3].upper())
    qr_token = f"sqr_{uuid.uuid4().hex}"
    qr_payload = f"PASHU:SAMPLE:{qr_token}"

    lat = data.get("collection_lat")
    lng = data.get("collection_lng")
    is_manual = int(data.get("is_manual_location", 0))

    if lat is None or lng is None:
        # Location is genuinely unavailable; do not substitute a district
        # centroid for biological sample GPS.
        lat = lng = None
        is_manual = 0

    cur = conn.execute(
        """
        INSERT INTO samples
        (sample_code, qr_token, qr_payload, animal_id, case_id, lab_request_id,
         sample_type, status, collector_id, collection_lat, collection_lng,
         is_manual_location, collection_notes, transporter_name, transporter_phone, collected_at)
        VALUES (?,?,?,?,?,?,?,'COLLECTED',?,?,?,?,?,?,?,datetime('now'))
        """,
        (sample_code, qr_token, qr_payload, case["animal_id"], case["id"],
         data.get("lab_request_id"), sample_type, g.user["uid"],
         lat, lng, is_manual, data.get("collection_notes"),
         data.get("transporter_name"), data.get("transporter_phone"))
    )
    sample_id = cur.lastrowid
    emit_event(conn, "LAB_SAMPLE_COLLECTED", {"sample_id": sample_id, "sample_code": sample_code, "status": "SAMPLE_COLLECTED"}, case_id=case["id"], actor_id=g.user["uid"], actor_role=g.user["role"], idempotency_key=f"sample-created:{sample_id}")

    # Record first custody event
    conn.execute(
        """
        INSERT INTO sample_custody_events
        (sample_id, status, action, actor_id, actor_name, actor_role, lat, lng, is_manual_location, notes)
        VALUES (?, 'COLLECTED', 'Biological specimen collected', ?, ?, ?, ?, ?, ?, ?)
        """,
        (sample_id, g.user["uid"], g.user["name"], g.user["role"], lat, lng, is_manual,
         f"Sample type: {sample_type}. Notes: {data.get('collection_notes', 'Standard collection')}")
    )

    conn.execute("UPDATE cases SET status='SAMPLE COLLECTED', updated_at=datetime('now') WHERE id=?", (case["id"],))
    conn.execute("INSERT INTO case_updates (case_id, status, note, updated_by) VALUES (?,?,?,?)",
                 (case["id"], "SAMPLE COLLECTED", f"Sample {sample_code} ({sample_type}) collected.", g.user["name"]))

    audit_log(conn, "CREATE_SAMPLE", "sample", sample_id, actor_id=g.user["uid"],
              actor_name=g.user["name"], actor_role=g.user["role"],
              details={"sample_code": sample_code, "qr_token": qr_token, "sample_type": sample_type})
    conn.commit()

    sample = conn.execute("SELECT * FROM samples WHERE id=?", (sample_id,)).fetchone()
    res = row_to_dict(sample)
    res["qr_image"] = make_qr_image_data_url(qr_payload)
    conn.close()
    return jsonify(res), 201


@app.get("/api/samples")
@auth_required()
def list_samples():
    conn = get_db()
    case_id = request.args.get("case_id")
    status = request.args.get("status")

    query = "SELECT s.*, a.animal_code, a.species, c.case_no FROM samples s JOIN animals a ON a.id=s.animal_id JOIN cases c ON c.id=s.case_id WHERE 1=1"
    params = []
    if case_id:
        query += " AND s.case_id=?"
        params.append(case_id)
    if status:
        query += " AND s.status=?"
        params.append(status)
    query += " ORDER BY s.id DESC"

    rows = conn.execute(query, params).fetchall()
    out = []
    for r in rows:
        if not _can_access_sample(conn, r):
            continue
        d = dict(r)
        d["qr_image"] = make_qr_image_data_url(d["qr_payload"])
        out.append(d)
    conn.close()
    return jsonify(out)


@app.get("/api/samples/<int:sample_id>")
@auth_required()
def get_sample_detail(sample_id):
    conn = get_db()
    sample = conn.execute(
        "SELECT s.*, a.animal_code, a.species, a.breed, c.case_no FROM samples s "
        "JOIN animals a ON a.id=s.animal_id JOIN cases c ON c.id=s.case_id WHERE s.id=?",
        (sample_id,)
    ).fetchone()
    if not sample:
        conn.close()
        return jsonify({"error": "Sample not found"}), 404
    if not _can_access_sample(conn, sample):
        conn.close()
        return jsonify({"error": "Not authorized for this sample"}), 403

    events = conn.execute(
        "SELECT * FROM sample_custody_events WHERE sample_id=? ORDER BY id ASC", (sample_id,)
    ).fetchall()
    conn.close()

    res = dict(sample)
    res["qr_image"] = make_qr_image_data_url(res["qr_payload"])
    res["custody_events"] = [dict(e) for e in events]
    return jsonify(res)


@app.get("/api/samples/lookup-qr")
@auth_required()
def lookup_sample_qr():
    raw = (request.args.get("token") or request.args.get("code") or request.args.get("payload") or "").strip()
    if not raw:
        return jsonify({"error": "Missing token or code parameter"}), 400

    token = raw
    if token.startswith("PASHU:SAMPLE:"):
        token = token[len("PASHU:SAMPLE:"):]

    conn = get_db()
    sample = conn.execute("SELECT id FROM samples WHERE qr_token=?", (token,)).fetchone()
    if not sample:
        sample = conn.execute("SELECT id FROM samples WHERE UPPER(sample_code)=UPPER(?)", (token,)).fetchone()

    if not sample:
        conn.close()
        return jsonify({"error": f"No sample matched identifier: '{raw}'"}), 404

    sid = sample["id"]
    conn.close()
    return get_sample_detail(sid)


@app.post("/api/samples/<int:sample_id>/transport")
@auth_required(roles=["vet", "lab"])
def update_sample_transport(sample_id):
    """Advance sample transport lifecycle status and record chain of custody."""
    data = request.get_json(force=True) or {}
    new_status = data.get("status")
    if new_status not in SAMPLE_STATUSES:
        return jsonify({"error": f"Invalid sample status. Must be one of: {', '.join(SAMPLE_STATUSES)}"}), 400

    conn = get_db()
    sample = conn.execute("SELECT * FROM samples WHERE id=?", (sample_id,)).fetchone()
    if not sample:
        conn.close()
        return jsonify({"error": "Sample not found"}), 404
    if not _can_access_sample(conn, sample):
        conn.close(); return jsonify({"error": "Not authorized for this sample"}), 403
    transport_transitions = {
        "COLLECTED": {"READY_FOR_PICKUP"}, "READY_FOR_PICKUP": {"PICKED_UP", "IN_TRANSIT"},
        "PICKED_UP": {"IN_TRANSIT", "ARRIVED_AT_LAB"}, "IN_TRANSIT": {"ARRIVED_AT_LAB"},
        "ARRIVED_AT_LAB": set(), "LAB_RECEIVED": set(), "TESTING": set(), "RESULT_READY": set(), "COMPLETED": set(), "REJECTED": set(),
    }
    if new_status != sample["status"] and new_status not in transport_transitions.get(sample["status"], set()):
        conn.close(); return jsonify({"error": f"Invalid sample transition {sample['status']} -> {new_status}"}), 409
    if g.user["role"] == "lab" and new_status not in {"ARRIVED_AT_LAB"}:
        conn.close(); return jsonify({"error": "Laboratory staff use receiving/testing workflow for this transition"}), 403

    t_name = data.get("transporter_name") or sample["transporter_name"]
    t_phone = data.get("transporter_phone") or sample["transporter_phone"]
    notes = data.get("notes") or f"Transport status moved to {new_status}"
    lat = data.get("lat")
    lng = data.get("lng")

    conn.execute(
        "UPDATE samples SET status=?, transporter_name=?, transporter_phone=?, updated_at=datetime('now') WHERE id=?",
        (new_status, t_name, t_phone, sample_id)
    )
    emit_event(conn, "LAB_SAMPLE_STATUS_UPDATED", {"sample_id": sample_id, "status": _sample_workflow_status(new_status)}, case_id=sample["case_id"], actor_id=g.user["uid"], actor_role=g.user["role"], idempotency_key=f"sample-status:{sample_id}:{new_status}:{data.get('idempotency_key','')}")

    action_label = {
        "READY_FOR_PICKUP": "Marked ready for cold-chain transport",
        "PICKED_UP": f"Courier collected package (Transporter: {t_name})",
        "IN_TRANSIT": "Package in transit to regional testing laboratory",
        "ARRIVED_AT_LAB": "Courier delivered package at lab intake dock",
    }.get(new_status, f"Transport updated to {new_status}")

    conn.execute(
        """
        INSERT INTO sample_custody_events
        (sample_id, status, action, actor_id, actor_name, actor_role, lat, lng, notes)
        VALUES (?,?,?,?,?,?,?,?,?)
        """,
        (sample_id, new_status, action_label, g.user["uid"], g.user["name"], g.user["role"], lat, lng, notes)
    )

    audit_log(conn, "SAMPLE_TRANSPORT_UPDATE", "sample", sample_id, actor_id=g.user["uid"],
              actor_name=g.user["name"], actor_role=g.user["role"],
              details={"status": new_status, "transporter": t_name})
    conn.commit()
    conn.close()
    return get_sample_detail(sample_id)


# --------------------------------------------- laboratory workflow endpoints --
@app.get("/api/lab/summary")
@auth_required(roles=["lab", "vet", "govt"])
def get_lab_summary():
    conn = get_db()
    pending = conn.execute(
        "SELECT COUNT(*) c FROM samples WHERE status IN ('COLLECTED','READY_FOR_PICKUP','PICKED_UP','IN_TRANSIT','ARRIVED_AT_LAB')"
    ).fetchone()["c"]
    in_testing = conn.execute(
        "SELECT COUNT(*) c FROM samples WHERE status IN ('LAB_RECEIVED','TESTING','RESULT_READY')"
    ).fetchone()["c"]
    completed_today = conn.execute(
        "SELECT COUNT(*) c FROM samples WHERE status='COMPLETED' AND updated_at >= date('now')"
    ).fetchone()["c"]
    rejected = conn.execute("SELECT COUNT(*) c FROM samples WHERE status='REJECTED'").fetchone()["c"]
    conn.close()
    return jsonify({
        "pending_receiving": pending,
        "in_testing": in_testing,
        "completed_today": completed_today,
        "rejected_samples": rejected,
    })


@app.get("/api/lab/queue")
@auth_required(roles=["lab", "vet", "govt"])
def get_lab_queue():
    conn = get_db()
    samples = conn.execute(
        """
        SELECT s.*, a.animal_code, a.species, c.case_no, c.disease_suspected,
               u.full_name collector_name
        FROM samples s
        JOIN animals a ON a.id=s.animal_id
        JOIN cases c ON c.id=s.case_id
        LEFT JOIN users u ON u.id=s.collector_id
        ORDER BY s.id DESC
        """
    ).fetchall()
    samples = [s for s in samples if _can_access_sample(conn, s)]
    conn.close()

    out = []
    for s in samples:
        d = dict(s)
        d["qr_image"] = make_qr_image_data_url(d["qr_payload"])
        out.append(d)
    return jsonify(out)


@app.post("/api/samples/<int:sample_id>/receive")
@auth_required(roles=["lab", "vet"])
def receive_sample(sample_id):
    """Lab receiving workflow: accept (LAB_RECEIVED) or reject with reason."""
    data = request.get_json(force=True) or {}
    action = data.get("action", "accept").lower()
    notes = data.get("notes") or ""
    rejection_reason = (data.get("rejection_reason") or "").strip()

    conn = get_db()
    sample = conn.execute("SELECT * FROM samples WHERE id=?", (sample_id,)).fetchone()
    if not sample:
        conn.close()
        return jsonify({"error": "Sample not found"}), 404

    if not _can_access_sample(conn, sample):
        conn.close(); return jsonify({"error": "Not authorized for this sample"}), 403
    case = conn.execute("SELECT * FROM cases WHERE id=?", (sample["case_id"],)).fetchone()
    animal = conn.execute("SELECT * FROM animals WHERE id=?", (sample["animal_id"],)).fetchone()
    if sample["status"] not in {"ARRIVED_AT_LAB", "IN_TRANSIT", "PICKED_UP", "LAB_RECEIVED"}:
        conn.close(); return jsonify({"error": f"Sample cannot be received from status {sample['status']}"}), 409

    if action == "reject":
        if not rejection_reason:
            conn.close()
            return jsonify({"error": "Rejection reason is required when rejecting a sample."}), 400

        conn.execute(
            "UPDATE samples SET status='REJECTED', rejection_reason=?, updated_at=datetime('now') WHERE id=?",
            (rejection_reason, sample_id)
        )
        emit_event(conn, "LAB_SAMPLE_STATUS_UPDATED", {"sample_id": sample_id, "status": "RESULT_PENDING", "reason": rejection_reason}, case_id=sample["case_id"], actor_id=g.user["uid"], actor_role=g.user["role"], idempotency_key=f"sample-reject:{sample_id}:{rejection_reason}")
        conn.execute(
            """
            INSERT INTO sample_custody_events (sample_id, status, action, actor_id, actor_name, actor_role, notes)
            VALUES (?, 'REJECTED', 'Specimen rejected by laboratory', ?, ?, ?, ?)
            """,
            (sample_id, g.user["uid"], g.user["name"], g.user["role"], f"Rejection reason: {rejection_reason}. Notes: {notes}")
        )
        # Notify vet and owner
        if case["vet_id"]:
            notify(conn, case["vet_id"], f"⚠️ Lab sample {sample['sample_code']} REJECTED: {rejection_reason}", "lab")
        notify(conn, case["owner_id"], f"Lab sample for animal {animal['animal_code']} could not be processed: {rejection_reason}", "lab")

        audit_log(conn, "REJECT_SAMPLE", "sample", sample_id, actor_id=g.user["uid"],
                  actor_name=g.user["name"], actor_role=g.user["role"],
                  details={"reason": rejection_reason})
        conn.commit()
        conn.close()
        return jsonify({"ok": True, "status": "REJECTED", "reason": rejection_reason})

    else:
        # Accept
        conn.execute("UPDATE samples SET status='LAB_RECEIVED', updated_at=datetime('now') WHERE id=?", (sample_id,))
        emit_event(conn, "LAB_SAMPLE_RECEIVED", {"sample_id": sample_id, "status": "RECEIVED_BY_LAB"}, case_id=sample["case_id"], actor_id=g.user["uid"], actor_role=g.user["role"], idempotency_key=f"sample-received:{sample_id}")
        conn.execute(
            """
            INSERT INTO sample_custody_events (sample_id, status, action, actor_id, actor_name, actor_role, notes)
            VALUES (?, 'LAB_RECEIVED', 'Sample inspected and accepted for diagnostic processing', ?, ?, ?, ?)
            """,
            (sample_id, g.user["uid"], g.user["name"], g.user["role"], notes or "Seal intact, cold-chain temperature confirmed.")
        )
        if case["vet_id"]:
            notify(conn, case["vet_id"], f"Lab has received sample {sample['sample_code']} for testing.", "lab")

        audit_log(conn, "ACCEPT_SAMPLE", "sample", sample_id, actor_id=g.user["uid"],
                  actor_name=g.user["name"], actor_role=g.user["role"])
        conn.commit()
        conn.close()
        return jsonify({"ok": True, "status": "LAB_RECEIVED"})


@app.post("/api/samples/<int:sample_id>/test")
@auth_required(roles=["lab", "vet"])
def start_sample_testing(sample_id):
    conn = get_db()
    sample = conn.execute("SELECT * FROM samples WHERE id=?", (sample_id,)).fetchone()
    if not sample:
        conn.close(); return jsonify({"error": "Sample not found"}), 404
    if not _can_access_sample(conn, sample):
        conn.close(); return jsonify({"error": "Not authorized for this sample"}), 403
    if sample["status"] not in {"LAB_RECEIVED", "TESTING"}:
        conn.close(); return jsonify({"error": f"Sample must be LAB_RECEIVED before testing; current status is {sample['status']}"}), 409
    conn.execute("UPDATE samples SET status='TESTING', updated_at=datetime('now') WHERE id=?", (sample_id,))
    emit_event(conn, "LAB_TEST_STARTED", {"sample_id": sample_id, "status": "TESTING"}, case_id=sample["case_id"], actor_id=g.user["uid"], actor_role=g.user["role"], idempotency_key=f"sample-testing:{sample_id}")
    conn.execute(
        """
        INSERT INTO sample_custody_events (sample_id, status, action, actor_id, actor_name, actor_role, notes)
        VALUES (?, 'TESTING', 'Diagnostic test procedures in progress', ?, ?, ?, 'Assigned to analytical bench')
        """,
        (sample_id, g.user["uid"], g.user["name"], g.user["role"])
    )
    conn.commit()
    conn.close()
    return jsonify({"ok": True, "status": "TESTING"})


@app.post("/api/samples/<int:sample_id>/results")
@auth_required(roles=["lab", "vet"])
def submit_sample_results(sample_id):
    """Structured result entry for diagnostic testing."""
    data = request.get_json(force=True) or {}
    test_name = (data.get("test_name") or "Diagnostic Test").strip()
    result_val = (data.get("result") or "PENDING").strip()

    conn = get_db()
    sample = conn.execute("SELECT * FROM samples WHERE id=?", (sample_id,)).fetchone()
    if not sample:
        conn.close()
        return jsonify({"error": "Sample not found"}), 404
    if not _can_access_sample(conn, sample):
        conn.close(); return jsonify({"error": "Not authorized for this sample"}), 403
    if sample["status"] != "TESTING":
        conn.close(); return jsonify({"error": f"Sample must be TESTING before result entry; current status is {sample['status']}"}), 409

    case = conn.execute("SELECT * FROM cases WHERE id=?", (sample["case_id"],)).fetchone()
    report_no = next_code(conn, "LAB", "lab_reports", "report_no")

    cur = conn.execute(
        """
        INSERT INTO lab_reports
        (report_no, lab_request_id, case_id, animal_id, herd_id, sample, sample_id,
         test_name, test_type, test_method, result, quantitative_result, units,
         reference_range_min, reference_range_max, reference_range_text,
         abnormal_flag, technician_name, verification_status, comments, test_date, entered_by)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,'UNVERIFIED',?,date('now'),?)
        """,
        (report_no, sample["lab_request_id"], case["id"], case["animal_id"], case["herd_id"],
         sample["sample_type"], sample_id, test_name, data.get("test_type", "Serology"),
         data.get("test_method", "ELISA"), result_val, data.get("quantitative_result"),
         data.get("units"), data.get("reference_range_min"), data.get("reference_range_max"),
         data.get("reference_range_text"), data.get("abnormal_flag", "Normal"),
         data.get("technician_name", g.user["name"]), data.get("comments"), g.user["uid"])
    )
    rep_id = cur.lastrowid
    conn.execute("UPDATE lab_reports SET current_status='RESULT_READY', report_generated_at=datetime('now'), last_updated_at=datetime('now') WHERE id=?", (rep_id,))
    conn.execute("UPDATE samples SET status='RESULT_READY', updated_at=datetime('now') WHERE id=?", (sample_id,))
    conn.execute("INSERT INTO lab_report_status_history (report_id,from_status,to_status,actor_id,actor_role,note) VALUES (?,?,?,?,?,?)", (rep_id, "TESTING", "RESULT_READY", g.user["uid"], g.user["role"], data.get("comments")))
    emit_event(conn, "LAB_RESULT_READY", {"sample_id": sample_id, "report_id": rep_id, "status": "RESULT_READY", "result": result_val}, case_id=case["id"], actor_id=g.user["uid"], actor_role=g.user["role"], idempotency_key=f"lab-result:{rep_id}")
    conn.execute(
        """
        INSERT INTO sample_custody_events (sample_id, status, action, actor_id, actor_name, actor_role, notes)
        VALUES (?, 'RESULT_READY', 'Diagnostic result entered awaiting verification', ?, ?, ?, ?)
        """,
        (sample_id, g.user["uid"], g.user["name"], g.user["role"], f"Report {report_no}: {test_name} = {result_val}")
    )
    conn.commit()
    rep = conn.execute("SELECT * FROM lab_reports WHERE id=?", (rep_id,)).fetchone()
    conn.close()
    return jsonify(row_to_dict(rep)), 201


@app.post("/api/lab/reports/<int:report_id>/verify")
@auth_required(roles=["lab", "vet"])
def verify_lab_report(report_id):
    """Verify and publish laboratory report — automatically triggers vet notification (Feature Group 10)."""
    conn = get_db()
    rep = conn.execute("SELECT * FROM lab_reports WHERE id=?", (report_id,)).fetchone()
    if not rep:
        conn.close()
        return jsonify({"error": "Lab report not found"}), 404

    case = conn.execute("SELECT * FROM cases WHERE id=?", (rep["case_id"],)).fetchone()
    animal = conn.execute("SELECT * FROM animals WHERE id=?", (rep["animal_id"],)).fetchone()
    if g.user["role"] == "vet" and not _can_access_case(conn, case, allow_govt=False):
        conn.close(); return jsonify({"error": "Not authorized for this laboratory report"}), 403
    if rep["verification_status"] == "VERIFIED":
        conn.close(); return jsonify(row_to_dict(rep))

    conn.execute(
        """
        UPDATE lab_reports
        SET verification_status='VERIFIED', current_status='REPORT_SENT_TO_VET', verified_by=?, verified_at=datetime('now'), published_at=datetime('now'), report_generated_at=COALESCE(report_generated_at, datetime('now')), last_updated_at=datetime('now')
        WHERE id=?
        """,
        (g.user["uid"], report_id)
    )

    conn.execute("INSERT INTO lab_report_status_history (report_id,from_status,to_status,actor_id,actor_role,note) VALUES (?,?,?,?,?,?)", (report_id, rep["current_status"] or "RESULT_READY", "REPORT_SENT_TO_VET", g.user["uid"], g.user["role"], "Report verified and released"))
    emit_event(conn, "LAB_REPORT_SUBMITTED", {"report_id": report_id, "report_no": rep["report_no"], "status": "REPORT_SENT_TO_VET", "result": rep["result"]}, case_id=rep["case_id"], actor_id=g.user["uid"], actor_role=g.user["role"], idempotency_key=f"lab-report-submitted:{report_id}")

    if rep["sample_id"]:
        conn.execute("UPDATE samples SET status='COMPLETED', updated_at=datetime('now') WHERE id=?", (rep["sample_id"],))
        conn.execute(
            """
            INSERT INTO sample_custody_events (sample_id, status, action, actor_id, actor_name, actor_role, notes)
            VALUES (?, 'COMPLETED', 'Diagnostic report verified and released', ?, ?, ?, ?)
            """,
            (rep["sample_id"], g.user["uid"], g.user["name"], g.user["role"], f"Report {rep['report_no']} verified by {g.user['name']}")
        )

    # FEATURE GROUP 10: AUTOMATIC NOTIFICATIONS
    # 1. Notify Veterinarian
    if case["vet_id"]:
        vet_msg = f"🧪 Verified Lab Report {rep['report_no']} is ready for Case {case['case_no']} (Animal {animal['animal_code']}, Test: {rep['test_name']}, Result: {rep['result']})."
        notify(conn, case["vet_id"], vet_msg, "lab", case_id=case["id"], event_type="LAB_REPORT_SUBMITTED")

    # Critical/positive findings are also escalated to authorised government
    # users; only the case number and clinical result are shared here.
    if str(rep["result"] or "").upper() in {"POSITIVE", "CRITICAL", "ABNORMAL"} or str(rep["abnormal_flag"] or "").lower() not in {"normal", ""}:
        for official in conn.execute("SELECT id FROM users WHERE role='govt'").fetchall():
            notify(conn, official["id"], f"Critical laboratory result {rep['report_no']} for case {case['case_no']} in {animal['district'] or 'unknown'}.", "lab", case_id=case["id"], event_type="CRITICAL_LAB_RESULT")

    # 2. Notify Owner
    owner_msg = f"🧪 Laboratory report {rep['report_no']} for your animal {animal['animal_code']} (Case {case['case_no']}) has been released."
    notify(conn, case["owner_id"], owner_msg, "lab")

    # Audit logging
    audit_log(conn, "VERIFY_LAB_REPORT", "lab_report", report_id, actor_id=g.user["uid"],
              actor_name=g.user["name"], actor_role=g.user["role"],
              details={"report_no": rep["report_no"], "result": rep["result"], "test_name": rep["test_name"]})

    conn.commit()
    updated = conn.execute("SELECT * FROM lab_reports WHERE id=?", (report_id,)).fetchone()
    conn.close()
    return jsonify(row_to_dict(updated))


# ------------------------------------------------------------ lab tests --
@app.post("/api/lab/requests")
@auth_required(roles=["vet", "lab"])
def create_lab_request():
    data = request.get_json(force=True) or {}
    conn = get_db()
    case = conn.execute("SELECT * FROM cases WHERE id=?", (data.get("case_id"),)).fetchone()
    if not case:
        conn.close()
        return jsonify({"error": "Invalid case ID"}), 404
    cur = conn.execute(
        "INSERT INTO lab_requests (case_id, animal_id, herd_id, sample_type, test_requested, priority, notes, status, requested_by) "
        "VALUES (?,?,?,?,?,?,?,'REQUESTED',?)",
        (case["id"], case["animal_id"], case["herd_id"], data.get("sample_type"), data.get("test_requested"),
         data.get("priority", "Normal"), data.get("notes"), g.user["uid"]),
    )
    conn.execute("UPDATE cases SET status='LAB PENDING', updated_at=datetime('now') WHERE id=?", (case["id"],))
    conn.execute("INSERT INTO case_updates (case_id, status, note, updated_by) VALUES (?,?,?,?)",
                 (case["id"], "LAB PENDING", f"Lab test requested: {data.get('test_requested')}", g.user["name"]))
    notify(conn, case["owner_id"], f"A laboratory test has been requested for case {case['case_no']}.", "lab")
    conn.commit()
    req = conn.execute("SELECT * FROM lab_requests WHERE id=?", (cur.lastrowid,)).fetchone()
    conn.close()
    return jsonify(row_to_dict(req)), 201


@app.get("/api/lab/reports")
@auth_required()
def list_lab_reports():
    conn = get_db()
    rows = conn.execute(
        "SELECT l.*, a.animal_code, a.animal_name, c.case_no, c.owner_id FROM lab_reports l "
        "JOIN animals a ON a.id=l.animal_id JOIN cases c ON c.id=l.case_id "
        "ORDER BY l.id DESC"
    ).fetchall()
    if g.user["role"] == "owner":
        rows = [r for r in rows if r["owner_id"] == g.user["uid"]]
    elif g.user["role"] in ("vet", "govt"):
        rows = [r for r in rows if _can_access_case(conn, _case_for_visit(conn, r["case_id"]), allow_govt=True)]
    elif g.user["role"] != "lab":
        rows = []
    conn.close()
    return jsonify([row_to_dict(r) for r in rows])


@app.post("/api/lab/reports")
@auth_required(roles=["vet", "lab"])
def create_lab_report():
    data = request.get_json(force=True) or {}
    conn = get_db()
    case = conn.execute("SELECT * FROM cases WHERE id=?", (data.get("case_id"),)).fetchone()
    if not case:
        conn.close()
        return jsonify({"error": "Invalid case ID"}), 404
    if g.user["role"] == "vet" and case["vet_id"] not in (None, g.user["uid"]):
        conn.close(); return jsonify({"error": "This case is assigned to another veterinarian"}), 403

    animal = conn.execute("SELECT * FROM animals WHERE id=?", (case["animal_id"],)).fetchone()
    report_no = next_code(conn, "LAB", "lab_reports", "report_no")
    cur = conn.execute(
        """
        INSERT INTO lab_reports
        (report_no, lab_request_id, case_id, animal_id, herd_id, sample, sample_id,
         test_name, test_type, test_method, result, quantitative_result, units,
         reference_range_min, reference_range_max, reference_range_text,
         abnormal_flag, technician_name, verification_status, comments, test_date, notes, entered_by)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,'VERIFIED',?,?,?,?)
        """,
        (report_no, data.get("lab_request_id"), case["id"], case["animal_id"], case["herd_id"],
         data.get("sample"), data.get("sample_id"), data.get("test_name"),
         data.get("test_type", "Diagnostic"), data.get("test_method", "Standard"),
         data.get("result"), data.get("quantitative_result"), data.get("units"),
         data.get("reference_range_min"), data.get("reference_range_max"),
         data.get("reference_range_text"), data.get("abnormal_flag", "Normal"),
         g.user["name"], data.get("comments"), data.get("test_date", str(date.today())),
         data.get("notes"), g.user["uid"]),
    )
    rep_id = cur.lastrowid
    if data.get("lab_request_id"):
        conn.execute("UPDATE lab_requests SET status='REPORT READY' WHERE id=?", (data["lab_request_id"],))

    conn.execute("INSERT INTO case_updates (case_id, status, note, updated_by) VALUES (?,?,?,?)",
                 (case["id"], case["status"], f"Lab report {report_no} entered: {data.get('test_name')} = {data.get('result')}", g.user["name"]))

    # Automatic Notifications
    notify(conn, case["owner_id"], f"Your lab report {report_no} is ready for case {case['case_no']}.", "lab")
    if case["vet_id"] and case["vet_id"] != g.user["uid"]:
        notify(conn, case["vet_id"], f"Lab report {report_no} ready for case {case['case_no']} (Animal {animal['animal_code']}).", "lab")

    audit_log(conn, "CREATE_LAB_REPORT", "lab_report", rep_id, actor_id=g.user["uid"],
              actor_name=g.user["name"], actor_role=g.user["role"],
              details={"report_no": report_no, "test_name": data.get("test_name"), "result": data.get("result")})

    conn.commit()
    rep = conn.execute("SELECT * FROM lab_reports WHERE id=?", (rep_id,)).fetchone()
    conn.close()
    return jsonify(row_to_dict(rep)), 201


# ------------------------------------------ veterinarian laboratory tracking ---
LAB_REPORT_STATUSES = {
    "SAMPLE_COLLECTED", "SAMPLE_IN_TRANSIT", "RECEIVED_BY_LAB", "TESTING",
    "RESULT_PENDING", "RESULT_READY", "REPORT_REVIEWED", "REPORT_SENT_TO_VET",
    "VET_ACTION_REQUIRED", "CASE_RESOLVED",
}


def _sample_workflow_status(sample_status, verification_status=None, report_status=None):
    if verification_status == "VERIFIED":
        return "REPORT_SENT_TO_VET"
    if report_status in LAB_REPORT_STATUSES:
        return report_status
    return {
        "COLLECTED": "SAMPLE_COLLECTED", "READY_FOR_PICKUP": "SAMPLE_IN_TRANSIT",
        "PICKED_UP": "SAMPLE_IN_TRANSIT", "IN_TRANSIT": "SAMPLE_IN_TRANSIT",
        "ARRIVED_AT_LAB": "RECEIVED_BY_LAB", "LAB_RECEIVED": "RECEIVED_BY_LAB",
        "TESTING": "TESTING", "RESULT_READY": "RESULT_READY", "COMPLETED": "REPORT_SENT_TO_VET",
        "REJECTED": "RESULT_PENDING",
    }.get(sample_status, "RESULT_PENDING")


def _lab_report_row(conn, row):
    d = dict(row)
    d["current_status"] = _sample_workflow_status(d.get("sample_status"), d.get("verification_status"), d.get("current_status"))
    d["report_generated_at"] = d.get("report_generated_at") or d.get("published_at") or d.get("report_created_at") or d.get("created_at")
    d["last_updated_at"] = d.get("last_updated_at") or d.get("sample_updated_at") or d.get("report_created_at") or d.get("created_at")
    d["report_id"] = d.get("report_id") or d.get("id")
    d["report_no"] = d.get("report_no") or None
    # Privacy: callers of this helper are already RBAC checked. Phone is
    # included for a veterinarian assigned to the case and govt visibility.
    d["farmer_phone"] = d.get("farmer_phone")
    return d


def _vet_lab_where(conn):
    actor = conn.execute("SELECT district FROM users WHERE id=?", (g.user["uid"],)).fetchone()
    district = (actor["district"] if actor else None) or ""
    if g.user["role"] == "vet":
        if district:
            return "AND (c.vet_id=? OR (c.vet_id IS NULL AND LOWER(COALESCE(a.district,''))=LOWER(?)))", [g.user["uid"], district]
        return "AND (c.vet_id=? OR c.vet_id IS NULL)", [g.user["uid"]]
    if g.user["role"] == "govt":
        return ("AND LOWER(COALESCE(a.district,''))=LOWER(?)", [district]) if district else ("", [])
    return "AND 1=0", []


LAB_TRACK_QUERY = """
SELECT s.id sample_id, s.sample_code, s.qr_token, s.status sample_status,
       s.collected_at sample_collection_at, s.updated_at sample_updated_at,
       s.case_id, c.case_no, c.symptoms, c.disease_suspected, c.severity,
       c.vet_id assigned_vet_id, c.created_at case_created_at,
       a.animal_type, a.species animal_species, a.animal_name, a.village,
       a.block, a.district, a.owner_id, owner.full_name farmer_name, owner.mobile farmer_phone,
       av.full_name assigned_veterinarian, COALESCE(lab.full_name, (SELECT full_name FROM users WHERE role='lab' ORDER BY id LIMIT 1)) laboratory,
       lr.test_requested test_type, lr.priority request_priority,
       r.id report_id, r.report_no, r.test_name, r.result, r.notes report_notes,
       r.verification_status, r.current_status, r.test_date,
       r.report_generated_at, r.last_updated_at, r.published_at, r.created_at report_created_at
FROM samples s
JOIN cases c ON c.id=s.case_id
JOIN animals a ON a.id=s.animal_id
JOIN users owner ON owner.id=c.owner_id
LEFT JOIN users av ON av.id=c.vet_id
LEFT JOIN lab_requests lr ON lr.id=s.lab_request_id
LEFT JOIN lab_reports r ON r.sample_id=s.id
LEFT JOIN users lab ON lab.id=r.entered_by
WHERE 1=1
"""


@app.get("/api/veterinarian/lab-reports")
@auth_required(roles=["vet", "govt"])
def veterinarian_lab_reports():
    conn = get_db()
    where, params = _vet_lab_where(conn)
    query = LAB_TRACK_QUERY + " " + where
    filters = []
    q = (request.args.get("q") or request.args.get("search") or "").strip()
    if q:
        query += " AND (c.case_no LIKE ? OR s.sample_code LIKE ? OR COALESCE(r.report_no,'') LIKE ? OR owner.full_name LIKE ? OR owner.mobile LIKE ? OR COALESCE(c.disease_suspected,'') LIKE ?)"
        filters += [f"%{q}%"] * 6
    for arg, col in (("district", "a.district"), ("disease", "c.disease_suspected"), ("laboratory", "lab.full_name")):
        val = (request.args.get(arg) or "").strip()
        if val:
            query += f" AND LOWER(COALESCE({col},''))=LOWER(?)"; filters.append(val)
    priority = (request.args.get("priority") or "").strip()
    if priority:
        query += " AND LOWER(COALESCE(lr.priority,''))=LOWER(?)"; filters.append(priority)
    status = (request.args.get("status") or "").strip().upper()
    if status in LAB_REPORT_STATUSES:
        # Status is normalized in Python because legacy sample rows and the
        # report lifecycle coexist during migration.
        pass
    date_from, date_to = request.args.get("date_from"), request.args.get("date_to")
    if date_from:
        query += " AND date(COALESCE(s.collected_at,s.created_at)) >= date(?)"; filters.append(date_from)
    if date_to:
        query += " AND date(COALESCE(s.collected_at,s.created_at)) <= date(?)"; filters.append(date_to)
    sort = request.args.get("sort", "updated").lower()
    order = {"date": "COALESCE(s.collected_at,s.created_at)", "priority": "COALESCE(lr.priority,'Normal')", "status": "s.status", "updated": "COALESCE(r.last_updated_at,s.updated_at)"}.get(sort, "COALESCE(r.last_updated_at,s.updated_at)")
    direction = "ASC" if request.args.get("order", "desc").lower() == "asc" else "DESC"
    query += f" ORDER BY {order} {direction} LIMIT 500"
    rows = conn.execute(query, params + filters).fetchall()
    out = [_lab_report_row(conn, r) for r in rows]
    # Legacy report-entry API permits a report without a linked sample. Keep
    # those records visible in the veterinary lifecycle instead of silently
    # dropping them during the additive sample migration.
    legacy = conn.execute("""
        SELECT r.*, c.case_no, c.symptoms, c.disease_suspected, c.severity, c.vet_id assigned_vet_id,
               c.created_at case_created_at, a.animal_type, a.species animal_species, a.animal_name,
               a.village, a.block, a.district, a.owner_id, owner.full_name farmer_name, owner.mobile farmer_phone,
               av.full_name assigned_veterinarian, COALESCE(lab.full_name,(SELECT full_name FROM users WHERE role='lab' ORDER BY id LIMIT 1)) laboratory,
               lr.test_requested test_type, lr.priority request_priority
        FROM lab_reports r JOIN cases c ON c.id=r.case_id JOIN animals a ON a.id=r.animal_id
        JOIN users owner ON owner.id=c.owner_id LEFT JOIN users av ON av.id=c.vet_id
        LEFT JOIN lab_requests lr ON lr.id=r.lab_request_id LEFT JOIN users lab ON lab.id=r.entered_by
        WHERE r.sample_id IS NULL
    """ + " " + where, params).fetchall()
    for r in legacy:
        d = dict(r)
        d.update({"sample_id": None, "sample_code": d.get("sample") or "Not linked", "qr_token": None,
                  "sample_status": None, "sample_collection_at": d.get("test_date"), "sample_updated_at": d.get("created_at"),
                  "report_id": d.get("id"), "report_created_at": d.get("created_at")})
        out.append(_lab_report_row(conn, d))
    if status in LAB_REPORT_STATUSES:
        out = [r for r in out if r["current_status"] == status]
    conn.close()
    return jsonify(out)


@app.get("/api/veterinarian/lab-reports/<int:report_id>")
@auth_required(roles=["vet", "govt"])
def veterinarian_lab_report_detail(report_id):
    conn = get_db()
    row = conn.execute(LAB_TRACK_QUERY.replace("WHERE 1=1", "WHERE (r.id=? OR (r.id IS NULL AND s.id=?))"), (report_id, report_id)).fetchone()
    if not row:
        # Details for a legacy report created without a digital sample.
        row = conn.execute("""
            SELECT r.*, c.case_no, c.symptoms, c.disease_suspected, c.severity, c.vet_id assigned_vet_id,
                   c.created_at case_created_at, a.animal_type, a.species animal_species, a.animal_name,
                   a.village, a.block, a.district, a.owner_id, owner.full_name farmer_name, owner.mobile farmer_phone,
                   av.full_name assigned_veterinarian, COALESCE(lab.full_name,(SELECT full_name FROM users WHERE role='lab' ORDER BY id LIMIT 1)) laboratory,
                   lr.test_requested test_type, lr.priority request_priority
            FROM lab_reports r JOIN cases c ON c.id=r.case_id JOIN animals a ON a.id=r.animal_id
            JOIN users owner ON owner.id=c.owner_id LEFT JOIN users av ON av.id=c.vet_id
            LEFT JOIN lab_requests lr ON lr.id=r.lab_request_id LEFT JOIN users lab ON lab.id=r.entered_by
            WHERE r.id=? AND r.sample_id IS NULL
        """, (report_id,)).fetchone()
    if not row:
        conn.close(); return jsonify({"error": "Laboratory report not found"}), 404
    case = _case_for_visit(conn, row["case_id"])
    if not _can_access_case(conn, case, allow_govt=True):
        conn.close(); return jsonify({"error": "Not authorized for this laboratory report"}), 403
    result = _lab_report_row(conn, row)
    result["timeline"] = [dict(x) for x in conn.execute("SELECT * FROM lab_report_status_history WHERE report_id=? ORDER BY id", (report_id,)).fetchall()]
    result["audit_history"] = [dict(x) for x in conn.execute("SELECT * FROM audit_events WHERE entity_type='lab_report' AND entity_id=? ORDER BY id", (str(report_id),)).fetchall()]
    result["result_history"] = [dict(x) for x in conn.execute("SELECT id,report_no,test_name,result,verification_status,created_at,verified_at,published_at FROM lab_reports WHERE case_id=? ORDER BY id", (row["case_id"],)).fetchall()]
    conn.close(); return jsonify(result)


@app.patch("/api/lab-reports/<int:report_id>/status")
@auth_required(roles=["vet", "lab", "govt"])
def update_lab_report_status(report_id):
    data = request.get_json(force=True) or {}
    new_status = str(data.get("status") or "").upper()
    if new_status not in LAB_REPORT_STATUSES:
        return jsonify({"error": "Unsupported laboratory report status"}), 400
    conn = get_db()
    report = conn.execute("SELECT * FROM lab_reports WHERE id=?", (report_id,)).fetchone()
    if not report:
        conn.close(); return jsonify({"error": "Laboratory report not found"}), 404
    case = _case_for_visit(conn, report["case_id"])
    if g.user["role"] != "lab" and not _can_access_case(conn, case, allow_govt=True):
        conn.close(); return jsonify({"error": "Not authorized for this report"}), 403
    lab_allowed = {"REPORT_REVIEWED", "REPORT_SENT_TO_VET"}
    vet_allowed = {"REPORT_REVIEWED", "VET_ACTION_REQUIRED", "CASE_RESOLVED"}
    allowed = lab_allowed if g.user["role"] == "lab" else vet_allowed
    if new_status not in allowed:
        conn.close(); return jsonify({"error": f"Role {g.user['role']} cannot transition to {new_status}"}), 403
    old = report["current_status"] or _sample_workflow_status(None, report["verification_status"])
    permitted = {
        "RESULT_PENDING": {"RESULT_READY", "REPORT_REVIEWED", "REPORT_SENT_TO_VET"},
        "RESULT_READY": {"REPORT_REVIEWED", "REPORT_SENT_TO_VET"},
        "REPORT_REVIEWED": {"REPORT_SENT_TO_VET"},
        "REPORT_SENT_TO_VET": {"REPORT_REVIEWED", "VET_ACTION_REQUIRED", "CASE_RESOLVED"},
        "VET_ACTION_REQUIRED": {"CASE_RESOLVED"},
        "CASE_RESOLVED": set(),
    }
    if new_status != old and new_status not in permitted.get(old, set()):
        conn.close(); return jsonify({"error": f"Invalid report transition {old} -> {new_status}"}), 409
    conn.execute("UPDATE lab_reports SET current_status=?, last_updated_at=datetime('now') WHERE id=?", (new_status, report_id))
    conn.execute("INSERT INTO lab_report_status_history (report_id,from_status,to_status,actor_id,actor_role,note) VALUES (?,?,?,?,?,?)",
                 (report_id, old, new_status, g.user["uid"], g.user["role"], data.get("note")))
    emit_event(conn, "LAB_REPORT_STATUS_UPDATED", {"report_id": report_id, "from_status": old, "status": new_status}, case_id=report["case_id"], actor_id=g.user["uid"], actor_role=g.user["role"],
               idempotency_key=f"lab-status:{report_id}:{new_status}:{data.get('idempotency_key','')}")
    if case and case["vet_id"] and new_status in ("RESULT_READY", "REPORT_SENT_TO_VET", "VET_ACTION_REQUIRED"):
        notify(conn, case["vet_id"], f"Laboratory report {report['report_no']} status: {new_status}.", "lab", case_id=report["case_id"], event_type="LAB_REPORT_STATUS_UPDATED")
    if case and new_status == "CASE_RESOLVED":
        conn.execute("UPDATE cases SET status='CLOSED', updated_at=datetime('now') WHERE id=?", (case["id"],))
    audit_log(conn, "UPDATE_LAB_REPORT_STATUS", "lab_report", report_id, actor_id=g.user["uid"], actor_name=g.user["name"], actor_role=g.user["role"], details={"from": old, "to": new_status})
    conn.commit(); result = dict(conn.execute("SELECT * FROM lab_reports WHERE id=?", (report_id,)).fetchone()); conn.close(); return jsonify(result)


@app.get("/api/veterinarian/lab-reports/<int:report_id>/download")
@auth_required(roles=["vet", "govt"])
def download_lab_report(report_id):
    response = veterinarian_lab_report_detail.__wrapped__(report_id)
    if getattr(response, "status_code", 200) != 200:
        return response
    data = response.get_json()
    lines = ["Pashu-Shield Laboratory Report", f"Report ID: {data.get('report_no') or report_id}", f"Case ID: {data.get('case_no')}", f"Sample ID: {data.get('sample_code')}", f"Current status: {data.get('current_status')}", f"Farmer: {data.get('farmer_name')}", f"Disease suspected: {data.get('disease_suspected')}", f"Test: {data.get('test_name') or data.get('test_type')}", f"Result: {data.get('result') or 'Pending'}", f"Generated: {data.get('report_generated_at')}", "", "This report is an authenticated application export."]
    return Response("\n".join(str(x or "") for x in lines), mimetype="text/plain", headers={"Content-Disposition": f"attachment; filename=lab-report-{report_id}.txt"})


# --------------------------------------------------------- prescriptions --
@app.post("/api/prescriptions")
@auth_required(roles=["vet"])
def create_prescription():
    """Prescription issuance with strict allergy conflict verification and authorized override."""
    data = request.get_json(force=True) or {}
    conn = get_db()
    try:
        case = conn.execute("SELECT * FROM cases WHERE id=?", (data.get("case_id"),)).fetchone()
        if not case:
            return jsonify({"error": "Invalid case ID"}), 404

        animal = conn.execute("SELECT * FROM animals WHERE id=?", (case["animal_id"],)).fetchone()
        medicine = (data.get("medicine") or "").strip()

        # FEATURE GROUP 3: ALLERGY CONFLICT CHECKING
        active_allergies = conn.execute(
            "SELECT * FROM animal_allergies WHERE animal_id=? AND status='Active'",
            (case["animal_id"],)
        ).fetchall()
        conflict = check_allergy_conflict(medicine, [dict(a) for a in active_allergies])

        override = bool(data.get("override") or data.get("allergy_override"))
        override_reason = (data.get("override_reason") or "").strip()

        if conflict and not override:
            return jsonify({
                "error": f"⚠️ CONTRAINDICATION ALERT: Animal {animal['animal_code']} has a documented {conflict['allergy_severity']} allergy to '{conflict['allergen']}' (Reaction: {conflict['reaction']}). Prescription halted.",
                "conflict": True,
                "allergen": conflict["allergen"],
                "severity": conflict["allergy_severity"],
                "reaction": conflict["reaction"],
                "requires_override": True,
            }), 409

        cur = conn.execute(
            """
            INSERT INTO prescriptions
            (case_id, animal_id, herd_id, diagnosis, medicine, dosage, frequency,
             duration, instructions, follow_up_date, allergy_override, override_reason, vet_id)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            (case["id"], case["animal_id"], case["herd_id"], data.get("diagnosis"), medicine,
             data.get("dosage"), data.get("frequency"), data.get("duration"), data.get("instructions"),
             data.get("follow_up_date"), int(override), override_reason if override else None, g.user["uid"]),
        )
        presc_id = cur.lastrowid

        # Add to animal medications history
        conn.execute(
            """
            INSERT INTO animal_medications
            (animal_id, case_id, prescription_id, medication_name, dosage, frequency,
             start_date, end_date, status, prescribed_by, allergy_override, override_reason, notes)
            VALUES (?,?,?,?,?,?,date('now'),?,'Active',?,?,?,?)
            """,
            (case["animal_id"], case["id"], presc_id, medicine, data.get("dosage"),
             data.get("frequency"), data.get("follow_up_date"), g.user["uid"],
             int(override), override_reason if override else None, data.get("instructions", ""))
        )

        conn.execute("UPDATE cases SET status='TREATMENT', diagnosis=COALESCE(?, diagnosis), updated_at=datetime('now') WHERE id=?",
                     (data.get("diagnosis"), case["id"]))
        note_txt = f"E-prescription issued: {medicine}"
        if override:
            note_txt += f" (Allergy override granted: {override_reason})"

        conn.execute("INSERT INTO case_updates (case_id, status, note, updated_by) VALUES (?,?,?,?)",
                     (case["id"], "TREATMENT", note_txt, g.user["name"]))
        notify(conn, case["owner_id"], f"An e-prescription is available for case {case['case_no']}.", "prescription")

        audit_log(conn, "ISSUE_PRESCRIPTION" if not override else "ALLERGY_OVERRIDE_PRESCRIBED",
                  "prescription", presc_id, actor_id=g.user["uid"],
                  actor_name=g.user["name"], actor_role=g.user["role"],
                  details={"medicine": medicine, "override": override, "override_reason": override_reason})

        conn.commit()
        presc = conn.execute("SELECT * FROM prescriptions WHERE id=?", (presc_id,)).fetchone()
        return jsonify(row_to_dict(presc)), 201
    finally:
        conn.close()


@app.get("/api/prescriptions")
@auth_required()
def list_prescriptions():
    conn = get_db()
    if g.user["role"] == "owner":
        rows = conn.execute(
            "SELECT p.*, c.case_no, a.animal_code, u.full_name vet_name FROM prescriptions p "
            "JOIN cases c ON c.id=p.case_id JOIN animals a ON a.id=p.animal_id LEFT JOIN users u ON u.id=p.vet_id "
            "WHERE c.owner_id=? ORDER BY p.id DESC", (g.user["uid"],)
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT p.*, c.case_no, a.animal_code, u.full_name vet_name FROM prescriptions p "
            "JOIN cases c ON c.id=p.case_id JOIN animals a ON a.id=p.animal_id LEFT JOIN users u ON u.id=p.vet_id "
            "ORDER BY p.id DESC"
        ).fetchall()
    conn.close()
    return jsonify([row_to_dict(r) for r in rows])


# ----------------------------------------------- treatment response tracking --
@app.get("/api/cases/<int:case_id>/treatment-responses")
@auth_required()
def get_treatment_responses(case_id):
    conn = get_db()
    rows = conn.execute(
        "SELECT t.*, u.full_name vet_name FROM treatment_responses t LEFT JOIN users u ON u.id=t.veterinarian_id "
        "WHERE case_id=? ORDER BY id DESC", (case_id,)
    ).fetchall()
    conn.close()
    return jsonify([row_to_dict(r) for r in rows])


@app.post("/api/cases/<int:case_id>/treatment-responses")
@auth_required(roles=["vet"])
def record_treatment_response(case_id):
    data = request.get_json(force=True) or {}
    response = data.get("response", "improved").lower()
    valid_responses = ["improved", "unchanged", "worsened", "recovered", "adverse_reaction", "treatment_discontinued", "follow_up_required"]
    if response not in valid_responses:
        return jsonify({"error": f"Invalid response. Must be one of: {', '.join(valid_responses)}"}), 400

    conn = get_db()
    case = conn.execute("SELECT * FROM cases WHERE id=?", (case_id,)).fetchone()
    if not case:
        conn.close()
        return jsonify({"error": "Case not found"}), 404

    cur = conn.execute(
        """
        INSERT INTO treatment_responses
        (case_id, animal_id, prescription_id, response, response_date,
         veterinarian_id, veterinarian_name, objective_observations, notes)
        VALUES (?,?,?,?,?,?,?,?,?)
        """,
        (case_id, case["animal_id"], data.get("prescription_id"), response,
         data.get("response_date", str(date.today())), g.user["uid"], g.user["name"],
         data.get("objective_observations"), data.get("notes"))
    )
    tr_id = cur.lastrowid

    # If recovered, advance status
    if response == "recovered":
        conn.execute("UPDATE cases SET status='RECOVERED', updated_at=datetime('now') WHERE id=?", (case_id,))
        conn.execute("UPDATE animals SET status='Healthy' WHERE id=?", (case["animal_id"],))
        conn.execute("INSERT INTO case_updates (case_id, status, note, updated_by) VALUES (?, 'RECOVERED', ?, ?)",
                     (case_id, f"Animal recovered. Observations: {data.get('objective_observations', 'Full recovery')}", g.user["name"]))
        notify(conn, case["owner_id"], f"Case {case['case_no']} marked as RECOVERED by Dr. {g.user['name']}.", "case")
    elif response == "adverse_reaction":
        conn.execute("INSERT INTO case_updates (case_id, status, note, updated_by) VALUES (?, ?, ?, ?)",
                     (case_id, case["status"], f"⚠️ ADVERSE REACTION REPORTED: {data.get('notes', 'Adverse drug reaction')}", g.user["name"]))
    else:
        conn.execute("INSERT INTO case_updates (case_id, status, note, updated_by) VALUES (?, ?, ?, ?)",
                     (case_id, case["status"], f"Treatment follow-up: status is {response}.", g.user["name"]))

    audit_log(conn, "RECORD_TREATMENT_RESPONSE", "treatment_response", tr_id,
              actor_id=g.user["uid"], actor_name=g.user["name"], actor_role=g.user["role"],
              details={"response": response, "case_no": case["case_no"]})
    conn.commit()
    tr = conn.execute("SELECT * FROM treatment_responses WHERE id=?", (tr_id,)).fetchone()
    conn.close()
    return jsonify(row_to_dict(tr)), 201


# --------------------------------------------------------- vaccinations --
@app.post("/api/vaccinations")
@auth_required(roles=["vet"])
def record_vaccination():
    data = request.get_json(force=True) or {}
    conn = get_db()
    animal = conn.execute(
        "SELECT * FROM animals WHERE animal_code=?", (data.get("animal_id"),)
    ).fetchone()
    if not animal:
        conn.close()
        return jsonify({"error": "Animal not found"}), 404

    cur = conn.execute(
        "INSERT INTO vaccinations (animal_id, vaccine, date_given, next_due_date, vet_id) VALUES (?,?,?,?,?)",
        (animal["id"], data.get("vaccine"), data.get("date_given"), data.get("next_due_date"), g.user["uid"]),
    )
    vax_id = cur.lastrowid
    audit_log(conn, "RECORD_VACCINATION", "vaccination", vax_id, actor_id=g.user["uid"],
              actor_name=g.user["name"], actor_role=g.user["role"],
              details={"animal_code": animal["animal_code"], "vaccine": data.get("vaccine")})
    conn.commit()
    conn.close()
    return jsonify({"ok": True}), 201


# -------------------------------------------------------- notifications --
@app.get("/api/notifications")
@auth_required()
def list_notifications():
    conn = get_db()
    notes = conn.execute(
        "SELECT * FROM notifications WHERE user_id=? ORDER BY id DESC LIMIT 50", (g.user["uid"],)
    ).fetchall()
    conn.close()
    return jsonify([row_to_dict(n) for n in notes])


@app.put("/api/notifications/<int:note_id>/read")
@auth_required()
def mark_notification_read(note_id):
    conn = get_db()
    conn.execute("UPDATE notifications SET is_read=1 WHERE id=? AND user_id=?", (note_id, g.user["uid"]))
    conn.commit()
    conn.close()
    return jsonify({"ok": True})


@app.post("/api/notifications")
@auth_required(roles=["owner", "vet", "govt", "lab"])
def create_notification():
    data = request.get_json(force=True) or {}
    recipient_id = data.get("recipient_id", data.get("user_id"))
    try: recipient_id = int(recipient_id)
    except (TypeError, ValueError): return jsonify({"error": "recipient_id is required"}), 400
    message = str(data.get("message") or "").strip()
    if not message: return jsonify({"error": "message is required"}), 400
    channels = data.get("channels") or ["in_app"]
    if isinstance(channels, str): channels = [channels]
    channels = [str(c).lower() for c in channels]
    if any(c not in {"in_app", "sms", "whatsapp", "push"} for c in channels):
        return jsonify({"error": "unsupported notification channel"}), 400
    conn = get_db()
    recipient = conn.execute("SELECT id,role,mobile FROM users WHERE id=?", (recipient_id,)).fetchone()
    if not recipient: conn.close(); return jsonify({"error": "recipient not found"}), 404
    # No caller may use this endpoint to escalate visibility beyond RBAC.
    if g.user["role"] == "owner" and recipient_id != g.user["uid"]:
        conn.close(); return jsonify({"error": "owners may notify only their own account"}), 403
    case_id = data.get("case_id")
    if case_id:
        case = _case_for_visit(conn, int(case_id))
        if not case or not _can_access_case(conn, case):
            conn.close(); return jsonify({"error": "not authorized for case"}), 403
        # The recipient must also have a legitimate relationship to the case;
        # a caller cannot use notifications as an information side channel.
        recipient_allowed = recipient_id == case["owner_id"]
        if recipient["role"] == "vet":
            recipient_allowed = recipient_allowed or recipient_id == case["vet_id"]
        if recipient["role"] in {"govt", "lab"}:
            recipient_allowed = True
        if not recipient_allowed:
            conn.close(); return jsonify({"error": "recipient is not authorized for this case"}), 403
    event_type = str(data.get("event_type") or "NOTIFICATION_CREATED").upper()
    if not re.match(r"^[A-Z][A-Z0-9_]{2,80}$", event_type):
        conn.close(); return jsonify({"error": "invalid event_type"}), 400
    base_idem = str(data.get("idempotency_key") or f"manual:{recipient_id}:{event_type}:{message}")
    results = []
    # In-app is durable and immediately visible.
    if "in_app" in channels:
        nid = notify(conn, recipient_id, message, str(data.get("type") or "info"), case_id=case_id, event_type=event_type, data=data.get("data"), idempotency_key=base_idem + ":in_app")
        results.append({"channel": "in_app", "status": "DELIVERED", "notification_id": nid})
    sms = SMSProvider()
    if "sms" in channels:
        sms_key = base_idem + ":sms"
        existing_sms = conn.execute("SELECT delivery_status,retry_count,error_info FROM notifications WHERE idempotency_key=?", (sms_key,)).fetchone()
        if existing_sms:
            results.append({"channel": "sms", "status": existing_sms["delivery_status"], "error": existing_sms["error_info"], "attempts": existing_sms["retry_count"], "duplicate": True})
        else:
            result = sms.send(to=recipient["mobile"], message=message, reference=sms_key)
            conn.execute("INSERT OR IGNORE INTO notifications (user_id,message,type,event_id,idempotency_key,recipient_role,channel,delivery_status,retry_count,error_info,case_id,data_json) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                         (recipient_id, message, str(data.get("type") or "info"), None, sms_key, recipient["role"], "sms", result.status, result.attempts, result.error, case_id, json.dumps(data.get("data") or {})))
            results.append({"channel": "sms", "status": result.status, "provider": result.provider, "error": result.error, "attempts": result.attempts})
    for channel in (c for c in channels if c in {"whatsapp", "push"}):
        configured = provider_health().get(channel, {}).get("configured", False)
        status = "NOT_CONFIGURED" if not configured else "UNSUPPORTED_PROVIDER_ADAPTER"
        conn.execute("INSERT OR IGNORE INTO notifications (user_id,message,type,idempotency_key,recipient_role,channel,delivery_status,error_info,case_id,data_json) VALUES (?,?,?,?,?,?,?,?,?,?)",
                     (recipient_id, message, str(data.get("type") or "info"), base_idem + ":" + channel, recipient["role"], channel, status, None if configured else f"{channel} provider is not configured", case_id, json.dumps(data.get("data") or {})))
        results.append({"channel": channel, "status": status})
    conn.commit(); conn.close()
    return jsonify({"event_type": event_type, "idempotency_key": base_idem, "deliveries": results}), 201


@app.get("/api/realtime/token")
@auth_required()
def realtime_token():
    """Mint a short-lived token for browser EventSource authentication."""
    payload = {
        "uid": g.user["uid"], "role": g.user["role"], "name": g.user["name"],
        "purpose": "realtime", "exp": datetime.utcnow() + timedelta(minutes=10),
    }
    return jsonify({"token": jwt.encode(payload, SECRET_KEY, algorithm="HS256"), "expires_in": 600})


@app.get("/api/realtime/events")
@auth_required()
def realtime_events():
    """Replay persisted events for reconnecting clients."""
    conn = get_db(); limit = min(max(int(request.args.get("limit", 100)), 1), 500)
    since = request.args.get("since")
    query = "SELECT * FROM realtime_events WHERE 1=1"; params = []
    if since:
        query += " AND event_id != ?"; params.append(since)
    query += " ORDER BY created_at DESC LIMIT ?"; params.append(limit)
    rows = [dict(r) for r in conn.execute(query, params).fetchall()]
    visible = []
    for row in rows:
        try:
            payload = json.loads(row["payload"] or "{}")
        except (TypeError, ValueError):
            payload = {}
        if payload.get("recipient_id") is not None and int(payload["recipient_id"]) != g.user["uid"]:
            continue
        if row["case_id"]:
            case = _case_for_visit(conn, row["case_id"])
            if not case or not _can_access_case(conn, case):
                continue
        elif payload.get("recipient_id") is None and g.user["role"] != "govt":
            continue
        visible.append(row)
    conn.close()
    for row in visible:
        try: row["payload"] = json.loads(row["payload"])
        except (TypeError, ValueError): pass
    return jsonify(list(reversed(visible)))


@app.get("/api/realtime/stream")
@auth_required()
def realtime_stream():
    """Server-Sent Events stream backed by database events.

    Query-string authentication is accepted only for the short-lived token
    minted by ``/api/realtime/token``; normal API tokens are not placed in the
    stream URL.

    The repository has no WebSocket server dependency. SSE provides a real
    authenticated server push channel over the existing Flask deployment; it
    is reconnectable and emits only committed events. A future WebSocket
    adapter can consume the same realtime_events table without changing APIs.
    """
    if request.args.get("access_token") and g.user.get("purpose") != "realtime":
        return jsonify({"error": "A short-lived realtime token is required"}), 401
    user = dict(g.user)
    last_seen = request.args.get("since") or request.headers.get("Last-Event-ID")

    @stream_with_context
    def generate():
        yield "retry: 3000\n\n"
        seen = set()
        resume_found = not bool(last_seen)
        # Bound the stream so workers are not held forever by abandoned tabs.
        for _ in range(60):
            conn = get_db()
            rows = conn.execute("SELECT * FROM realtime_events ORDER BY created_at ASC, event_id ASC LIMIT 500").fetchall()
            for row in rows:
                if not resume_found:
                    if row["event_id"] == last_seen:
                        resume_found = True
                    continue
                if row["event_id"] in seen: continue
                try:
                    payload = json.loads(row["payload"] or "{}")
                except (TypeError, ValueError):
                    payload = {}
                if payload.get("recipient_id") is not None and int(payload["recipient_id"]) != user["uid"]:
                    continue
                if row["case_id"]:
                    case = _case_for_visit(conn, row["case_id"])
                    if not case or not _can_access_case_for_user(conn, case, user): continue
                elif payload.get("recipient_id") is None and user["role"] != "govt":
                    continue
                seen.add(row["event_id"])
                yield f"id: {row['event_id']}\nevent: {row['event_type']}\ndata: {json.dumps({'event_id': row['event_id'], 'event_type': row['event_type'], 'timestamp': row['created_at'], 'case_id': row['case_id'], 'actor_id': row['actor_id'], 'actor_role': row['actor_role'], 'payload': payload}, ensure_ascii=False)}\n\n"
            # If the browser resumed after an event outside the bounded replay
            # window, replay the retained window once rather than stalling.
            if not resume_found:
                resume_found = True
            conn.close()
            import time as _time; _time.sleep(1)
        yield ": stream-closed\n\n"
    return Response(generate(), mimetype="text/event-stream", headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


# ------------------------------------------------------------ summaries --
@app.get("/api/owner/summary")
@auth_required(roles=["owner"])
def owner_summary():
    conn = get_db()
    uid = g.user["uid"]
    animals = conn.execute("SELECT COUNT(*) c FROM animals WHERE owner_id=?", (uid,)).fetchone()["c"]
    active = conn.execute(
        "SELECT COUNT(*) c FROM cases WHERE owner_id=? AND status NOT IN ('CLOSED','RECOVERED')", (uid,)
    ).fetchone()["c"]
    today = date.today().isoformat()
    vax_due = conn.execute(
        "SELECT COUNT(*) c FROM vaccinations v JOIN animals a ON a.id=v.animal_id "
        "WHERE a.owner_id=? AND v.next_due_date <= date('now','+30 day') AND v.next_due_date >= date('now','-30 day')",
        (uid,),
    ).fetchone()["c"]
    lab_reps = conn.execute(
        "SELECT COUNT(*) c FROM lab_reports l JOIN animals a ON a.id=l.animal_id WHERE a.owner_id=?", (uid,)
    ).fetchone()["c"]
    presc = conn.execute(
        "SELECT COUNT(*) c FROM prescriptions p JOIN animals a ON a.id=p.animal_id WHERE a.owner_id=?", (uid,)
    ).fetchone()["c"]
    conn.close()
    return jsonify({
        "animals": animals,
        "active_cases": active,
        "vaccinations_due": vax_due,
        "lab_reports": lab_reps,
        "prescriptions": presc,
    })


@app.get("/api/vet/summary")
@auth_required(roles=["vet"])
def vet_summary():
    conn = get_db()
    uid = g.user["uid"]
    new_cases = conn.execute("SELECT COUNT(*) c FROM cases WHERE status='NEW'").fetchone()["c"]
    lab_pending = conn.execute("SELECT COUNT(*) c FROM cases WHERE status='LAB PENDING'").fetchone()["c"]
    user_reports = conn.execute("SELECT COUNT(*) c FROM cases").fetchone()["c"]
    followups = conn.execute(
        "SELECT COUNT(*) c FROM cases WHERE status IN ('TREATMENT','FOLLOW-UP') AND (vet_id=? OR vet_id IS NULL)",
        (uid,),
    ).fetchone()["c"]
    vax_due = conn.execute(
        "SELECT COUNT(*) c FROM vaccinations WHERE next_due_date <= date('now','+30 day') AND next_due_date >= date('now','-30 day')"
    ).fetchone()["c"]
    conn.close()
    return jsonify({
        "new_cases": new_cases,
        "vaccinations_due": vax_due,
        "lab_pending": lab_pending,
        "user_reports": user_reports,
        "followups": followups,
    })


# ---------------------------------------------------- govt: analytics ---
@app.get("/api/govt/analytics")
@auth_required(roles=["govt", "vet"])
def govt_analytics():
    conn = get_db()
    tot_cases = conn.execute("SELECT COUNT(*) c FROM cases").fetchone()["c"]
    tot_active = conn.execute("SELECT COUNT(*) c FROM cases WHERE status NOT IN ('CLOSED','RECOVERED')").fetchone()["c"]
    tot_animals = conn.execute("SELECT COUNT(*) c FROM animals").fetchone()["c"]
    districts = conn.execute("SELECT COUNT(DISTINCT district) c FROM animals WHERE district IS NOT NULL").fetchone()["c"]

    cbd = conn.execute(
        "SELECT COALESCE(NULLIF(TRIM(district),''), 'Unknown') d, COUNT(*) c FROM animals a "
        "JOIN cases cs ON cs.animal_id=a.id GROUP BY LOWER(d) ORDER BY c DESC"
    ).fetchall()
    ds = conn.execute(
        "SELECT COALESCE(NULLIF(TRIM(disease_suspected),''), NULLIF(TRIM(diagnosis),''), 'Unspecified') d, COUNT(*) c "
        "FROM cases GROUP BY LOWER(d) ORDER BY c DESC LIMIT 6"
    ).fetchall()

    stock_rows = conn.execute("SELECT district, vaccine, doses_available FROM vaccine_stock ORDER BY district, vaccine").fetchall()
    stock_by_dist = {}
    for r in stock_rows:
        stock_by_dist.setdefault(r["district"], []).append({"vaccine": r["vaccine"], "doses": r["doses_available"]})

    conn.close()
    return jsonify({
        "totals": {"cases": tot_cases, "active": tot_active, "animals": tot_animals, "districts": districts},
        "cases_by_district": [{"label": r["d"], "value": r["c"]} for r in cbd],
        "disease_spread": [{"label": r["d"], "value": r["c"]} for r in ds],
        "vaccine_stock": stock_by_dist,
    })


@app.put("/api/govt/stock")
@auth_required(roles=["govt"])
def update_stock():
    data = request.get_json(force=True) or {}
    district = (data.get("district") or "").strip()
    vaccine = (data.get("vaccine") or "").strip()
    try:
        doses = int(data.get("doses", 0))
    except (ValueError, TypeError):
        return jsonify({"error": "doses must be an integer"}), 400
    if not district or not vaccine:
        return jsonify({"error": "district and vaccine are required"}), 400

    conn = get_db()
    conn.execute(
        "INSERT INTO vaccine_stock (district, vaccine, doses_available, updated_at) "
        "VALUES (?,?,?,datetime('now')) "
        "ON CONFLICT(district, vaccine) DO UPDATE SET doses_available=?, updated_at=datetime('now')",
        (district, vaccine, doses, doses),
    )
    audit_log(conn, "UPDATE_VACCINE_STOCK", "vaccine_stock", f"{district}:{vaccine}",
              actor_id=g.user["uid"], actor_name=g.user["name"], actor_role=g.user["role"],
              details={"district": district, "vaccine": vaccine, "doses": doses})
    conn.commit()
    conn.close()
    return jsonify({"ok": True, "district": district, "vaccine": vaccine, "doses_available": doses})


# --------------------------------------------- vaccination campaigns ---
@app.get("/api/campaigns")
@auth_required()
def list_campaigns():
    district = (request.args.get("district") or "").strip()
    status = (request.args.get("status") or "").strip().upper()
    conn = get_db()
    query = "SELECT c.*, u.full_name created_by_name FROM vaccination_campaigns c LEFT JOIN users u ON u.id=c.created_by WHERE 1=1"
    params = []
    if district:
        query += " AND LOWER(c.district) = LOWER(?)"
        params.append(district)
    if status:
        query += " AND c.status = ?"
        params.append(status)
    query += " ORDER BY c.id DESC"
    rows = conn.execute(query, params).fetchall()
    conn.close()
    return jsonify([row_to_dict(r) for r in rows])


@app.post("/api/campaigns")
@auth_required(roles=["govt"])
def create_campaign():
    data = request.get_json(force=True) or {}
    required = ["name", "district", "vaccine", "target_animals", "start_date", "end_date"]
    missing = [f for f in required if not data.get(f)]
    if missing:
        return jsonify({"error": f"Missing fields: {', '.join(missing)}"}), 400

    conn = get_db()
    dist_prefix = (data["district"][:3] or "PUN").upper()
    code = next_code(conn, "CAMP", "vaccination_campaigns", "campaign_code", pad=4, district=dist_prefix)
    cur = conn.execute(
        "INSERT INTO vaccination_campaigns (campaign_code, name, district, vaccine, target_animals, doses_administered, start_date, end_date, status, notes, created_by) "
        "VALUES (?,?,?,?,?,0,?,?,?, ?,?)",
        (code, data["name"].strip(), data["district"].strip(), data["vaccine"].strip(),
         int(data["target_animals"]), data["start_date"], data["end_date"],
         data.get("status", "PLANNED").upper(), data.get("notes"), g.user["uid"]),
    )
    conn.commit()
    row = conn.execute("SELECT * FROM vaccination_campaigns WHERE id=?", (cur.lastrowid,)).fetchone()
    conn.close()
    return jsonify(row_to_dict(row)), 201


@app.put("/api/campaigns/<int:campaign_id>")
@auth_required(roles=["govt", "vet"])
def update_campaign(campaign_id):
    data = request.get_json(force=True) or {}
    conn = get_db()
    camp = conn.execute("SELECT * FROM vaccination_campaigns WHERE id=?", (campaign_id,)).fetchone()
    if not camp:
        conn.close()
        return jsonify({"error": "Campaign not found"}), 404

    doses = data.get("doses_administered")
    new_doses = int(doses) if doses is not None else camp["doses_administered"]
    status = data.get("status", camp["status"]).upper()
    notes = data.get("notes", camp["notes"])

    conn.execute(
        "UPDATE vaccination_campaigns SET doses_administered=?, status=?, notes=?, updated_at=datetime('now') WHERE id=?",
        (new_doses, status, notes, campaign_id),
    )
    conn.commit()
    updated = conn.execute("SELECT * FROM vaccination_campaigns WHERE id=?", (campaign_id,)).fetchone()
    conn.close()
    return jsonify(row_to_dict(updated))


@app.delete("/api/campaigns/<int:campaign_id>")
@auth_required(roles=["govt"])
def delete_campaign(campaign_id):
    conn = get_db()
    camp = conn.execute("SELECT * FROM vaccination_campaigns WHERE id=?", (campaign_id,)).fetchone()
    if not camp:
        conn.close()
        return jsonify({"error": "Campaign not found"}), 404
    conn.execute("DELETE FROM vaccination_campaigns WHERE id=?", (campaign_id,))
    conn.commit()
    conn.close()
    return jsonify({"ok": True, "deleted": camp["campaign_code"]})


# --------------------------------------------------- govt: GIS / geo data --
@app.get("/api/govt/geo")
@auth_required(roles=["govt", "vet"])
def govt_geo():
    conn = get_db()
    rows = conn.execute(
        """
        SELECT COALESCE(NULLIF(TRIM(a.district),''), 'Unknown') AS district,
               COUNT(c.id) AS cases,
               SUM(CASE WHEN c.status NOT IN ('CLOSED','RECOVERED') THEN 1 ELSE 0 END) AS active,
               SUM(CASE WHEN LOWER(COALESCE(c.severity,'')) IN ('high','critical') THEN 1 ELSE 0 END) AS high_severity,
               COUNT(DISTINCT c.animal_id) AS affected_animals
        FROM animals a LEFT JOIN cases c ON c.animal_id = a.id
        GROUP BY LOWER(district)
        ORDER BY cases DESC
        """
    ).fetchall()
    animals_by_district = conn.execute(
        "SELECT COALESCE(NULLIF(TRIM(district),''), 'Unknown') d, COUNT(*) c FROM animals GROUP BY LOWER(d)"
    ).fetchall()
    pop = {r["d"].title(): r["c"] for r in animals_by_district}
    diseases_by_district = conn.execute(
        """
        SELECT COALESCE(NULLIF(TRIM(a.district),''), 'Unknown') district,
               COALESCE(NULLIF(TRIM(c.disease_suspected),''), NULLIF(TRIM(c.diagnosis),''), 'Unspecified') disease,
               COUNT(*) value
        FROM cases c JOIN animals a ON a.id=c.animal_id
        GROUP BY LOWER(district), LOWER(disease)
        """
    ).fetchall()
    conn.close()
    dmap = {}
    for r in diseases_by_district:
        dmap.setdefault(r["district"].title(), []).append({"label": r["disease"], "value": r["value"]})
    out = []
    for r in rows:
        name = r["district"].title()
        cases = r["cases"] or 0
        high = r["high_severity"] or 0
        affected = r["affected_animals"] or 0
        if high > 0 or affected >= 10:
            risk = "High Risk"
        elif affected >= 5 or cases >= 3:
            risk = "Moderate Risk"
        else:
            risk = "Low Risk"
        out.append({
            "district": name,
            "cases": cases,
            "active": r["active"] or 0,
            "high_severity": high,
            "affected_animals": affected,
            "animal_population": pop.get(name, 0),
            "risk_level": risk,
            "diseases": dmap.get(name, []),
        })
    return jsonify(out)


# --------------------------------- real spatiotemporal clustering endpoint --
@app.get("/api/govt/clusters")
@auth_required(roles=["govt", "vet"])
def govt_clusters():
    """Real spatiotemporal disease clustering via DBSCAN on actual cases in DB."""
    conn = get_db()
    cases = conn.execute(
        """
        SELECT c.id, c.case_no, c.severity, c.disease_suspected, c.diagnosis, c.created_at,
               a.district, s.collection_lat, s.collection_lng,
               v.to_lat, v.to_lng
        FROM cases c
        JOIN animals a ON a.id=c.animal_id
        LEFT JOIN samples s ON s.case_id=c.id
        LEFT JOIN case_visits v ON v.case_id=c.id
        WHERE c.status NOT IN ('CLOSED', 'RECOVERED')
        ORDER BY c.id DESC
        """
    ).fetchall()
    conn.close()

    case_items = []
    for c in cases:
        dist = c["district"] or "Pune"
        c_lat = c["to_lat"] or c["collection_lat"]
        c_lng = c["to_lng"] or c["collection_lng"]
        if not c_lat or not c_lng:
            coords = weather.get_coords_for_district(dist)
            c_lat, c_lng = coords if coords else (18.5204, 73.8567)

        case_items.append({
            "case_no": c["case_no"],
            "lat": float(c_lat),
            "lng": float(c_lng),
            "district": dist.title(),
            "disease": c["disease_suspected"] or c["diagnosis"] or "HS",
            "severity": c["severity"] or "Medium",
            "created_at": c["created_at"]
        })

    # Forward to ML backend cluster endpoint
    res, status = _ml_post("/api/cluster", {"cases": case_items, "eps_km": 45.0, "min_samples": 2})
    if status == 200:
        return jsonify(res)

    # In-process DBSCAN fallback if ML backend is offline
    if len(case_items) >= 2:
        points = [[c["lat"], c["lng"]] for c in case_items]
        coords_rad = np.radians(points)
        db = DBSCAN(eps=45.0 / 6371.0, min_samples=2, metric="haversine").fit(coords_rad)
        clusters = []
        for lab in set(db.labels_):
            if lab == -1:
                continue
            c_cases = [case_items[i] for i, l in enumerate(db.labels_) if l == lab]
            clusters.append({
                "cluster_id": f"CLUST-GEO-{lab+101}",
                "district": c_cases[0]["district"],
                "lat": round(float(np.mean([c["lat"] for c in c_cases])), 4),
                "lng": round(float(np.mean([c["lng"] for c in c_cases])), 4),
                "cases": len(c_cases),
                "diseases": list(set([c["disease"] for c in c_cases])),
                "risk_level": "High Risk" if len(c_cases) >= 3 else "Moderate Risk",
                "latest_case": max([c["created_at"] for c in c_cases])[:10],
                "method": "In-process DBSCAN"
            })
        return jsonify({"clusters": clusters, "algorithm": "DBSCAN", "eps_km": 45.0})

    return jsonify({"clusters": [], "algorithm": "DBSCAN", "eps_km": 45.0})


# ------------------------------------------------------ disease library --
DISEASES_PATH = os.path.join(os.path.dirname(__file__), "diseases.json")


@app.get("/api/diseases")
@auth_required()
def list_diseases():
    try:
        with open(DISEASES_PATH, encoding="utf-8") as f:
            diseases = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return jsonify({"error": "Disease library unavailable"}), 500
    q = (request.args.get("q") or "").strip().lower()
    if q:
        diseases = [
            d for d in diseases
            if q in d["name_en"].lower() or q in d["name_mr"]
            or any(q in a.lower() for a in d.get("aliases", []))
        ]
    return jsonify(diseases)


# ------------------------------------------------- AI early warning (govt) --
def _ml_backend_base():
    raw = (os.environ.get("SIH_ML_BACKEND") or "http://127.0.0.1:8000").strip().rstrip("/")
    if not raw.startswith("http://") and not raw.startswith("https://"):
        # Render `fromService: host` may inject a bare hostname; production is HTTPS.
        raw = "https://" + raw
    return raw


ML_BACKEND = _ml_backend_base()


def _ml_post(path, payload):
    try:
        resp = requests.post(ML_BACKEND + path, json=payload, timeout=15)
        return resp.json(), resp.status_code
    except requests.RequestException:
        return {"error": "AI service is not running. Start ml-backend (port 8000) and retry."}, 503


def district_ai_features(conn, district):
    """Build AI prediction features from REAL database rows for one district,
    incorporating real weather observations from Open-Meteo API."""
    animal_population = conn.execute(
        "SELECT COUNT(*) c FROM animals WHERE LOWER(COALESCE(NULLIF(TRIM(district),''),'unknown'))=LOWER(?)",
        (district,)).fetchone()["c"]
    affected_animals = conn.execute(
        "SELECT COUNT(DISTINCT c.animal_id) c FROM cases c JOIN animals a ON a.id=c.animal_id "
        "WHERE LOWER(COALESCE(NULLIF(TRIM(a.district),''),'unknown'))=LOWER(?)",
        (district,)).fetchone()["c"]
    new_cases = conn.execute(
        "SELECT COUNT(*) c FROM cases c JOIN animals a ON a.id=c.animal_id "
        "WHERE LOWER(COALESCE(NULLIF(TRIM(a.district),''),'unknown'))=LOWER(?) "
        "AND c.created_at >= datetime('now','-30 day')",
        (district,)).fetchone()["c"]
    previous_cases = conn.execute(
        "SELECT COUNT(*) c FROM cases c JOIN animals a ON a.id=c.animal_id "
        "WHERE LOWER(COALESCE(NULLIF(TRIM(a.district),''),'unknown'))=LOWER(?) "
        "AND c.created_at < datetime('now','-30 day')",
        (district,)).fetchone()["c"]
    vaccinated = conn.execute(
        "SELECT COUNT(DISTINCT v.animal_id) c FROM vaccinations v JOIN animals a ON a.id=v.animal_id "
        "WHERE LOWER(COALESCE(NULLIF(TRIM(a.district),''),'unknown'))=LOWER(?)",
        (district,)).fetchone()["c"]
    vaccination_coverage = round(vaccinated / animal_population, 3) if animal_population else 0.0
    growth_rate = round((new_cases - previous_cases) / max(previous_cases, 1), 3)

    # FEATURE GROUP 18: Real weather data integration
    w_info = weather.fetch_district_weather(conn, district)

    return {
        "animal_population": float(animal_population),
        "affected_animals": float(affected_animals),
        "new_cases": float(new_cases),
        "deaths": 0.0,
        "vaccination_coverage": vaccination_coverage,
        "animal_density": float(animal_population),
        "previous_cases": float(previous_cases),
        "cases_growth_rate": growth_rate,
        "temperature": float(w_info["temperature"]),
        "rainfall": float(w_info["rainfall"]),
        "humidity": float(w_info["humidity"]),
        "weather_source": w_info["source"],
        "weather_status": w_info["status"],
    }


@app.get("/api/weather/<district>")
@auth_required()
def get_weather(district):
    conn = get_db()
    w_info = weather.fetch_district_weather(conn, district)
    conn.close()
    return jsonify(w_info)


@app.get("/api/govt/ai/districts")
@auth_required(roles=["govt"])
def ai_districts():
    conn = get_db()
    rows = conn.execute(
        "SELECT DISTINCT LOWER(COALESCE(NULLIF(TRIM(district),''),'unknown')) d FROM animals").fetchall()
    conn.close()
    return jsonify([r["d"].title() for r in rows if r["d"] != "unknown"])


@app.get("/api/govt/ai/predict")
@auth_required(roles=["govt"])
def ai_predict():
    district = (request.args.get("district") or "").strip()
    disease = (request.args.get("disease") or "").strip()
    if not district or not disease:
        return jsonify({"error": "district and disease are required"}), 400
    conn = get_db()
    try:
        features = district_ai_features(conn, district)
    finally:
        conn.close()
    payload = {
        "disease": disease,
        "district": district,
        "time_range": "14",
        "animal_population": features["animal_population"],
        "affected_animals": features["affected_animals"],
        "new_cases": features["new_cases"],
        "deaths": features["deaths"],
        "vaccination_coverage": features["vaccination_coverage"],
        "temperature": features["temperature"],
        "rainfall": features["rainfall"],
        "humidity": features["humidity"],
        "animal_density": features["animal_density"],
        "previous_cases": features["previous_cases"],
        "cases_growth_rate": features["cases_growth_rate"],
    }
    result, status = _ml_post("/api/predict", payload)
    if status == 200:
        result["features_used"] = features
    return jsonify(result), status


@app.get("/api/govt/ai/outbreak")
@auth_required(roles=["govt"])
def ai_outbreak():
    district = (request.args.get("district") or "").strip()
    if not district:
        return jsonify({"error": "district is required"}), 400
    conn = get_db()
    try:
        features = district_ai_features(conn, district)
    finally:
        conn.close()
    payload = {
        "new_cases": features["new_cases"],
        "cases_growth_rate": features["cases_growth_rate"],
        "deaths": features["deaths"],
        "district": district,
    }
    result, status = _ml_post("/api/outbreak-detection", payload)
    return jsonify(result), status


@app.get("/api/govt/ai/status")
@auth_required(roles=["govt"])
def ai_status():
    try:
        resp = requests.get(ML_BACKEND + "/api/model-performance", timeout=5)
        metrics = resp.json()
        return jsonify({"online": True, **metrics})
    except (requests.RequestException, ValueError):
        return jsonify({"online": False})


# ---------------------------- animal-level AI decision support endpoint -----
@app.get("/api/animals/<int:animal_id>/ai-assessment")
@auth_required(roles=["vet", "govt", "owner"])
def get_animal_ai_assessment(animal_id):
    """Run individual animal clinical decision support engine."""
    conn = get_db()
    animal = conn.execute("SELECT * FROM animals WHERE id=?", (animal_id,)).fetchone()
    if not animal:
        conn.close()
        return jsonify({"error": "Animal not found"}), 404

    cases = conn.execute("SELECT * FROM cases WHERE animal_id=? ORDER BY id DESC", (animal_id,)).fetchall()
    vaccinations = conn.execute("SELECT * FROM vaccinations WHERE animal_id=? ORDER BY date_given DESC", (animal_id,)).fetchall()
    lab_reports = conn.execute("SELECT * FROM lab_reports WHERE animal_id=? ORDER BY id DESC", (animal_id,)).fetchall()
    reproductive_records = conn.execute("SELECT * FROM animal_reproductive_records WHERE animal_id=? ORDER BY id DESC", (animal_id,)).fetchall()
    allergies = conn.execute("SELECT * FROM animal_allergies WHERE animal_id=? ORDER BY id DESC", (animal_id,)).fetchall()
    medications = conn.execute("SELECT * FROM animal_medications WHERE animal_id=? ORDER BY id DESC", (animal_id,)).fetchall()

    assessment = animal_ai.evaluate_animal_cds(
        dict(animal), [dict(c) for c in cases], [dict(v) for v in vaccinations],
        [dict(l) for l in lab_reports], [dict(r) for r in reproductive_records],
        [dict(al) for al in allergies], [dict(m) for m in medications]
    )

    # Persist assessment record
    cur = conn.execute(
        """
        INSERT INTO ai_animal_assessments
        (animal_id, model_version, risk_score, risk_level, abnormal_findings,
         concern_categories, suggested_next_steps, follow_up_recommendations,
         explanation_factors, input_summary, confidence)
        VALUES (?,?,?,?,?,?,?,?,?,?,?)
        """,
        (animal_id, assessment["model_version"], assessment["risk_score"], assessment["risk_level"],
         json.dumps(assessment["abnormal_findings"]), json.dumps(assessment["concern_categories"]),
         json.dumps(assessment["suggested_next_steps"]), json.dumps(assessment["follow_up_recommendations"]),
         json.dumps(assessment["explanation_factors"]), json.dumps(assessment["input_summary"]),
         assessment["confidence"])
    )
    audit_log(conn, "RUN_ANIMAL_AI_ASSESSMENT", "animal", animal_id, actor_id=g.user["uid"],
              actor_name=g.user["name"], actor_role=g.user["role"],
              details={"risk_score": assessment["risk_score"], "risk_level": assessment["risk_level"]})
    conn.commit()
    conn.close()
    return jsonify(assessment)


# ----------------------------- farm-level disease intelligence & alerts -----
@app.get("/api/herds/<int:herd_id>/intelligence")
@auth_required()
def herd_intelligence(herd_id):
    conn = get_db()
    herd = conn.execute("SELECT * FROM herds WHERE id=?", (herd_id,)).fetchone()
    if not herd:
        conn.close()
        return jsonify({"error": "Herd not found"}), 404

    animals_count = conn.execute("SELECT COUNT(*) c FROM animals WHERE herd_id=?", (herd_id,)).fetchone()["c"]
    active_cases = conn.execute(
        "SELECT COUNT(*) c FROM cases WHERE herd_id=? AND status NOT IN ('CLOSED','RECOVERED')", (herd_id,)
    ).fetchone()["c"]
    new_cases = conn.execute(
        "SELECT COUNT(*) c FROM cases WHERE herd_id=? AND created_at >= datetime('now','-30 day')", (herd_id,)
    ).fetchone()["c"]
    recovered = conn.execute(
        "SELECT COUNT(*) c FROM cases WHERE herd_id=? AND status='RECOVERED'", (herd_id,)
    ).fetchone()["c"]

    diseases = conn.execute(
        """
        SELECT COALESCE(disease_suspected, diagnosis, 'Unspecified') disease, COUNT(*) c
        FROM cases WHERE herd_id=? GROUP BY LOWER(disease)
        """, (herd_id,)
    ).fetchall()

    vax_count = conn.execute(
        """
        SELECT COUNT(DISTINCT animal_id) c FROM vaccinations
        WHERE animal_id IN (SELECT id FROM animals WHERE herd_id=?)
        """, (herd_id,)
    ).fetchone()["c"]

    vax_cov = round(vax_count / animals_count, 2) if animals_count > 0 else 0.0

    # Risk calculation
    if active_cases >= 2 or (active_cases >= 1 and vax_cov < 0.5):
        risk = "High Risk"
    elif active_cases >= 1 or new_cases >= 1:
        risk = "Moderate Risk"
    else:
        risk = "Low Risk"

    alerts = conn.execute("SELECT * FROM farm_alerts WHERE herd_id=? ORDER BY id DESC", (herd_id,)).fetchall()
    conn.close()

    return jsonify({
        "herd_id": herd_id,
        "herd_code": herd["herd_code"],
        "village": herd["village"],
        "district": herd["district"],
        "total_animals": animals_count,
        "active_cases": active_cases,
        "new_cases_30d": new_cases,
        "recovered_cases": recovered,
        "vaccination_coverage": vax_cov,
        "diseases": [dict(d) for d in diseases],
        "risk_level": risk,
        "alerts": [row_to_dict(a) for a in alerts]
    })


@app.get("/api/farm-alerts")
@auth_required(roles=["vet", "govt", "owner"])
def list_farm_alerts():
    conn = get_db()
    rows = conn.execute("SELECT * FROM farm_alerts ORDER BY id DESC").fetchall()
    conn.close()
    return jsonify([row_to_dict(r) for r in rows])


@app.post("/api/farm-alerts/<int:alert_id>/acknowledge")
@auth_required(roles=["vet", "govt"])
def acknowledge_farm_alert(alert_id):
    conn = get_db()
    conn.execute(
        "UPDATE farm_alerts SET status='ACKNOWLEDGED', acknowledged_by=?, acknowledged_at=datetime('now') WHERE id=?",
        (g.user["uid"], alert_id)
    )
    audit_log(conn, "ACKNOWLEDGE_FARM_ALERT", "farm_alert", alert_id, actor_id=g.user["uid"],
              actor_name=g.user["name"], actor_role=g.user["role"])
    conn.commit()
    conn.close()
    return jsonify({"ok": True, "status": "ACKNOWLEDGED"})


@app.post("/api/farm-alerts/<int:alert_id>/resolve")
@auth_required(roles=["vet", "govt"])
def resolve_farm_alert(alert_id):
    conn = get_db()
    conn.execute(
        "UPDATE farm_alerts SET status='RESOLVED', resolved_by=?, resolved_at=datetime('now') WHERE id=?",
        (g.user["uid"], alert_id)
    )
    audit_log(conn, "RESOLVE_FARM_ALERT", "farm_alert", alert_id, actor_id=g.user["uid"],
              actor_name=g.user["name"], actor_role=g.user["role"])
    conn.commit()
    conn.close()
    return jsonify({"ok": True, "status": "RESOLVED"})


# ----------------------------- national surveillance & national alerts ------
@app.get("/api/national/surveillance")
@auth_required(roles=["govt", "vet"])
def national_surveillance():
    """Hierarchical national livestock surveillance aggregation:
    Country (India) -> States -> Districts -> Herds -> Animals."""
    conn = get_db()

    # Real data from Maharashtra in DB
    mh_animals = conn.execute("SELECT COUNT(*) c FROM animals WHERE COALESCE(state,'Maharashtra')='Maharashtra'").fetchone()["c"]
    mh_cases = conn.execute(
        "SELECT COUNT(*) c FROM cases cs JOIN animals a ON a.id=cs.animal_id WHERE COALESCE(a.state,'Maharashtra')='Maharashtra'"
    ).fetchone()["c"]
    mh_active = conn.execute(
        "SELECT COUNT(*) c FROM cases cs JOIN animals a ON a.id=cs.animal_id WHERE COALESCE(a.state,'Maharashtra')='Maharashtra' AND cs.status NOT IN ('CLOSED','RECOVERED')"
    ).fetchone()["c"]
    mh_districts = conn.execute("SELECT COUNT(DISTINCT district) c FROM animals WHERE COALESCE(state,'Maharashtra')='Maharashtra'").fetchone()["c"]

    # National state breakdown (Maharashtra active; other Indian states marked clearly as no reporting or pilot)
    states_data = [
        {
            "state": "Maharashtra",
            "reporting_status": "Active Surveillance Node",
            "animals_registered": mh_animals,
            "total_cases": mh_cases,
            "active_cases": mh_active,
            "districts_reporting": mh_districts,
            "risk_index": "Elevated (Monsoon Risk)" if mh_active >= 2 else "Normal"
        },
        {
            "state": "Gujarat",
            "reporting_status": "No active field node data",
            "animals_registered": 0,
            "total_cases": 0,
            "active_cases": 0,
            "districts_reporting": 0,
            "risk_index": "No data available"
        },
        {
            "state": "Karnataka",
            "reporting_status": "No active field node data",
            "animals_registered": 0,
            "total_cases": 0,
            "active_cases": 0,
            "districts_reporting": 0,
            "risk_index": "No data available"
        },
        {
            "state": "Madhya Pradesh",
            "reporting_status": "No active field node data",
            "animals_registered": 0,
            "total_cases": 0,
            "active_cases": 0,
            "districts_reporting": 0,
            "risk_index": "No data available"
        }
    ]

    national_alerts = conn.execute("SELECT * FROM national_alerts ORDER BY id DESC").fetchall()
    conn.close()

    return jsonify({
        "country": "India",
        "hierarchy": "Country -> State -> District -> Block -> Herd -> Animal",
        "active_states_count": 1,
        "total_national_animals": mh_animals,
        "total_national_cases": mh_cases,
        "total_national_active": mh_active,
        "states": states_data,
        "national_alerts": [row_to_dict(a) for a in national_alerts]
    })


@app.get("/api/national/alerts")
@auth_required(roles=["govt", "vet"])
def list_national_alerts():
    conn = get_db()
    rows = conn.execute("SELECT * FROM national_alerts ORDER BY id DESC").fetchall()
    conn.close()
    return jsonify([row_to_dict(r) for r in rows])


@app.post("/api/national/alerts")
@auth_required(roles=["govt"])
def create_national_alert():
    data = request.get_json(force=True) or {}
    title = (data.get("title") or "").strip()
    disease = (data.get("disease") or "").strip()
    state_name = (data.get("state") or "Maharashtra").strip()
    if not title or not disease:
        return jsonify({"error": "Title and disease are required"}), 400

    conn = get_db()
    cur = conn.execute(
        """
        INSERT INTO national_alerts
        (title, state, district, disease, alert_type, severity, affected_count, description, recommended_measures, status)
        VALUES (?,?,?,?,?,?,?,?,?,'ACTIVE')
        """,
        (title, state_name, data.get("district"), disease,
         data.get("alert_type", "OUTBREAK"), data.get("severity", "HIGH"),
         int(data.get("affected_count", 1)), data.get("description", ""),
         data.get("recommended_measures", "Enhanced ring surveillance and movement control"))
    )
    alert_id = cur.lastrowid
    audit_log(conn, "CREATE_NATIONAL_ALERT", "national_alert", alert_id,
              actor_id=g.user["uid"], actor_name=g.user["name"], actor_role=g.user["role"],
              details={"title": title, "state": state_name, "disease": disease})
    conn.commit()
    row = conn.execute("SELECT * FROM national_alerts WHERE id=?", (alert_id,)).fetchone()
    conn.close()
    return jsonify(row_to_dict(row)), 201


# ------------------------------------------------ complete audit logs -------
@app.get("/api/audit-logs")
@auth_required(roles=["govt", "vet", "lab"])
def list_audit_logs():
    conn = get_db()
    entity_type = request.args.get("entity_type")
    entity_id = request.args.get("entity_id")
    limit = min(int(request.args.get("limit", 50)), 200)

    query = "SELECT * FROM audit_events WHERE 1=1"
    params = []
    if entity_type:
        query += " AND entity_type=?"
        params.append(entity_type)
    if entity_id:
        query += " AND entity_id=?"
        params.append(str(entity_id))
    query += " ORDER BY id DESC LIMIT ?"
    params.append(limit)

    rows = conn.execute(query, params).fetchall()
    conn.close()
    return jsonify([row_to_dict(r) for r in rows])


# -------------------------------------- offline field synchronization -------
@app.post("/api/sync/queue")
@auth_required()
def sync_queue():
    """Batch synchronize field actions recorded during poor/offline connectivity with client_txn_id deduplication."""
    data = request.get_json(force=True) or {}
    items = data.get("items", [])
    if not isinstance(items, list):
        return jsonify({"error": "items must be a list of queued operations"}), 400

    conn = get_db()
    results = []
    for item in items:
        txn_id = item.get("client_txn_id")
        action = item.get("action")
        payload = item.get("payload", {})

        if not txn_id or not action:
            continue

        # Check deduplication
        existing = conn.execute("SELECT id, synced_at FROM offline_sync_log WHERE client_txn_id=?", (txn_id,)).fetchone()
        if existing:
            results.append({"client_txn_id": txn_id, "status": "already_synced", "synced_at": existing["synced_at"]})
            continue

        try:
            if action == "RECORD_TREATMENT":
                case_id = payload.get("case_id")
                response = payload.get("response", "improved")
                conn.execute(
                    "INSERT INTO treatment_responses (case_id, animal_id, response, response_date, veterinarian_id, veterinarian_name, notes) "
                    "VALUES (?, ?, ?, date('now'), ?, ?, ?)",
                    (case_id, payload.get("animal_id"), response, g.user["uid"], g.user["name"], payload.get("notes"))
                )
            elif action == "COLLECT_SAMPLE":
                case_id = payload.get("case_id")
                case = conn.execute("SELECT * FROM cases WHERE id=?", (case_id,)).fetchone()
                if case:
                    code = next_code(conn, "SMP", "samples", "sample_code")
                    token = f"sqr_{uuid.uuid4().hex}"
                    conn.execute(
                        "INSERT INTO samples (sample_code, qr_token, qr_payload, animal_id, case_id, sample_type, status, collector_id, collection_lat, collection_lng, is_manual_location, collection_notes) "
                        "VALUES (?,?,?,?,?,?,'COLLECTED',?,?,?,?,'Offline synced')",
                        (code, token, f"PASHU:SAMPLE:{token}", case["animal_id"], case_id,
                         payload.get("sample_type", "Blood Sample"), g.user["uid"],
                         payload.get("lat"), payload.get("lng"), int(payload.get("is_manual", 0)))
                    )

            conn.execute(
                "INSERT INTO offline_sync_log (client_txn_id, user_id, action, payload) VALUES (?,?,?,?)",
                (txn_id, g.user["uid"], action, json.dumps(payload))
            )
            results.append({"client_txn_id": txn_id, "status": "success"})
        except Exception as err:
            results.append({"client_txn_id": txn_id, "status": "error", "error": str(err)})

    conn.commit()
    conn.close()
    return jsonify({"synced_count": len([r for r in results if r["status"] == "success"]), "results": results})


# Register IVR routes (adds /api/ivr/* endpoints without breaking existing APIs)
if HAS_IVR:
    try:
        register_ivr_routes(app)
        print(f"✓ IVR system registered (provider={TELEPHONY_PROVIDER}, number={IVR_PHONE_NUMBER or 'NOT_CONFIGURED'})")
    except Exception as e:
        print(f"✗ Failed to register IVR routes: {e}")
        import traceback; traceback.print_exc()

# Ensure all existing /api cases include IVR source visibility (already via reported_through field)
def _health_payload():
    db_status = "ok"
    try:
        conn = get_db(); conn.execute("SELECT 1").fetchone(); conn.close()
    except Exception:
        db_status = "error"
    redis_url = os.environ.get("REDIS_URL", "").strip()
    return {"status": "ok" if db_status == "ok" else "degraded", "database": db_status,
            "redis": {"configured": bool(redis_url), "status": "not_checked" if redis_url else "not_configured"},
            "realtime": {"transport": "sse", "status": "available"},
            "ivr": HAS_IVR, "version": "1.1"}


@app.get("/api/health")
def health():
    return jsonify(_health_payload())


@app.get("/health")
def health_root():
    return jsonify(_health_payload())


@app.get("/health/integrations")
def integrations_health():
    return jsonify({"sms": provider_health()["sms"],
                    "whatsapp": provider_health()["whatsapp"],
                    "push": provider_health()["push"],
                    "map": {"provider": os.environ.get("MAP_PROVIDER", ""), "configured": bool(os.environ.get("MAP_PROVIDER"))},
                    "translation": {"provider": os.environ.get("TRANSLATION_PROVIDER", "builtin"), "configured": True},
                    "telephony": {"provider": os.environ.get("TELEPHONY_PROVIDER", "mock"), "configured": HAS_IVR},
                    "ai": {"configured": bool(os.environ.get("SIH_ML_BACKEND"))}})

@app.get("/api/ivr/info")
def ivr_info_public():
    if not HAS_IVR:
        return jsonify({"ivr": False})
    from ivr.config import get_ivr_config_summary, validate_required_config, is_provider_configured
    return jsonify({
        "ivr": True,
        "config": get_ivr_config_summary(),
        "missing_env": validate_required_config(),
        "provider_ready": is_provider_configured(),
        "reporting_channels": ["WEB", "MOBILE", "IVR"],
        "note": "IVR is an additional reporting channel; web reporting continues unchanged."
    })

if __name__ == "__main__":
    # init_db() already ran at import; this block is the local dev server only.
    try:
        from ivr.config import get_ivr_config_summary
        print("IVR Config:", get_ivr_config_summary())
    except Exception:
        pass
    _debug_env = os.environ.get("FLASK_DEBUG", "")
    # Preserve local default (debug on port 5001) but never debug in production.
    DEBUG = (_debug_env.lower() in ("1", "true", "yes")) if _debug_env else (PORT == 5001)
    print(f"Starting app on http://0.0.0.0:{PORT} (debug={DEBUG})")
    app.run(host="0.0.0.0", port=PORT, debug=DEBUG)
