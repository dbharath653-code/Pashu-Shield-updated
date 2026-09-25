import os
import json
import math
import jwt
import sqlite3
import requests
from datetime import datetime, timedelta, date
from functools import wraps
from flask import Flask, request, jsonify, g, send_from_directory

from database import get_db, init_db, hash_password, verify_password, next_code

SECRET_KEY = os.environ.get("SIH_SECRET_KEY", "sih-hackathon-dev-secret-change-me")
TOKEN_EXP_HOURS = 12

FRONTEND_DIR = os.path.join(os.path.dirname(__file__), "..", "frontend")
PORT = int(os.environ.get("PORT", "5001"))

app = Flask(__name__, static_folder=FRONTEND_DIR, static_url_path="")

CASE_STATUSES = ["NEW", "ASSIGNED", "UNDER INVESTIGATION", "SAMPLE COLLECTED", "LAB PENDING",
                  "DIAGNOSED", "TREATMENT", "FOLLOW-UP", "RECOVERED", "CLOSED"]


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
            if not auth.startswith("Bearer "):
                return jsonify({"error": "Missing or invalid Authorization header"}), 401
            payload = decode_token(auth.split(" ", 1)[1])
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


def notify(conn, user_id, message, type_="info"):
    conn.execute("INSERT INTO notifications (user_id, message, type) VALUES (?,?,?)", (user_id, message, type_))


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


# -------------------------------------------------------------- frontend --
@app.after_request
def no_cache_static(resp):
    # The SPA is a single app.js/app.js bundle; stale browser caches otherwise
    # hide fresh edits, so always revalidate html/js/css.
    if request.path in ("/", "/index.html") or request.path.endswith((".js", ".css")):
        resp.headers["Cache-Control"] = "no-store, must-revalidate"
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
    if data["role"] not in ("owner", "vet", "govt"):
        return jsonify({"error": "Invalid role"}), 400

    conn = get_db()
    try:
        existing = conn.execute(
            "SELECT id FROM users WHERE email=? OR mobile=?", (data["email"], data["mobile"])
        ).fetchone()
        if existing:
            return jsonify({"error": "An account with this email or mobile already exists"}), 409

        pw_hash, salt = hash_password(data["password"])
        cur = conn.execute(
            "INSERT INTO users (full_name, mobile, email, password_hash, salt, role, specialization, village, block, district, state) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (data["full_name"], data["mobile"], data["email"], pw_hash, salt, data["role"],
             data.get("specialization"), data.get("village"), data.get("block"),
             data.get("district"), data.get("state", "Maharashtra")),
        )
        conn.commit()
        user = conn.execute("SELECT * FROM users WHERE id=?", (cur.lastrowid,)).fetchone()
        token = make_token(user)
        return jsonify({"token": token, "user": public_user(user)}), 201
    except sqlite3.IntegrityError:
        return jsonify({"error": "An account with this email or mobile already exists"}), 409
    finally:
        conn.close()


def public_user(row):
    d = dict(row)
    d.pop("password_hash", None)
    d.pop("salt", None)
    return d


@app.post("/api/auth/login")
def login():
    data = request.get_json(force=True) or {}
    identifier = data.get("identifier") or data.get("email") or data.get("mobile")
    password = data.get("password")
    if not identifier or not password:
        return jsonify({"error": "Email/mobile and password are required"}), 400

    conn = get_db()
    user = conn.execute(
        "SELECT * FROM users WHERE email=? OR mobile=?", (identifier, identifier)
    ).fetchone()
    conn.close()
    if not user or not verify_password(password, user["salt"], user["password_hash"]):
        return jsonify({"error": "Invalid credentials"}), 401

    token = make_token(user)
    return jsonify({"token": token, "user": public_user(user)})


@app.get("/api/users/me")
@auth_required()
def me():
    conn = get_db()
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
        "INSERT INTO herds (herd_code, owner_id, village, block, district) VALUES (?,?,?,?,?)",
        (code, g.user["uid"], data.get("village", owner["village"]), data.get("block", owner["block"]),
         district),
    )
    conn.commit()
    herd = conn.execute("SELECT * FROM herds WHERE id=?", (cur.lastrowid,)).fetchone()
    conn.close()
    return jsonify(row_to_dict(herd)), 201


@app.get("/api/herds")
@auth_required(roles=["owner"])
def list_herds():
    conn = get_db()
    herds = conn.execute("SELECT * FROM herds WHERE owner_id=? ORDER BY id DESC", (g.user["uid"],)).fetchall()
    out = []
    for h in herds:
        hd = dict(h)
        hd["animal_count"] = conn.execute("SELECT COUNT(*) c FROM animals WHERE herd_id=?", (h["id"],)).fetchone()["c"]
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
            "INSERT INTO animals (animal_code, owner_id, herd_id, animal_name, animal_type, species, breed, gender, sex, age, age_years, owner_name, mobile, village, block, district, status) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (code, g.user["uid"], herd_id, data.get("animal_name"), animal_type, animal_type, data.get("breed"),
             data.get("gender"), data.get("gender"), age, age, owner_name, mobile,
             data.get("village", owner["village"]), data.get("block", owner["block"]), district,
             data.get("status", "Healthy")),
        )
        conn.commit()
        animal = conn.execute("SELECT * FROM animals WHERE id=?", (cur.lastrowid,)).fetchone()
        return jsonify(row_to_dict(animal)), 201
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
        conn.execute("DELETE FROM case_updates WHERE case_id IN (SELECT id FROM cases WHERE animal_id=?)", (animal_id,))
        conn.execute("DELETE FROM lab_reports WHERE animal_id=?", (animal_id,))
        conn.execute("DELETE FROM lab_requests WHERE animal_id=?", (animal_id,))
        conn.execute("DELETE FROM prescriptions WHERE animal_id=?", (animal_id,))
        conn.execute("DELETE FROM vaccinations WHERE animal_id=?", (animal_id,))
        conn.execute("DELETE FROM cases WHERE animal_id=?", (animal_id,))
        conn.execute("DELETE FROM animals WHERE id=?", (animal_id,))
        conn.commit()
        return jsonify({"ok": True, "deleted": animal["animal_code"]})
    finally:
        conn.close()


@app.get("/api/animals")
@auth_required(roles=["owner"])
def list_animals():
    conn = get_db()
    animals = conn.execute("SELECT * FROM animals WHERE owner_id=? ORDER BY id DESC", (g.user["uid"],)).fetchall()
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
    vaccinations = conn.execute("SELECT v.*, u.full_name vet_name FROM vaccinations v LEFT JOIN users u ON u.id=v.vet_id "
                                 "WHERE animal_id=? ORDER BY date_given DESC", (animal_id,)).fetchall()
    lab_reports = conn.execute("SELECT * FROM lab_reports WHERE animal_id=? ORDER BY id DESC", (animal_id,)).fetchall()
    prescriptions = conn.execute("SELECT p.*, u.full_name vet_name FROM prescriptions p LEFT JOIN users u ON u.id=p.vet_id "
                                  "WHERE animal_id=? ORDER BY id DESC", (animal_id,)).fetchall()
    herd = conn.execute("SELECT * FROM herds WHERE id=?", (animal["herd_id"],)).fetchone() if animal["herd_id"] else None

    result = row_to_dict(animal)
    result["herd"] = row_to_dict(herd)
    result["cases"] = [row_to_dict(c) for c in cases]
    result["vaccinations"] = [row_to_dict(v) for v in vaccinations]
    result["lab_reports"] = [row_to_dict(l) for l in lab_reports]
    result["prescriptions"] = [row_to_dict(p) for p in prescriptions]
    conn.close()
    return jsonify(result)


# --------------------------------------------------------------- cases ---
@app.post("/api/cases")
@auth_required(roles=["owner"])
def create_case():
    """A user reports a health issue -> creates a real case in the DB."""
    data = request.get_json(force=True) or {}
    animal_code = data.get("animal_id")
    if not animal_code or not data.get("symptoms"):
        return jsonify({"error": "Animal and symptoms are required"}), 400

    conn = get_db()
    animal = conn.execute(
        "SELECT * FROM animals WHERE animal_code=? AND owner_id=?", (animal_code, g.user["uid"])
    ).fetchone()
    if not animal:
        conn.close()
        return jsonify({"error": "Animal ID not found in your account"}), 404

    case_no = next_code(conn, "CASE", "cases", "case_no")
    cur = conn.execute(
        "INSERT INTO cases (case_no, animal_id, herd_id, owner_id, symptoms, disease_suspected, severity, description, reported_through, status) "
        "VALUES (?,?,?,?,?,?,?,?,?,'NEW')",
        (case_no, animal["id"], animal["herd_id"], g.user["uid"], data["symptoms"],
         data.get("disease_suspected"), data.get("severity", "Medium"), data.get("description"),
         data.get("reported_through", "Mobile App")),
    )
    case_id = cur.lastrowid
    conn.execute("INSERT INTO case_updates (case_id, status, note, updated_by) VALUES (?,?,?,?)",
                 (case_id, "NEW", "Case reported by animal owner", "system"))
    conn.execute("UPDATE animals SET status=? WHERE id=?", ("Under Observation", animal["id"]))

    # notify all vets (simple hackathon broadcast — in production this would be assignment-based)
    vets = conn.execute("SELECT id FROM users WHERE role='vet'").fetchall()
    for v in vets:
        notify(conn, v["id"], f"New user report received: {case_no} ({data['symptoms']})", "case")

    conn.commit()
    case = conn.execute("SELECT * FROM cases WHERE id=?", (case_id,)).fetchone()
    result = case_json(conn, case)
    conn.close()
    return jsonify(result), 201


# IVR-ready endpoint: represents the future telephony pipeline feeding the SAME case table
@app.post("/api/ivr/report")
def ivr_report():
    data = request.get_json(force=True) or {}
    required = ["mobile", "animal_id", "symptoms"]
    if any(not data.get(f) for f in required):
        return jsonify({"error": "mobile, animal_id and symptoms are required"}), 400

    conn = get_db()
    owner = conn.execute("SELECT * FROM users WHERE mobile=?", (data["mobile"],)).fetchone()
    if not owner:
        conn.close()
        return jsonify({"error": "No registered owner found for this mobile number"}), 404
    animal = conn.execute("SELECT * FROM animals WHERE animal_code=? AND owner_id=?",
                           (data["animal_id"], owner["id"])).fetchone()
    if not animal:
        conn.close()
        return jsonify({"error": "Animal ID not found for this owner"}), 404

    case_no = next_code(conn, "CASE", "cases", "case_no")
    cur = conn.execute(
        "INSERT INTO cases (case_no, animal_id, herd_id, owner_id, symptoms, disease_suspected, severity, description, reported_through, status) "
        "VALUES (?,?,?,?,?,?,?,?,?,'NEW')",
        (case_no, animal["id"], animal["herd_id"], owner["id"], data["symptoms"],
         data.get("disease_suspected"), data.get("severity", "Medium"),
         data.get("description", f"Reported via IVR call in {data.get('language','Marathi')}"), "IVR"),
    )
    conn.execute("INSERT INTO case_updates (case_id, status, note, updated_by) VALUES (?,?,?,?)",
                 (cur.lastrowid, "NEW", "Case reported via IVR", "system"))
    vets = conn.execute("SELECT id FROM users WHERE role='vet'").fetchall()
    for v in vets:
        notify(conn, v["id"], f"New IVR report received: {case_no}", "case")
    conn.commit()
    conn.close()
    return jsonify({"case_no": case_no, "status": "NEW"}), 201


@app.get("/api/cases")
@auth_required()
def list_cases():
    conn = get_db()
    if g.user["role"] == "owner":
        rows = conn.execute("SELECT * FROM cases WHERE owner_id=? ORDER BY id DESC", (g.user["uid"],)).fetchall()
    else:
        rows = conn.execute("SELECT * FROM cases ORDER BY id DESC").fetchall()
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

    result = case_json(conn, case)
    updates = conn.execute("SELECT * FROM case_updates WHERE case_id=? ORDER BY id", (case_id,)).fetchall()
    lab_requests = conn.execute("SELECT * FROM lab_requests WHERE case_id=? ORDER BY id DESC", (case_id,)).fetchall()
    lab_reports = conn.execute("SELECT * FROM lab_reports WHERE case_id=? ORDER BY id DESC", (case_id,)).fetchall()
    prescriptions = conn.execute("SELECT * FROM prescriptions WHERE case_id=? ORDER BY id DESC", (case_id,)).fetchall()
    result["updates"] = [row_to_dict(u) for u in updates]
    result["lab_requests"] = [row_to_dict(l) for l in lab_requests]
    result["lab_reports"] = [row_to_dict(l) for l in lab_reports]
    result["prescriptions"] = [row_to_dict(p) for p in prescriptions]
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

    status = data.get("status", case["status"])
    if status not in CASE_STATUSES:
        conn.close()
        return jsonify({"error": f"Invalid status. Must be one of {CASE_STATUSES}"}), 400

    diagnosis = data.get("diagnosis", case["diagnosis"])
    treatment = data.get("treatment", case["treatment"])
    conn.execute(
        "UPDATE cases SET status=?, diagnosis=?, treatment=?, vet_id=?, updated_at=datetime('now') WHERE id=?",
        (status, diagnosis, treatment, g.user["uid"], case_id),
    )
    conn.execute("INSERT INTO case_updates (case_id, status, note, updated_by) VALUES (?,?,?,?)",
                 (case_id, status, data.get("note", f"Status updated to {status}"), g.user["name"]))

    if status in ("RECOVERED", "CLOSED"):
        conn.execute("UPDATE animals SET status='Healthy' WHERE id=?", (case["animal_id"],))

    notify(conn, case["owner_id"], f"Your case {case['case_no']} status changed to {status}.", "case")
    conn.commit()
    updated = conn.execute("SELECT * FROM cases WHERE id=?", (case_id,)).fetchone()
    result = case_json(conn, updated)
    conn.close()
    return jsonify(result)


@app.delete("/api/cases/<int:case_id>")
@auth_required(roles=["vet"])
def delete_case(case_id):
    """Vet close-out: once a case is solved it can be removed from the system."""
    conn = get_db()
    try:
        case = conn.execute("SELECT * FROM cases WHERE id=?", (case_id,)).fetchone()
        if not case:
            return jsonify({"error": "Case not found"}), 404
        conn.execute("DELETE FROM case_updates WHERE case_id=?", (case_id,))
        conn.execute("DELETE FROM lab_reports WHERE case_id=?", (case_id,))
        conn.execute("DELETE FROM lab_requests WHERE case_id=?", (case_id,))
        conn.execute("DELETE FROM prescriptions WHERE case_id=?", (case_id,))
        conn.execute("DELETE FROM cases WHERE id=?", (case_id,))
        conn.commit()
        return jsonify({"ok": True, "deleted": case["case_no"]})
    finally:
        conn.close()


# ------------------------------------------- live field-visit tracking ----
# Swiggy/Zomato-style: once a vet accepts a report and heads to the farm, the
# owner watches the vet move on a map in real time. GPS is simulated on the
# server by interpolating between the vet's and the owner's district anchors
# over `travel_seconds`, so no background thread or device GPS is required.
DISTRICT_COORDS = {
    "pune": (18.5204, 73.8567), "satara": (17.6805, 74.0183),
    "aurangabad": (19.8762, 75.3433), "nagpur": (21.1458, 79.0882),
    "nashik": (20.0110, 73.7903), "nanded": (19.1383, 77.3210),
    "latur": (18.4088, 76.5604), "solapur": (17.6599, 75.9064),
    "kolhapur": (16.7050, 74.2433), "ahmednagar": (19.0952, 74.7496),
}
VISIT_ACTIVE = ("ON_THE_WAY", "ARRIVED")


def _anchor(district, seed):
    """Deterministic lat/lng for a district, jittered by seed so two cases in
    the same district don't stack on one point."""
    base = DISTRICT_COORDS.get((district or "").strip().lower(), (19.7515, 75.7139))
    j = (seed * 37) % 100 / 9000.0
    k = (seed * 53) % 100 / 9000.0
    return (round(base[0] + j, 5), round(base[1] + k, 5))


def _visit_state(visit):
    """Progress 0..1 plus the vet's current interpolated position and ETA."""
    started = datetime.strptime(visit["started_at"], "%Y-%m-%d %H:%M:%S")
    elapsed = (datetime.utcnow() - started).total_seconds()
    travel = max(10, visit["travel_seconds"] or 240)
    if visit["status"] in ("ARRIVED", "COMPLETED"):
        p = 1.0
    elif visit["status"] == "CANCELLED":
        p = 0.0
    else:
        p = max(0.0, min(1.0, elapsed / travel))
    # slight perpendicular bow so the route reads as a road, not a straight line
    bow = math.sin(p * math.pi) * 0.06
    lat = visit["from_lat"] + (visit["to_lat"] - visit["from_lat"]) * p + bow * 0.4
    lng = visit["from_lng"] + (visit["to_lng"] - visit["from_lng"]) * p + bow
    eta = 0 if p >= 1 else int(travel - elapsed)
    return {"progress": round(p, 3), "vet_lat": round(lat, 5), "vet_lng": round(lng, 5),
            "eta_seconds": max(0, eta)}


@app.post("/api/cases/<int:case_id>/visit")
@auth_required(roles=["vet"])
def start_visit(case_id):
    """Vet accepts the report and starts travelling to the owner's farm."""
    conn = get_db()
    try:
        case = conn.execute("SELECT * FROM cases WHERE id=?", (case_id,)).fetchone()
        if not case:
            return jsonify({"error": "Case not found"}), 404
        active = conn.execute(
            "SELECT id FROM case_visits WHERE case_id=? AND status IN (?,?)",
            (case_id, *VISIT_ACTIVE)).fetchone()
        if active:
            return jsonify({"error": "A visit is already in progress for this case"}), 409
        vet = conn.execute("SELECT * FROM users WHERE id=?", (g.user["uid"],)).fetchone()
        owner = conn.execute("SELECT * FROM users WHERE id=?", (case["owner_id"],)).fetchone()
        frm = _anchor(vet["district"], case_id)
        to = _anchor(owner["district"], case_id + 7)
        if abs(frm[0] - to[0]) < 0.02 and abs(frm[1] - to[1]) < 0.02:
            frm = (round(to[0] + 0.18, 5), round(to[1] + 0.14, 5))
        travel = int((request.get_json(silent=True) or {}).get("travel_seconds", 240) or 240)
        cur = conn.execute(
            "INSERT INTO case_visits (case_id, vet_id, status, from_lat, from_lng, to_lat, to_lng, travel_seconds)"
            " VALUES (?,?,?,?,?,?,?,?)",
            (case_id, vet["id"], "ON_THE_WAY", frm[0], frm[1], to[0], to[1], travel))
        conn.execute("UPDATE cases SET vet_id=?, status='ASSIGNED', updated_at=datetime('now') WHERE id=?",
                     (vet["id"], case_id))
        conn.execute("INSERT INTO case_updates (case_id, status, note, updated_by)"
                     " VALUES (?,?,?,?)",
                     (case_id, "ASSIGNED", f"Vet {vet['full_name']} accepted & is on the way", g.user["name"]))
        conn.execute("INSERT INTO notifications (user_id, type, message) VALUES (?,?,?)",
                     (owner["id"], "visit", f"{vet['full_name']} is on the way to your farm!"))
        conn.commit()
        visit = conn.execute("SELECT * FROM case_visits WHERE id=?", (cur.lastrowid,)).fetchone()
        return jsonify({"visit": dict(visit), **_visit_state(visit)}), 201
    finally:
        conn.close()


@app.put("/api/cases/<int:case_id>/visit")
@auth_required(roles=["vet"])
def update_visit(case_id):
    """Vet marks the visit ARRIVED / COMPLETED / CANCELLED."""
    status = (request.get_json(silent=True) or {}).get("status", "").upper()
    if status not in ("ARRIVED", "COMPLETED", "CANCELLED"):
        return jsonify({"error": "status must be ARRIVED, COMPLETED or CANCELLED"}), 400
    conn = get_db()
    try:
        visit = conn.execute(
            "SELECT * FROM case_visits WHERE case_id=? AND status IN (?,?) ORDER BY id DESC LIMIT 1",
            (case_id, *VISIT_ACTIVE)).fetchone()
        if not visit:
            return jsonify({"error": "No active visit for this case"}), 404
        col = {"ARRIVED": "arrived_at", "COMPLETED": "completed_at"}.get(status)
        if col:
            conn.execute(f"UPDATE case_visits SET status=?, {col}=datetime('now') WHERE id=?", (status, visit["id"]))
        else:
            conn.execute("UPDATE case_visits SET status=? WHERE id=?", (status, visit["id"]))
        note = {"ARRIVED": "Vet arrived at the farm", "COMPLETED": "Field visit completed",
                "CANCELLED": "Field visit cancelled"}[status]
        conn.execute("INSERT INTO case_updates (case_id, status, note, updated_by) VALUES (?,?,?,?)",
                     (case_id, status, note, g.user["name"]))
        if status == "COMPLETED":
            conn.execute("UPDATE cases SET status='TREATMENT', updated_at=datetime('now') WHERE id=?", (case_id,))
        conn.commit()
        visit = conn.execute("SELECT * FROM case_visits WHERE id=?", (visit["id"],)).fetchone()
        return jsonify({"visit": dict(visit), **_visit_state(visit)})
    finally:
        conn.close()


@app.get("/api/cases/<int:case_id>/track")
@auth_required()
def track_visit(case_id):
    """Live position + ETA of the vet en route. Owner sees only their own case."""
    conn = get_db()
    try:
        case = conn.execute("SELECT * FROM cases WHERE id=?", (case_id,)).fetchone()
        if not case:
            return jsonify({"error": "Case not found"}), 404
        if g.user["role"] == "owner" and case["owner_id"] != g.user["uid"]:
            return jsonify({"error": "Not your case"}), 403
        visit = conn.execute(
            "SELECT * FROM case_visits WHERE case_id=? ORDER BY id DESC LIMIT 1", (case_id,)).fetchone()
        if not visit:
            return jsonify({"visit": None})
        vet = conn.execute("SELECT * FROM users WHERE id=?", (visit["vet_id"],)).fetchone()
        owner = conn.execute("SELECT * FROM users WHERE id=?", (case["owner_id"],)).fetchone()
        return jsonify({
            "visit": dict(visit), **_visit_state(visit),
            "vet": {"name": vet["full_name"], "mobile": vet["mobile"], "district": vet["district"]},
            "owner": {"name": owner["full_name"], "mobile": owner["mobile"],
                      "address": f"{owner['village']}, {owner['district']}"},
            "from": {"lat": visit["from_lat"], "lng": visit["from_lng"]},
            "to": {"lat": visit["to_lat"], "lng": visit["to_lng"]},
        })
    finally:
        conn.close()


# --------------------------------------------------------- vet: reports --
@app.get("/api/vet/reports")
@auth_required(roles=["vet"])
def vet_reports():
    """All user-submitted reports/cases, newest first — this is the critical
    user -> veterinary connection required by the spec."""
    conn = get_db()
    rows = conn.execute("SELECT * FROM cases ORDER BY id DESC").fetchall()
    out = [case_json(conn, r) for r in rows]
    conn.close()
    return jsonify(out)


@app.get("/api/vets")
@auth_required()
def list_vets():
    """Public contact list of veterinarians so owners (including those who
    cannot type) can tap-to-call a doctor; the call is handled as IVR."""
    conn = get_db()
    rows = conn.execute(
        "SELECT id, full_name, mobile, specialization, district FROM users WHERE role='vet' ORDER BY id"
    ).fetchall()
    conn.close()
    return jsonify([row_to_dict(r) for r in rows])


@app.get("/api/vet/search")
@auth_required(roles=["vet"])
def vet_search():
    q = request.args.get("q", "").strip()
    conn = get_db()
    animals = conn.execute(
        "SELECT * FROM animals WHERE animal_code LIKE ? OR species LIKE ?", (f"%{q}%", f"%{q}%")
    ).fetchall()
    herds = conn.execute("SELECT * FROM herds WHERE herd_code LIKE ?", (f"%{q}%",)).fetchall()
    conn.close()
    return jsonify({"animals": [row_to_dict(a) for a in animals], "herds": [row_to_dict(h) for h in herds]})


# ------------------------------------------------------------ lab tests --
@app.post("/api/lab/requests")
@auth_required(roles=["vet"])
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
    if g.user["role"] == "owner":
        rows = conn.execute(
            "SELECT l.*, a.animal_code, a.animal_name, c.case_no FROM lab_reports l "
            "JOIN animals a ON a.id=l.animal_id JOIN cases c ON c.id=l.case_id "
            "WHERE a.owner_id=? ORDER BY l.id DESC", (g.user["uid"],)
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT l.*, a.animal_code, a.animal_name, c.case_no FROM lab_reports l "
            "JOIN animals a ON a.id=l.animal_id JOIN cases c ON c.id=l.case_id "
            "ORDER BY l.id DESC"
        ).fetchall()
    conn.close()
    return jsonify([row_to_dict(r) for r in rows])


@app.post("/api/lab/reports")
@auth_required(roles=["vet"])
def create_lab_report():
    data = request.get_json(force=True) or {}
    conn = get_db()
    case = conn.execute("SELECT * FROM cases WHERE id=?", (data.get("case_id"),)).fetchone()
    if not case:
        conn.close()
        return jsonify({"error": "Invalid case ID"}), 404

    report_no = next_code(conn, "LAB", "lab_reports", "report_no")
    cur = conn.execute(
        "INSERT INTO lab_reports (report_no, lab_request_id, case_id, animal_id, herd_id, sample, test_name, result, test_date, notes, entered_by) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
        (report_no, data.get("lab_request_id"), case["id"], case["animal_id"], case["herd_id"],
         data.get("sample"), data.get("test_name"), data.get("result"),
         data.get("test_date", str(date.today())), data.get("notes"), g.user["uid"]),
    )
    if data.get("lab_request_id"):
        conn.execute("UPDATE lab_requests SET status='REPORT READY' WHERE id=?", (data["lab_request_id"],))
    conn.execute("INSERT INTO case_updates (case_id, status, note, updated_by) VALUES (?,?,?,?)",
                 (case["id"], case["status"], f"Lab report {report_no} entered: {data.get('test_name')} = {data.get('result')}", g.user["name"]))
    notify(conn, case["owner_id"], f"Your lab report {report_no} is ready for case {case['case_no']}.", "lab")
    conn.commit()
    rep = conn.execute("SELECT * FROM lab_reports WHERE id=?", (cur.lastrowid,)).fetchone()
    conn.close()
    return jsonify(row_to_dict(rep)), 201


# --------------------------------------------------------- prescriptions --
@app.post("/api/prescriptions")
@auth_required(roles=["vet"])
def create_prescription():
    data = request.get_json(force=True) or {}
    conn = get_db()
    case = conn.execute("SELECT * FROM cases WHERE id=?", (data.get("case_id"),)).fetchone()
    if not case:
        conn.close()
        return jsonify({"error": "Invalid case ID"}), 404
    cur = conn.execute(
        "INSERT INTO prescriptions (case_id, animal_id, herd_id, diagnosis, medicine, dosage, frequency, duration, instructions, follow_up_date, vet_id) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
        (case["id"], case["animal_id"], case["herd_id"], data.get("diagnosis"), data.get("medicine"),
         data.get("dosage"), data.get("frequency"), data.get("duration"), data.get("instructions"),
         data.get("follow_up_date"), g.user["uid"]),
    )
    conn.execute("UPDATE cases SET status='TREATMENT', diagnosis=COALESCE(?, diagnosis), updated_at=datetime('now') WHERE id=?",
                 (data.get("diagnosis"), case["id"]))
    conn.execute("INSERT INTO case_updates (case_id, status, note, updated_by) VALUES (?,?,?,?)",
                 (case["id"], "TREATMENT", f"E-prescription issued: {data.get('medicine')}", g.user["name"]))
    notify(conn, case["owner_id"], f"An e-prescription is available for case {case['case_no']}.", "prescription")
    conn.commit()
    presc = conn.execute("SELECT * FROM prescriptions WHERE id=?", (cur.lastrowid,)).fetchone()
    conn.close()
    return jsonify(row_to_dict(presc)), 201


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


# --------------------------------------------------------- vaccinations --
@app.post("/api/vaccinations")
@auth_required(roles=["vet"])
def create_vaccination():
    data = request.get_json(force=True) or {}
    animal_code = data.get("animal_id")
    conn = get_db()
    animal = conn.execute("SELECT * FROM animals WHERE animal_code=?", (animal_code,)).fetchone()
    if not animal:
        conn.close()
        return jsonify({"error": "Animal ID not found"}), 404
    cur = conn.execute(
        "INSERT INTO vaccinations (animal_id, vaccine, date_given, next_due_date, vet_id) VALUES (?,?,?,?,?)",
        (animal["id"], data.get("vaccine"), data.get("date_given", str(date.today())),
         data.get("next_due_date"), g.user["uid"]),
    )
    notify(conn, animal["owner_id"], f"Vaccination ({data.get('vaccine')}) recorded for {animal['animal_code']}.", "vaccination")
    conn.commit()
    vac = conn.execute("SELECT * FROM vaccinations WHERE id=?", (cur.lastrowid,)).fetchone()
    conn.close()
    return jsonify(row_to_dict(vac)), 201


# -------------------------------------------------------- notifications --
@app.get("/api/notifications")
@auth_required()
def list_notifications():
    conn = get_db()
    rows = conn.execute("SELECT * FROM notifications WHERE user_id=? ORDER BY id DESC LIMIT 50", (g.user["uid"],)).fetchall()
    conn.close()
    return jsonify([row_to_dict(r) for r in rows])


@app.put("/api/notifications/<int:note_id>/read")
@auth_required()
def mark_read(note_id):
    conn = get_db()
    conn.execute("UPDATE notifications SET is_read=1 WHERE id=? AND user_id=?", (note_id, g.user["uid"]))
    conn.commit()
    conn.close()
    return jsonify({"ok": True})


# --------------------------------------------------------- owner summary --
@app.get("/api/owner/summary")
@auth_required(roles=["owner"])
def owner_summary():
    conn = get_db()
    uid = g.user["uid"]
    animals = conn.execute("SELECT COUNT(*) c FROM animals WHERE owner_id=?", (uid,)).fetchone()["c"]
    active_cases = conn.execute(
        "SELECT COUNT(*) c FROM cases WHERE owner_id=? AND status NOT IN ('CLOSED','RECOVERED')", (uid,)
    ).fetchone()["c"]
    vaccinations_due = conn.execute(
        "SELECT COUNT(*) c FROM vaccinations v JOIN animals a ON a.id=v.animal_id "
        "WHERE a.owner_id=? AND v.next_due_date <= date('now', '+30 day')", (uid,)
    ).fetchone()["c"]
    lab_reports = conn.execute(
        "SELECT COUNT(*) c FROM lab_reports l JOIN animals a ON a.id=l.animal_id WHERE a.owner_id=?", (uid,)
    ).fetchone()["c"]
    prescriptions = conn.execute(
        "SELECT COUNT(*) c FROM prescriptions p JOIN animals a ON a.id=p.animal_id WHERE a.owner_id=?", (uid,)
    ).fetchone()["c"]
    conn.close()
    return jsonify({
        "animals": animals, "active_cases": active_cases, "vaccinations_due": vaccinations_due,
        "lab_reports": lab_reports, "prescriptions": prescriptions,
    })


# ----------------------------------------------------------- vet summary --
@app.get("/api/vet/summary")
@auth_required(roles=["vet"])
def vet_summary():
    conn = get_db()
    new_cases = conn.execute("SELECT COUNT(*) c FROM cases WHERE status='NEW'").fetchone()["c"]
    vacc_due = conn.execute("SELECT COUNT(*) c FROM vaccinations WHERE next_due_date <= date('now', '+30 day')").fetchone()["c"]
    lab_pending = conn.execute("SELECT COUNT(*) c FROM lab_requests WHERE status NOT IN ('REPORT READY')").fetchone()["c"]
    user_reports = conn.execute("SELECT COUNT(*) c FROM cases").fetchone()["c"]
    followups = conn.execute("SELECT COUNT(*) c FROM cases WHERE status='FOLLOW-UP'").fetchone()["c"]
    conn.close()
    return jsonify({
        "new_cases": new_cases, "vaccinations_due": vacc_due, "lab_pending": lab_pending,
        "user_reports": user_reports, "followups": followups,
    })


# ------------------------------------------------- government analytics --
@app.get("/api/govt/analytics")
@auth_required(roles=["govt"])
def govt_analytics():
    """State-level analytics: case volume, disease spread per district and
    vaccine stock — all aggregated live from the same case/animal tables."""
    conn = get_db()
    cases_by_status = [dict(r) for r in conn.execute(
        "SELECT status label, COUNT(*) value FROM cases GROUP BY status ORDER BY value DESC").fetchall()]
    cases_by_district = [dict(r) for r in conn.execute(
        "SELECT COALESCE(NULLIF(TRIM(a.district),''), 'Unknown') label, COUNT(*) value "
        "FROM cases c JOIN animals a ON a.id=c.animal_id "
        "GROUP BY LOWER(label) ORDER BY value DESC").fetchall()]
    disease_spread = [dict(r) for r in conn.execute(
        "SELECT COALESCE(NULLIF(TRIM(disease_suspected),''), NULLIF(TRIM(diagnosis),''), 'Unspecified') label, "
        "COUNT(*) value FROM cases GROUP BY LOWER(label) ORDER BY value DESC").fetchall()]
    disease_by_district = [dict(r) for r in conn.execute(
        "SELECT COALESCE(NULLIF(TRIM(a.district),''), 'Unknown') district, "
        "COALESCE(NULLIF(TRIM(c.disease_suspected),''), NULLIF(TRIM(c.diagnosis),''), 'Unspecified') disease, "
        "COUNT(*) value FROM cases c JOIN animals a ON a.id=c.animal_id "
        "GROUP BY LOWER(district), LOWER(disease) ORDER BY value DESC").fetchall()]
    stock = [dict(r) for r in conn.execute(
        "SELECT district, vaccine, doses_available, updated_at FROM vaccine_stock ORDER BY district, vaccine").fetchall()]
    totals = dict(conn.execute(
        "SELECT (SELECT COUNT(*) FROM cases) cases, "
        "(SELECT COUNT(*) FROM cases WHERE status NOT IN ('CLOSED','RECOVERED')) active, "
        "(SELECT COUNT(*) FROM animals) animals, "
        "(SELECT COUNT(DISTINCT district) FROM animals) districts").fetchone())
    conn.close()
    for row in cases_by_district:
        row["label"] = row["label"].title()
    for row in disease_by_district:
        row["district"] = row["district"].title()
    return jsonify({
        "cases_by_status": cases_by_status,
        "cases_by_district": cases_by_district,
        "disease_spread": disease_spread,
        "disease_by_district": disease_by_district,
        "stock": stock,
        "totals": totals,
    })


@app.put("/api/govt/stock")
@auth_required(roles=["govt"])
def update_stock():
    data = request.get_json(force=True) or {}
    district = (data.get("district") or "").strip()
    vaccine = (data.get("vaccine") or "").strip()
    doses = data.get("doses_available")
    if not district or not vaccine or doses is None:
        return jsonify({"error": "district, vaccine and doses_available are required"}), 400
    conn = get_db()
    try:
        conn.execute(
            "INSERT INTO vaccine_stock (district, vaccine, doses_available) VALUES (?,?,?) "
            "ON CONFLICT(district, vaccine) DO UPDATE SET doses_available=excluded.doses_available, updated_at=datetime('now')",
            (district, vaccine, int(doses)),
        )
        conn.commit()
        row = conn.execute(
            "SELECT district, vaccine, doses_available, updated_at FROM vaccine_stock WHERE district=? AND vaccine=?",
            (district, vaccine),
        ).fetchone()
        return jsonify(row_to_dict(row))
    finally:
        conn.close()


# ------------------------------------------------- vaccination campaigns --
CAMPAIGN_STATUSES = ["PLANNED", "ACTIVE", "PAUSED", "COMPLETED", "CANCELLED"]


@app.get("/api/campaigns")
@auth_required()
def list_campaigns():
    """Vaccination campaigns. Owners only see campaigns in their own district;
    vets and govt officers see all of them."""
    conn = get_db()
    if g.user["role"] == "owner":
        user = conn.execute("SELECT district FROM users WHERE id=?", (g.user["uid"],)).fetchone()
        rows = conn.execute(
            "SELECT * FROM vaccination_campaigns WHERE LOWER(COALESCE(district,''))=LOWER(?) ORDER BY id DESC",
            ((user["district"] or ""),),
        ).fetchall()
    else:
        rows = conn.execute("SELECT * FROM vaccination_campaigns ORDER BY id DESC").fetchall()
    conn.close()
    return jsonify([row_to_dict(r) for r in rows])


@app.post("/api/campaigns")
@auth_required(roles=["vet", "govt"])
def create_campaign():
    data = request.get_json(force=True) or {}
    name = (data.get("name") or "").strip()
    vaccine = (data.get("vaccine") or "").strip()
    if not name or not vaccine:
        return jsonify({"error": "Campaign name and vaccine are required"}), 400
    status = data.get("status", "PLANNED")
    if status not in CAMPAIGN_STATUSES:
        return jsonify({"error": f"Invalid status. Must be one of {CAMPAIGN_STATUSES}"}), 400
    conn = get_db()
    try:
        district = (data.get("district") or "").strip()
        n = conn.execute("SELECT COUNT(*) c FROM vaccination_campaigns").fetchone()["c"] + 1
        code = f"CAMP-MH-{(district[:3] or 'GEN').upper()}-{1000 + n}"
        cur = conn.execute(
            "INSERT INTO vaccination_campaigns (campaign_code, name, district, vaccine, target_animals, "
            "doses_administered, start_date, end_date, status, notes, created_by) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (code, name, district, vaccine, int(data.get("target_animals") or 0), 0,
             data.get("start_date"), data.get("end_date"), status, data.get("notes"), g.user["uid"]),
        )
        conn.commit()
        camp = conn.execute("SELECT * FROM vaccination_campaigns WHERE id=?", (cur.lastrowid,)).fetchone()
        return jsonify(row_to_dict(camp)), 201
    except (ValueError, sqlite3.IntegrityError):
        conn.rollback()
        return jsonify({"error": "Could not save this campaign. Please check the details."}), 400
    finally:
        conn.close()


@app.put("/api/campaigns/<int:campaign_id>")
@auth_required(roles=["vet", "govt"])
def update_campaign(campaign_id):
    data = request.get_json(force=True) or {}
    conn = get_db()
    camp = conn.execute("SELECT * FROM vaccination_campaigns WHERE id=?", (campaign_id,)).fetchone()
    if not camp:
        conn.close()
        return jsonify({"error": "Campaign not found"}), 404
    status = data.get("status", camp["status"])
    if status not in CAMPAIGN_STATUSES:
        conn.close()
        return jsonify({"error": f"Invalid status. Must be one of {CAMPAIGN_STATUSES}"}), 400
    try:
        doses = int(data.get("doses_administered", camp["doses_administered"]))
        target = int(data.get("target_animals", camp["target_animals"]))
    except (TypeError, ValueError):
        conn.close()
        return jsonify({"error": "doses_administered and target_animals must be numbers"}), 400
    if doses < 0 or target < 0:
        conn.close()
        return jsonify({"error": "Numbers cannot be negative"}), 400
    conn.execute(
        "UPDATE vaccination_campaigns SET name=?, district=?, vaccine=?, target_animals=?, doses_administered=?, "
        "start_date=?, end_date=?, status=?, notes=?, updated_at=datetime('now') WHERE id=?",
        (data.get("name", camp["name"]), data.get("district", camp["district"]),
         data.get("vaccine", camp["vaccine"]), target, doses,
         data.get("start_date", camp["start_date"]), data.get("end_date", camp["end_date"]),
         status, data.get("notes", camp["notes"]), campaign_id),
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
    """Per-district aggregates computed live from the real cases/animals tables,
    shaped for the GIS Risk Map (ported from LivestockHealth's GisRiskMap)."""
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
        # Risk classification (ported thresholds from GisRiskMap.tsx)
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


# ------------------------------------------------------ disease library --
DISEASES_PATH = os.path.join(os.path.dirname(__file__), "diseases.json")


@app.get("/api/diseases")
@auth_required()
def list_diseases():
    """Bilingual (EN/Marathi) disease reference library — ported from the
    LivestockHealth app's DiseaseService so every role can look up symptoms,
    transmission and prevention for common livestock diseases."""
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
ML_BACKEND = os.environ.get("SIH_ML_BACKEND", "http://127.0.0.1:8000")
# State-level climate defaults; the DB does not track weather readings.
CLIMATE_DEFAULTS = {"temperature": 30.0, "rainfall": 50.0, "humidity": 65.0}


def _ml_post(path, payload):
    try:
        resp = requests.post(ML_BACKEND + path, json=payload, timeout=15)
        return resp.json(), resp.status_code
    except requests.RequestException:
        return {"error": "AI service is not running. Start ml-backend (port 8000) and retry."}, 503


def district_ai_features(conn, district):
    """Build AI prediction features from REAL database rows for one district."""
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
    return {
        "animal_population": float(animal_population),
        "affected_animals": float(affected_animals),
        "new_cases": float(new_cases),
        "deaths": 0.0,
        "vaccination_coverage": vaccination_coverage,
        "animal_density": float(animal_population),
        "previous_cases": float(previous_cases),
        "cases_growth_rate": growth_rate,
        **CLIMATE_DEFAULTS,
    }


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
    """Risk prediction for a district+disease using features computed live
    from the cases/animals/vaccinations tables, scored by the trained
    Random Forest model in ml-backend."""
    district = (request.args.get("district") or "").strip()
    disease = (request.args.get("disease") or "").strip()
    if not district or not disease:
        return jsonify({"error": "district and disease are required"}), 400
    conn = get_db()
    try:
        features = district_ai_features(conn, district)
    finally:
        conn.close()
    payload = {"disease": disease, "district": district, "time_range": "14", **features}
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


if __name__ == "__main__":
    init_db()
    print("Database ready at", os.path.join(os.path.dirname(__file__), "animal_health.db"))
    print(f"Starting app on http://0.0.0.0:{PORT}")
    app.run(host="0.0.0.0", port=PORT, debug=True)
