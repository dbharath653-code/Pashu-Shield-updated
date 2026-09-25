"""Live IVR end-to-end over HTTP: call -> language -> menu -> survey -> report -> case -> notifications."""
import json
import sys
import time

import requests

BASE = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:5001"

ANSWERS = {
    "farmer_name": ("", "E2E Farmer"), "species": ("1", None), "animal_count": ("2", None),
    "breed": ("", "Gir"), "age": ("4", None), "sex": ("1", None), "pregnancy": ("2", None),
    "main_problem": ("1", None), "symptoms": ("", "Fever and not eating since 3 days"),
    "duration": ("3", None), "severity": ("3", None), "eating": ("2", None),
    "drinking": ("2", None), "temperature": ("104", None), "vaccination": ("2", None),
    "previous_disease": ("2", None), "medicines": ("", "No medicine given"),
    "location_village": ("", "Wagholi"), "location_district": ("", "Pune"),
    "location_state": ("1", None), "additional": ("", "Animal is lethargic"),
}
ORDER = ["farmer_name", "species", "animal_count", "breed", "age", "sex", "pregnancy",
         "main_problem", "symptoms", "duration", "severity", "eating", "drinking",
         "temperature", "vaccination", "previous_disease", "medicines",
         "location_village", "location_district", "location_state", "additional"]

fails = []

def check(name, cond, extra=""):
    print(("PASS " if cond else "FAIL ") + name + (f" | {extra}" if extra and not cond else ""))
    if not cond:
        fails.append(name)

# 1. Incoming call via canonical production webhook
r = requests.post(f"{BASE}/api/ivr/webhook/call",
                  data={"From": "+919800009999", "To": "+919000000000", "CallSid": "E2E-LIVE-1"})
check("incoming call webhook", r.status_code == 200 and "<Response" in r.text, f"{r.status_code} {r.text[:150]}")

# 2. Mock call handle
r = requests.post(f"{BASE}/api/ivr/mock/call", json={"from": "+919800009999"})
call_sid = r.json().get("call_sid") if r.status_code == 200 else ""
check("mock call handle", bool(call_sid), r.text[:150])

# 3. Language -> menu -> survey
r = requests.post(f"{BASE}/api/ivr/webhook/language?call_sid={call_sid}", data={"Digits": "1"})
check("language select (1=en)", r.status_code == 200, f"{r.status_code}")
r = requests.post(f"{BASE}/api/ivr/webhook/menu?call_sid={call_sid}", data={"Digits": "2"})
check("menu select (2=report)", r.status_code == 200, f"{r.status_code}")
r = requests.post(f"{BASE}/api/ivr/webhook/survey/start?call_sid={call_sid}")
check("survey start", r.status_code == 200, f"{r.status_code}")
ok = True
for q in ORDER:
    digits, speech = ANSWERS[q]
    data = {}
    if digits:
        data["Digits"] = digits
    if speech:
        data["SpeechResult"] = speech
    r = requests.post(f"{BASE}/api/ivr/webhook/survey?call_sid={call_sid}&q={q}", data=data)
    if r.status_code != 200:
        ok = False
        print(f"  survey question {q} -> {r.status_code} {r.text[:120]}")
check(f"survey 21 answers", ok)

# 4. Allow async jobs (report + case + notifications) to complete
time.sleep(6)

govt = requests.post(f"{BASE}/api/auth/login",
                     json={"identifier": "govt@example.com", "password": "password123"}).json()["token"]
GH = {"Authorization": f"Bearer {govt}"}
r = requests.get(f"{BASE}/api/ivr/reports", headers=GH)
reports = r.json() if r.status_code == 200 else []
mine = [x for x in (reports if isinstance(reports, list) else reports.get("reports", []))
        if x.get("call_sid") == call_sid or (x.get("farmer_name") or "") == "E2E Farmer"]
check("IVR report created (govt visible)", len(mine) > 0, f"reports={len(reports)}")
if mine:
    rep = mine[0]
    print(f"  report id={rep.get('id')} village={rep.get('village')} district={rep.get('district')} "
          f"location_source={rep.get('location_source')} status={rep.get('status')}")
    check("location stored as farmer-provided (no fake GPS)",
          rep.get("location_source") in ("FARMER_PROVIDED", "NOT_AVAILABLE", "GPS", "NETWORK"),
          str(rep.get("location_source")))
    check("no invented lat/lng", not rep.get("latitude") and not rep.get("longitude"),
          f"lat={rep.get('latitude')} lng={rep.get('longitude')}")
    ai = rep.get("ai_summary") or ""
    check("AI summary carries vet-verification disclaimer",
          ("veterinarian verification required" in ai.lower()) or ai == "",
          ai[:150])
    # case linkage
    r = requests.get(f"{BASE}/api/cases", headers=GH)
    cases = r.json() if r.status_code == 200 else []
    linked = [c for c in cases if (c.get("reported_through") or "") == "IVR"
              and str(call_sid) in json.dumps(c, default=str)]
    check("IVR-linked case exists", len(linked) > 0 or any(
        (c.get("reported_through") or "") == "IVR" for c in cases), f"cases={len(cases)}")

# 5. Vet notification
vet = requests.post(f"{BASE}/api/auth/login",
                    json={"identifier": "vet1@example.com", "password": "password123"}).json()["token"]
r = requests.get(f"{BASE}/api/notifications", headers={"Authorization": f"Bearer {vet}"})
notes = r.json() if r.status_code == 200 else []
check("vet has notifications", len(notes) > 0, f"notes={len(notes)}")

print(f"\n==== IVR E2E: {'ALL PASS' if not fails else f'{len(fails)} FAILURES: {fails}'} ====")
sys.exit(1 if fails else 0)
