"""Production verification sweep: exercises every major API area against a live backend."""
import json
import sys

import requests

BASE = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:5001"
ML = sys.argv[2] if len(sys.argv) > 2 else "http://127.0.0.1:8000"
PASS, FAIL = [], []

def check(name, cond, extra=""):
    (PASS if cond else FAIL).append(name)
    print(("PASS " if cond else "FAIL ") + name + (f" | {extra}" if extra and not cond else ""))

def login(identifier, pw="password123"):
    r = requests.post(f"{BASE}/api/auth/login", json={"identifier": identifier, "password": pw})
    return r.json().get("token") if r.status_code == 200 else None

def H(tok):
    return {"Authorization": f"Bearer {tok}", "Content-Type": "application/json"}

# --- health ---
r = requests.get(f"{BASE}/api/health")
check("backend /api/health", r.status_code == 200 and r.json().get("status") == "ok", r.text[:200])
r = requests.get(f"{ML}/health")
check("ml /health", r.status_code == 200 and r.json().get("models_loaded") is True, r.text[:200])
r = requests.get(f"{BASE}/")
check("frontend / serves index", r.status_code == 200 and "PashuMitra" in r.text, str(r.status_code))
r = requests.get(f"{BASE}/app.js")
check("frontend /app.js", r.status_code == 200 and len(r.content) > 100000, str(r.status_code))
r = requests.get(f"{BASE}/maharashtra_state.geojson")
check("frontend geojson", r.status_code == 200, str(r.status_code))
r = requests.get(f"{BASE}/api/users/me")
check("protected route w/o token -> 401", r.status_code == 401, str(r.status_code))

# --- auth all roles ---
tokens = {}
for ident, role in [("rajesh@example.com", "owner"), ("vet1@example.com", "vet"),
                    ("govt@example.com", "govt"), ("lab@example.com", "lab")]:
    t = login(ident)
    tokens[role] = t
    check(f"login {role}", bool(t))
r = requests.post(f"{BASE}/api/auth/register", json={
    "full_name": "Verify Farmer", "mobile": "9898989898", "email": "verify.farmer@example.com",
    "password": "password123", "confirm_password": "password123", "role": "owner",
    "village": "Haveli", "district": "Pune"})
check("register owner", r.status_code in (200, 201, 409), f"{r.status_code} {r.text[:150]}")
new_owner_tok = r.json().get("token") if r.status_code in (200, 201) else login("verify.farmer@example.com")
r = requests.get(f"{BASE}/api/users/me", headers=H(tokens["owner"]))
check("GET /api/users/me", r.status_code == 200 and r.json().get("role") == "owner", f"{r.status_code}")

# --- owner: herd + animal + case flow ---
r = requests.post(f"{BASE}/api/herds", headers=H(new_owner_tok or tokens["owner"]),
                  json={"village": "Haveli", "block": "Haveli", "district": "Pune"})
check("POST /api/herds", r.status_code == 201, f"{r.status_code} {r.text[:200]}")
herd_id = r.json().get("id") if r.status_code == 201 else None
r = requests.post(f"{BASE}/api/animals", headers=H(new_owner_tok or tokens["owner"]), json={
    "species": "Cattle", "breed": "Gir", "gender": "Female", "age": 4,
    "village": "Haveli", "district": "Pune", "herd_id": herd_id})
check("POST /api/animals", r.status_code == 201, f"{r.status_code} {r.text[:200]}")
animal_id = r.json().get("id") if r.status_code == 201 else 1
r = requests.get(f"{BASE}/api/animals", headers=H(tokens["owner"]))
check("GET /api/animals", r.status_code == 200 and isinstance(r.json(), list), f"{r.status_code}")
r = requests.get(f"{BASE}/api/animals/{animal_id}/qr", headers=H(tokens["owner"]))
check("GET animal QR", r.status_code == 200, f"{r.status_code} {r.text[:150]}")
r = requests.post(f"{BASE}/api/cases", headers=H(new_owner_tok or tokens["owner"]), json={
    "animal_id": animal_id, "symptoms": "Fever, loss of appetite", "severity": "High",
    "description": "Verification case"})
check("POST /api/cases (report)", r.status_code == 201, f"{r.status_code} {r.text[:200]}")
case_id = r.json().get("id") if r.status_code == 201 else None
if case_id:
    r = requests.get(f"{BASE}/api/cases/{case_id}", headers=H(tokens["vet"]))
    check("GET case detail (vet)", r.status_code == 200, f"{r.status_code}")
    r = requests.put(f"{BASE}/api/cases/{case_id}", headers=H(tokens["vet"]),
                     json={"status": "UNDER INVESTIGATION", "diagnosis": "Suspected FMD"})
    check("PUT case update (vet)", r.status_code == 200, f"{r.status_code} {r.text[:200]}")
r = requests.get(f"{BASE}/api/vet/summary", headers=H(tokens["vet"]))
check("GET /api/vet/summary", r.status_code == 200, f"{r.status_code}")
r = requests.get(f"{BASE}/api/owner/summary", headers=H(tokens["owner"]))
check("GET /api/owner/summary", r.status_code == 200, f"{r.status_code}")

# --- animal AI assessment (local engine) ---
r = requests.get(f"{BASE}/api/animals/{animal_id}/ai-assessment", headers=H(tokens["vet"]))
check("GET animal ai-assessment", r.status_code == 200, f"{r.status_code} {r.text[:200]}")

# --- lab flow ---
if case_id:
    r = requests.post(f"{BASE}/api/lab/requests", headers=H(tokens["vet"]), json={
        "case_id": case_id, "animal_id": animal_id, "sample_type": "Blood Sample",
        "test_requested": "CBC", "priority": "High"})
    check("POST /api/lab/requests", r.status_code in (200, 201), f"{r.status_code} {r.text[:200]}")
r = requests.get(f"{BASE}/api/lab/queue", headers=H(tokens["lab"]))
check("GET /api/lab/queue", r.status_code == 200, f"{r.status_code}")
r = requests.get(f"{BASE}/api/lab/summary", headers=H(tokens["lab"]))
check("GET /api/lab/summary", r.status_code == 200, f"{r.status_code}")

# --- prescriptions ---
if case_id:
    r = requests.post(f"{BASE}/api/prescriptions", headers=H(tokens["vet"]), json={
        "case_id": case_id, "animal_id": animal_id, "diagnosis": "FMD suspected",
        "medicine": "Oxytetracycline", "dosage": "10ml", "frequency": "OD", "duration": "5 days"})
    check("POST /api/prescriptions", r.status_code in (200, 201), f"{r.status_code} {r.text[:200]}")
r = requests.get(f"{BASE}/api/prescriptions", headers=H(tokens["vet"]))
check("GET /api/prescriptions", r.status_code == 200, f"{r.status_code}")

# --- vaccinations (endpoint takes animal_code in `animal_id` field, per app contract) ---
_owner_tok = new_owner_tok or tokens["owner"]
_acode = requests.get(f"{BASE}/api/animals/{animal_id}", headers=H(_owner_tok)).json().get("animal_code")
r = requests.post(f"{BASE}/api/vaccinations", headers=H(tokens["vet"]), json={
    "animal_id": _acode, "vaccine": "FMD", "dose": "1st", "date_given": "2026-09-20"})
check("POST /api/vaccinations", r.status_code in (200, 201), f"{r.status_code} {r.text[:200]}")

# --- notifications ---
r = requests.get(f"{BASE}/api/notifications", headers=H(tokens["owner"]))
check("GET /api/notifications", r.status_code == 200, f"{r.status_code} {r.text[:150]}")

# --- GIS / surveillance ---
for path in ["/api/govt/geo", "/api/govt/clusters", "/api/govt/analytics",
             "/api/national/surveillance", "/api/national/alerts", "/api/diseases"]:
    r = requests.get(f"{BASE}{path}", headers=H(tokens["govt"]))
    check(f"GET {path}", r.status_code == 200, f"{r.status_code} {r.text[:150]}")
r = requests.get(f"{BASE}/api/weather/Pune", headers=H(tokens["govt"]))
check("GET /api/weather/Pune", r.status_code == 200, f"{r.status_code} {r.text[:150]}")

# --- ML proxy through backend ---
r = requests.get(f"{BASE}/api/govt/ai/status", headers=H(tokens["govt"]))
check("GET /api/govt/ai/status online", r.status_code == 200 and r.json().get("online") is True,
      f"{r.status_code} {r.text[:200]}")
r = requests.get(f"{BASE}/api/govt/ai/predict?district=Pune&disease=FMD", headers=H(tokens["govt"]))
check("GET /api/govt/ai/predict (real ML)", r.status_code == 200 and "risk_score" in r.json(),
      f"{r.status_code} {r.text[:200]}")
r = requests.get(f"{BASE}/api/govt/ai/outbreak?district=Pune", headers=H(tokens["govt"]))
check("GET /api/govt/ai/outbreak", r.status_code == 200, f"{r.status_code} {r.text[:200]}")

# --- IVR: webhook alias + mock E2E ---
r = requests.post(f"{BASE}/api/ivr/webhook/call", data={"From": "+919800000001", "CallSid": "VERIFY-CALL-1"})
check("POST /api/ivr/webhook/call (canonical)", r.status_code == 200 and "xml" in r.headers.get("Content-Type", ""),
      f"{r.status_code} {r.text[:150]}")
r = requests.post(f"{BASE}/api/ivr/mock/call", json={"from": "+919800000001"})
ok = r.status_code == 200
call_sid = (r.json().get("call_sid") or "") if ok else ""
check("POST /api/ivr/mock/call", ok and bool(call_sid), f"{r.status_code} {r.text[:200]}")
if call_sid:
    r = requests.post(f"{BASE}/api/ivr/mock/dtmf", json={"call_sid": call_sid, "digits": "1"})
    check("IVR mock language select", r.status_code == 200, f"{r.status_code} {r.text[:200]}")
r = requests.get(f"{BASE}/api/ivr/calls", headers=H(tokens["govt"]))
check("GET /api/ivr/calls", r.status_code == 200, f"{r.status_code} {r.text[:150]}")
r = requests.get(f"{BASE}/api/ivr/reports", headers=H(tokens["govt"]))
check("GET /api/ivr/reports", r.status_code == 200, f"{r.status_code} {r.text[:150]}")
r = requests.get(f"{BASE}/api/ivr/analytics", headers=H(tokens["govt"]))
check("GET /api/ivr/analytics", r.status_code == 200, f"{r.status_code} {r.text[:150]}")

print(f"\n==== RESULT: {len(PASS)} passed, {len(FAIL)} failed ====")
if FAIL:
    print("FAILURES:", FAIL)
sys.exit(1 if FAIL else 0)
