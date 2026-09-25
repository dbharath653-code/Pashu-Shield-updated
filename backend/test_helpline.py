"""
Helpline acceptance tests: fixed number 7382210251, click-to-call config,
farmer identification, language/region routing, regional vet routing,
no-vet survey fallback, automatic HELPLINE reporting, notifications,
government visibility, GIS, duplicates, partial preservation.
"""
import json
import random
import unittest
import uuid
from unittest import mock

import database
from app import app, make_token


def fresh_phone():
    """Unique caller number per run (never collides with seeds or reruns)."""
    return f"+91981{random.randint(1000000, 9999999)}"


def fresh_sid(prefix="HL"):
    return f"{prefix}-{uuid.uuid4().hex[:10].upper()}"


ANSWERS = {
    "farmer_name": ("", "Helpline Farmer"),
    "species": ("1", None),
    "animal_count": ("2", None),
    "breed": ("", "Gir"),
    "age": ("4", None),
    "sex": ("1", None),
    "pregnancy": ("2", None),
    "main_problem": ("1", None),
    "symptoms": ("", "Fever and not eating"),
    "duration": ("3", None),
    "severity": ("3", None),
    "eating": ("2", None),
    "drinking": ("2", None),
    "temperature": ("104", None),
    "vaccination": ("2", None),
    "previous_disease": ("2", None),
    "medicines": ("", "None"),
    "location_village": ("", "Wagholi"),
    "location_district": ("", "Pune"),
    "location_state": ("1", None),
    "additional": ("", "Lethargic"),
}
ORDER = list(ANSWERS.keys())


class TestHelpline(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        database.init_db()
        cls.client = app.test_client()
        conn = database.get_db()
        # Test hygiene: suites that bridge vets without ending calls leave open
        # participant/session rows that would pin seed vets BUSY for routing
        # tests. Complete all unfinished calls so routing starts deterministic.
        # (Production closes these via telephony status callbacks; the 30-minute
        # stuck-BUSY guard covers crashes.)
        conn.execute("UPDATE ivr_call_participants SET left_at=datetime('now') WHERE left_at IS NULL")
        conn.execute("UPDATE ivr_sessions SET vet_connected=0 WHERE vet_connected=1")
        conn.execute("UPDATE ivr_calls SET status='COMPLETED', ended_at=datetime('now') "
                     "WHERE status IN ('INITIATED','RINGING','IN_PROGRESS')")
        conn.commit()
        cls.owner = dict(conn.execute("SELECT * FROM users WHERE email='rajesh@example.com'").fetchone())
        cls.vet1 = dict(conn.execute("SELECT * FROM users WHERE email='vet1@example.com'").fetchone())
        cls.vet2 = dict(conn.execute("SELECT * FROM users WHERE email='vet2@example.com'").fetchone())
        cls.govt = dict(conn.execute("SELECT * FROM users WHERE role='govt' LIMIT 1").fetchone())
        conn.close()
        cls.owner_token = make_token(cls.owner)
        cls.vet_token = make_token(cls.vet1)
        cls.govt_token = make_token(cls.govt)

    def auth(self, token):
        return {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}

    def complete_survey(self, from_phone, to_phone, lang_digit="1", skip_keys=()):
        c = self.client
        resp = c.post("/api/ivr/mock/call", json={"from": from_phone, "to": to_phone})
        self.assertEqual(resp.status_code, 200)
        call_sid = resp.get_json()["call_sid"]
        resp = c.post(f"/api/ivr/webhook/language?call_sid={call_sid}", data={"Digits": lang_digit})
        self.assertEqual(resp.status_code, 200)
        resp = c.post(f"/api/ivr/webhook/menu?call_sid={call_sid}", data={"Digits": "2"})
        self.assertEqual(resp.status_code, 200)
        resp = c.post(f"/api/ivr/webhook/survey/start?call_sid={call_sid}")
        self.assertEqual(resp.status_code, 200)
        for q in ORDER:
            if q in skip_keys:
                continue
            digits, speech = ANSWERS[q]
            data = {}
            if digits:
                data["Digits"] = digits
            if speech:
                data["SpeechResult"] = speech
            resp = c.post(f"/api/ivr/webhook/survey?call_sid={call_sid}&q={q}", data=data)
            self.assertEqual(resp.status_code, 200, f"question {q}: {resp.data[:200]}")
        return call_sid

    # 1-3. Number, config, click-to-call -------------------------------
    def test_01_helpline_number_config(self):
        from ivr.config import (IVR_PHONE_NUMBER, get_helpline_e164,
                                get_helpline_display_in, is_helpline_number)
        self.assertEqual(IVR_PHONE_NUMBER, "7382210251")
        self.assertEqual(get_helpline_e164(), "+917382210251")
        self.assertEqual(get_helpline_display_in(), "+91 73822 10251")
        self.assertTrue(is_helpline_number("7382210251"))
        self.assertTrue(is_helpline_number("+917382210251"))
        self.assertFalse(is_helpline_number("+911800123456"))

    def test_02_ivr_info_exposes_helpline_without_secrets(self):
        resp = self.client.get("/api/ivr/info")
        self.assertEqual(resp.status_code, 200)
        body = resp.get_json()
        cfg = body["config"]
        self.assertEqual(cfg["helpline_number"], "7382210251")
        self.assertEqual(cfg["helpline_e164"], "+917382210251")
        self.assertEqual(cfg["helpline_display_in"], "+91 73822 10251")
        self.assertFalse(cfg["pstn_connected"])
        raw = json.dumps(body).lower()
        for secret in ("auth_token", "account_id", "webhook_secret", "sk-", "password_hash", "salt"):
            self.assertNotIn(secret, raw)

    def test_03_frontend_click_to_call(self):
        with open("../frontend/app.js", encoding="utf-8") as f:
            js = f.read()
        self.assertIn("+917382210251", js)
        self.assertIn("7382210251", js)
        self.assertIn("+91 73822 10251", js)
        self.assertIn("tel:", js)
        self.assertIn("CALL NOW", js)

    # 4. Channel detection ----------------------------------------------
    def test_04_channel_helpline_vs_ivr(self):
        r1 = self.client.post("/api/ivr/mock/call", json={"from": fresh_phone(), "to": "7382210251"})
        r2 = self.client.post("/api/ivr/mock/call", json={"from": fresh_phone(), "to": "+911800123456"})
        conn = database.get_db()
        ch1 = conn.execute("SELECT channel FROM ivr_calls WHERE call_sid=?", (r1.get_json()["call_sid"],)).fetchone()["channel"]
        ch2 = conn.execute("SELECT channel FROM ivr_calls WHERE call_sid=?", (r2.get_json()["call_sid"],)).fetchone()["channel"]
        conn.close()
        self.assertEqual(ch1, "HELPLINE")
        self.assertEqual(ch2, "IVR")

    # 5-6. Farmer identification -----------------------------------------
    def test_05_farmer_identification(self):
        from ivr.services.farmer import identify_farmer
        conn = database.get_db()
        farmer = identify_farmer(conn, "+919800000001")
        conn.close()
        self.assertIsNotNone(farmer)
        self.assertEqual(farmer["user"]["email"], "rajesh@example.com")
        self.assertEqual(farmer["region"]["district"], "Pune")
        self.assertEqual(farmer["region"]["source"], "PROFILE")
        self.assertGreater(len(farmer["animals"]), 0)

    def test_06_unknown_caller_not_identified(self):
        from ivr.services.farmer import identify_farmer
        conn = database.get_db()
        try:
            self.assertIsNone(identify_farmer(conn, "+919999999999"))
            self.assertIsNone(identify_farmer(conn, ""))
        finally:
            conn.close()

    # 7-9. Language routing ----------------------------------------------
    def test_07_known_language_skips_prompt(self):
        conn = database.get_db()
        conn.execute("UPDATE users SET preferred_language='te' WHERE id=?", (self.owner["id"],))
        conn.commit()
        conn.close()
        sid = fresh_sid("HL-LANG")
        try:
            resp = self.client.post("/api/ivr/webhook/call",
                                    data={"From": "+919800000001", "To": "7382210251",
                                          "CallSid": sid})
            self.assertEqual(resp.status_code, 200)
            twiml = resp.data.decode()
            # Telugu main menu served directly; English language menu NOT asked.
            self.assertIn("nokkandi", twiml)
            self.assertNotIn("Press 1 for English", twiml)
            conn = database.get_db()
            sess = conn.execute("SELECT language, caller_user_id, routing_status FROM ivr_sessions WHERE call_sid=?", (sid,)).fetchone()
            conn.close()
            self.assertEqual(sess["language"], "te")
            self.assertEqual(sess["caller_user_id"], self.owner["id"])
            self.assertEqual(sess["routing_status"], "IDENTIFIED")
        finally:
            conn = database.get_db()
            conn.execute("UPDATE users SET preferred_language=NULL WHERE id=?", (self.owner["id"],))
            conn.commit()
            conn.close()

    def test_08_unknown_caller_gets_language_prompt(self):
        resp = self.client.post("/api/ivr/webhook/call",
                                data={"From": fresh_phone(), "To": "7382210251",
                                      "CallSid": fresh_sid("HL-LANG")})
        self.assertEqual(resp.status_code, 200)
        self.assertIn("Press 1 for English", resp.data.decode())

    def test_09_language_choice_persisted(self):
        resp = self.client.post("/api/ivr/mock/call", json={"from": "+919800000001", "to": "7382210251"})
        call_sid = resp.get_json()["call_sid"]
        self.client.post(f"/api/ivr/webhook/language?call_sid={call_sid}", data={"Digits": "3"})
        conn = database.get_db()
        lang = conn.execute("SELECT preferred_language FROM users WHERE id=?", (self.owner["id"],)).fetchone()["preferred_language"]
        conn.execute("UPDATE users SET preferred_language=NULL WHERE id=?", (self.owner["id"],))
        conn.commit()
        conn.close()
        self.assertEqual(lang, "hi")

    # 10-12. Region + prefill --------------------------------------------
    def test_10_region_priority_profile(self):
        from ivr.services.farmer import resolve_region
        conn = database.get_db()
        region = resolve_region(conn, self.owner)
        conn.close()
        self.assertEqual(region["source"], "PROFILE")
        self.assertEqual(region["district"], "Pune")

    def test_11_survey_prefill_skips_known(self):
        from ivr.services.farmer import get_prefilled_keys
        resp = self.client.post("/api/ivr/mock/call", json={"from": "+919800000001", "to": "7382210251"})
        call_sid = resp.get_json()["call_sid"]
        self.client.post(f"/api/ivr/webhook/language?call_sid={call_sid}", data={"Digits": "1"})
        self.client.post(f"/api/ivr/webhook/menu?call_sid={call_sid}", data={"Digits": "2"})
        self.client.post(f"/api/ivr/webhook/survey/start?call_sid={call_sid}")
        conn = database.get_db()
        prefilled = get_prefilled_keys(conn, call_sid)
        first = conn.execute("SELECT current_question_key FROM ivr_sessions WHERE call_sid=?", (call_sid,)).fetchone()["current_question_key"]
        conn.close()
        for key in ("farmer_name", "location_village", "location_district", "location_state", "species"):
            self.assertIn(key, prefilled)
        self.assertNotEqual(first, "farmer_name")

    def test_12_explicit_answer_overrides_prefill(self):
        from ivr.services.farmer import get_prefilled_keys
        resp = self.client.post("/api/ivr/mock/call", json={"from": "+919800000001", "to": "7382210251"})
        call_sid = resp.get_json()["call_sid"]
        self.client.post(f"/api/ivr/webhook/language?call_sid={call_sid}", data={"Digits": "1"})
        self.client.post(f"/api/ivr/webhook/menu?call_sid={call_sid}", data={"Digits": "2"})
        self.client.post(f"/api/ivr/webhook/survey/start?call_sid={call_sid}")
        # Farmer explicitly corrects species to Buffalo (digit 2)
        r = self.client.post(f"/api/ivr/webhook/survey?call_sid={call_sid}&q=species", data={"Digits": "2"})
        self.assertEqual(r.status_code, 200)
        conn = database.get_db()
        prefilled = get_prefilled_keys(conn, call_sid)
        latest = conn.execute("SELECT answer_normalized FROM ivr_survey_responses WHERE call_sid=? AND question_key='species' ORDER BY id DESC LIMIT 1", (call_sid,)).fetchone()["answer_normalized"]
        conn.close()
        self.assertNotIn("species", prefilled)
        self.assertEqual(latest, "Buffalo")

    # 13-17. Vet routing --------------------------------------------------
    def test_13_routing_prefers_same_district(self):
        from ivr.services.call_service import find_available_vet
        conn = database.get_db()
        try:
            vet = find_available_vet(conn, "Pune")
            self.assertIsNotNone(vet)
            self.assertEqual(vet["id"], self.vet1["id"])
        finally:
            conn.close()

    def test_14_unavailable_vet_skipped(self):
        from ivr.services.call_service import find_available_vet
        conn = database.get_db()
        conn.execute("UPDATE users SET availability_status='OFFLINE' WHERE id=?", (self.vet1["id"],))
        conn.commit()
        try:
            vet = find_available_vet(conn, "Pune")
            self.assertIsNotNone(vet)
            self.assertNotEqual(vet["id"], self.vet1["id"])
        finally:
            conn.execute("UPDATE users SET availability_status='AVAILABLE' WHERE id=?", (self.vet1["id"],))
            conn.commit()
            conn.close()

    def test_15_outside_hours_status(self):
        from ivr.services.routing import get_vet_availability
        conn = database.get_db()
        conn.execute("INSERT INTO users (full_name, mobile, email, password_hash, salt, role, district) VALUES (?,?,?,?,?,?,?)",
                     ("Night Vet", "9800098001", "nightvet@example.com", "x", "y", "vet", "Pune"))
        vid = conn.execute("SELECT id FROM users WHERE email='nightvet@example.com'").fetchone()["id"]
        try:
            vet = dict(conn.execute("SELECT * FROM users WHERE id=?", (vid,)).fetchone())
            with mock.patch("ivr.services.routing._current_ist_hour", return_value=3):
                self.assertEqual(get_vet_availability(conn, vet), "OUTSIDE_HOURS")
            with mock.patch("ivr.services.routing._current_ist_hour", return_value=10):
                self.assertEqual(get_vet_availability(conn, vet), "AVAILABLE")
        finally:
            conn.execute("DELETE FROM users WHERE id=?", (vid,))
            conn.commit()
            conn.close()

    def test_16_busy_while_on_live_call(self):
        from ivr.services.routing import get_vet_availability
        from ivr.services.call_service import vet_call_connected, handle_call_end
        conn = database.get_db()
        resp = self.client.post("/api/ivr/mock/call", json={"from": fresh_phone(), "to": "7382210251"})
        call_sid = resp.get_json()["call_sid"]
        try:
            vet_call_connected(conn, call_sid, self.vet1["id"])
            vet = dict(conn.execute("SELECT * FROM users WHERE id=?", (self.vet1["id"],)).fetchone())
            self.assertEqual(get_vet_availability(conn, vet), "BUSY")
        finally:
            handle_call_end(conn, call_sid, reason="test")
            conn.close()

    def test_17_existing_assignment_preferred(self):
        from ivr.services.call_service import rank_vets_for_call
        conn = database.get_db()
        # Temporarily close any existing active cases for owner with other vets so only vid has the active assignment
        conn.execute("UPDATE cases SET status='CLOSED' WHERE owner_id=? AND vet_id!=0", (self.owner["id"],))
        conn.execute("INSERT INTO users (full_name, mobile, email, password_hash, salt, role, district, availability_status) VALUES (?,?,?,?,?,?,?,?)",
                     ("Pune Vet Two", "9800098002", "punevet2@example.com", "x", "y", "vet", "Pune", "AVAILABLE"))
        vid = conn.execute("SELECT id FROM users WHERE email='punevet2@example.com'").fetchone()["id"]
        animal = conn.execute("SELECT id FROM animals WHERE owner_id=? LIMIT 1", (self.owner["id"],)).fetchone()
        conn.execute("INSERT INTO cases (case_no, animal_id, owner_id, vet_id, symptoms, status) VALUES (?,?,?,?,?,?)",
                     ("CASE-HL-ASSIGN-1", animal["id"], self.owner["id"], vid, "test", "NEW"))
        conn.commit()
        try:
            ranked = rank_vets_for_call(conn, "Pune", "en", self.owner["id"])
            self.assertEqual(ranked[0]["id"], vid)
            self.assertIn("existing_assignment", ranked[0]["reasons"])
        finally:
            conn.execute("DELETE FROM cases WHERE case_no='CASE-HL-ASSIGN-1'")
            conn.execute("DELETE FROM users WHERE id=?", (vid,))
            conn.execute("UPDATE cases SET status='DIAGNOSED' WHERE case_no='CASE-000801'")
            conn.commit()
            conn.close()

    # 18. No-vet fallback --------------------------------------------------
    def test_18_no_vet_falls_back_to_survey(self):
        conn = database.get_db()
        prior = {r["id"]: r["availability_status"] for r in
                 conn.execute("SELECT id, availability_status FROM users WHERE role='vet'").fetchall()}
        conn.execute("UPDATE users SET availability_status='OFFLINE' WHERE role='vet'")
        conn.commit()
        try:
            resp = self.client.post("/api/ivr/mock/call", json={"from": fresh_phone(), "to": "7382210251"})
            call_sid = resp.get_json()["call_sid"]
            self.client.post(f"/api/ivr/webhook/language?call_sid={call_sid}", data={"Digits": "1"})
            resp = self.client.post(f"/api/ivr/webhook/menu?call_sid={call_sid}", data={"Digits": "1"})
            self.assertEqual(resp.status_code, 200)
            conn2 = database.get_db()
            state = conn2.execute("SELECT current_state, routing_status FROM ivr_sessions WHERE call_sid=?", (call_sid,)).fetchone()
            conn2.close()
            self.assertEqual(state["current_state"], "SURVEY")
            self.assertEqual(state["routing_status"], "VET_UNAVAILABLE")
        finally:
            for vid, status in prior.items():
                conn.execute("UPDATE users SET availability_status=? WHERE id=?", (status, vid))
            conn.commit()
            conn.close()

    # 19-20. Report + case source -------------------------------------------
    def test_19_helpline_survey_creates_helpline_case(self):
        call_sid = self.complete_survey(fresh_phone(), "7382210251")
        conn = database.get_db()
        report = conn.execute("SELECT * FROM ivr_reports WHERE call_sid=?", (call_sid,)).fetchone()
        self.assertIsNotNone(report)
        self.assertEqual(report["source"], "HELPLINE")
        case = conn.execute("SELECT * FROM cases WHERE id=?", (report["case_id"],)).fetchone()
        conn.close()
        self.assertIsNotNone(case)
        self.assertEqual(case["reported_through"], "HELPLINE")

    def test_20_ivr_channel_preserved(self):
        call_sid = self.complete_survey(fresh_phone(), "+911800123456")
        conn = database.get_db()
        report = conn.execute("SELECT * FROM ivr_reports WHERE call_sid=?", (call_sid,)).fetchone()
        case = conn.execute("SELECT * FROM cases WHERE id=?", (report["case_id"],)).fetchone()
        conn.close()
        self.assertEqual(report["source"], "IVR")
        self.assertEqual(case["reported_through"], "IVR")

    # 21-22. Location honesty -------------------------------------------------
    def test_21_profile_location_source(self):
        call_sid = self.complete_survey("+919800000001", "7382210251",
                                        skip_keys=("farmer_name", "species", "breed", "age", "sex",
                                                   "location_village", "location_district", "location_state"))
        conn = database.get_db()
        report = conn.execute("SELECT location_source, location_district FROM ivr_reports WHERE call_sid=?", (call_sid,)).fetchone()
        conn.close()
        self.assertEqual(report["location_source"], "PROFILE")
        self.assertEqual(report["location_district"], "Pune")

    def test_22_no_fake_gps(self):
        call_sid = self.complete_survey("+919800000001", "7382210251",
                                        skip_keys=("farmer_name", "species", "breed", "age", "sex",
                                                   "location_village", "location_district", "location_state"))
        conn = database.get_db()
        report = conn.execute("SELECT location_lat, location_lng FROM ivr_reports WHERE call_sid=?", (call_sid,)).fetchone()
        conn.close()
        self.assertIsNone(report["location_lat"])
        self.assertIsNone(report["location_lng"])

    # 23-24. Notifications + govt/GIS ------------------------------------------
    def test_23_vet_notification_content(self):
        call_sid = self.complete_survey(fresh_phone(), "7382210251")
        conn = database.get_db()
        report = conn.execute("SELECT report_no FROM ivr_reports WHERE call_sid=?", (call_sid,)).fetchone()
        note = conn.execute("SELECT message FROM notifications WHERE user_id=? ORDER BY id DESC LIMIT 1", (self.vet1["id"],)).fetchone()
        conn.close()
        self.assertIn(report["report_no"], note["message"])
        self.assertIn("HELPLINE", note["message"])
        self.assertIn("Urgency", note["message"])

    def test_24_govt_visibility_and_gis(self):
        call_sid = self.complete_survey(fresh_phone(), "7382210251")
        resp = self.client.get("/api/ivr/reports", headers=self.auth(self.govt_token))
        self.assertTrue(any(r["call_sid"] == call_sid for r in resp.get_json()))
        resp = self.client.get("/api/cases", headers=self.auth(self.govt_token))
        cases = [c for c in resp.get_json() if c["reported_through"] == "HELPLINE"]
        self.assertGreater(len(cases), 0)
        resp = self.client.get("/api/govt/geo", headers=self.auth(self.govt_token))
        self.assertEqual(resp.status_code, 200)

    # 25-26. Duplicates + partial -----------------------------------------------
    def test_25_duplicate_flagged(self):
        dup_phone = fresh_phone()
        self.complete_survey(dup_phone, "7382210251")
        call_sid2 = self.complete_survey(dup_phone, "7382210251")
        conn = database.get_db()
        report = conn.execute("SELECT status, is_duplicate FROM ivr_reports WHERE call_sid=?", (call_sid2,)).fetchone()
        conn.close()
        self.assertEqual(report["status"], "DUPLICATE_FLAGGED")
        self.assertEqual(report["is_duplicate"], 1)

    def test_26_partial_preserved(self):
        c = self.client
        resp = c.post("/api/ivr/mock/call", json={"from": fresh_phone(), "to": "7382210251"})
        call_sid = resp.get_json()["call_sid"]
        c.post(f"/api/ivr/webhook/language?call_sid={call_sid}", data={"Digits": "1"})
        c.post(f"/api/ivr/webhook/menu?call_sid={call_sid}", data={"Digits": "2"})
        c.post(f"/api/ivr/webhook/survey/start?call_sid={call_sid}")
        c.post(f"/api/ivr/webhook/survey?call_sid={call_sid}&q=farmer_name", data={"SpeechResult": "Partial Caller"})
        c.post(f"/api/ivr/webhook/survey?call_sid={call_sid}&q=species", data={"Digits": "1"})
        c.post("/api/ivr/webhook/call-ended", json={"call_sid": call_sid, "duration": 90})
        conn = database.get_db()
        report = conn.execute("SELECT status, source FROM ivr_reports WHERE call_sid=?", (call_sid,)).fetchone()
        conn.close()
        self.assertIsNotNone(report)
        self.assertEqual(report["status"], "PARTIALLY_COMPLETED")
        self.assertEqual(report["source"], "HELPLINE")

    # 27-28. Lifecycle + analytics ------------------------------------------------
    def test_27_routing_status_lifecycle(self):
        c = self.client
        sid = fresh_sid("HL-LIFE")
        resp = c.post("/api/ivr/webhook/call",
                      data={"From": "+919800000001", "To": "7382210251", "CallSid": sid})
        self.assertEqual(resp.status_code, 200)
        conn = database.get_db()
        st = conn.execute("SELECT routing_status FROM ivr_sessions WHERE call_sid=?", (sid,)).fetchone()["routing_status"]
        conn.close()
        self.assertEqual(st, "IDENTIFIED")
        c.post(f"/api/ivr/webhook/menu?call_sid={sid}", data={"Digits": "1"})
        conn = database.get_db()
        st = conn.execute("SELECT routing_status FROM ivr_sessions WHERE call_sid=?", (sid,)).fetchone()["routing_status"]
        conn.close()
        self.assertEqual(st, "VET_CONNECTED")
        c.post("/api/ivr/webhook/call-ended", json={"call_sid": sid, "duration": 60})

    def test_28_analytics_helpline_counts(self):
        resp = self.client.get("/api/ivr/analytics", headers=self.auth(self.govt_token))
        self.assertEqual(resp.status_code, 200)
        body = resp.get_json()
        self.assertGreaterEqual(body["helpline_calls"], 1)
        self.assertGreaterEqual(body["helpline_reports"], 1)
        self.assertTrue(any(b["label"] == "HELPLINE" for b in body["by_channel"]))
        self.assertIn("by_routing_status", body)

    # 29-30. Profile endpoints -----------------------------------------------------
    def test_29_update_own_profile(self):
        # owner sets language
        resp = self.client.put("/api/users/me", headers=self.auth(self.owner_token),
                               json={"preferred_language": "hi"})
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.get_json()["preferred_language"], "hi")
        # invalid language rejected
        resp = self.client.put("/api/users/me", headers=self.auth(self.owner_token),
                               json={"preferred_language": "xx"})
        self.assertEqual(resp.status_code, 400)
        # owner cannot set availability
        resp = self.client.put("/api/users/me", headers=self.auth(self.owner_token),
                               json={"availability_status": "BUSY"})
        self.assertEqual(resp.status_code, 403)
        # vet can set availability
        resp = self.client.put("/api/users/me", headers=self.auth(self.vet_token),
                               json={"availability_status": "BUSY"})
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.get_json()["availability_status"], "BUSY")
        # restore
        self.client.put("/api/users/me", headers=self.auth(self.vet_token),
                        json={"availability_status": "AVAILABLE"})
        conn = database.get_db()
        conn.execute("UPDATE users SET preferred_language=NULL WHERE id=?", (self.owner["id"],))
        conn.commit()
        conn.close()

    def test_30_register_with_language(self):
        tag = uuid.uuid4().hex[:8]
        mobile = f"981{random.randint(1000000, 9999999)}"
        try:
            resp = self.client.post("/api/auth/register", json={
                "full_name": "Lang Farmer", "mobile": mobile,
                "email": f"langfarmer-{tag}@example.com",
                "password": "password123", "confirm_password": "password123", "role": "owner",
                "village": "X", "district": "Kadapa", "preferred_language": "te"})
            self.assertIn(resp.status_code, (200, 201))
            self.assertEqual(resp.get_json()["user"]["preferred_language"], "te")
        finally:
            conn = database.get_db()
            ids = [r["id"] for r in conn.execute(
                "SELECT id FROM users WHERE email LIKE 'langfarmer%@example.com'").fetchall()]
            for uid in ids:
                conn.execute("DELETE FROM audit_events WHERE actor_id=?", (uid,))
                conn.execute("DELETE FROM users WHERE id=?", (uid,))
            conn.commit()
            conn.close()


if __name__ == "__main__":
    unittest.main()
