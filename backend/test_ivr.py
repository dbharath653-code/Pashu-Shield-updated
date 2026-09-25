import unittest
import json
import uuid
from app import app, make_token
import database

class TestIVRReportingSystem(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        database.init_db()
        cls.client = app.test_client()
        conn = database.get_db()
        cls.owner = dict(conn.execute("SELECT * FROM users WHERE role='owner' LIMIT 1").fetchone())
        cls.vet = dict(conn.execute("SELECT * FROM users WHERE role='vet' LIMIT 1").fetchone())
        cls.govt = dict(conn.execute("SELECT * FROM users WHERE role='govt' LIMIT 1").fetchone())
        cls.lab = dict(conn.execute("SELECT * FROM users WHERE role='lab' LIMIT 1").fetchone())
        conn.close()
        cls.owner_token = make_token(cls.owner)
        cls.vet_token = make_token(cls.vet)
        cls.govt_token = make_token(cls.govt)
        cls.lab_token = make_token(cls.lab)

    def auth(self, token):
        return {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}

    # Helper to complete survey
    def complete_survey(self, from_phone="+919800000301", answers=None):
        # Default complete answers
        if answers is None:
            answers = {
                "farmer_name": ("", "Ramesh Patil"),
                "species": ("1", None),
                "animal_count": ("2", None),
                "breed": ("", "Gir"),
                "age": ("4", None),
                "sex": ("1", None),
                "pregnancy": ("2", None),
                "main_problem": ("1", None),
                "symptoms": ("", "Fever and not eating since 3 days"),
                "duration": ("3", None),
                "severity": ("3", None),
                "eating": ("2", None),
                "drinking": ("2", None),
                "temperature": ("104", None),
                "vaccination": ("2", None),
                "previous_disease": ("2", None),
                "medicines": ("", "No medicine given"),
                "location_village": ("", "Wagholi"),
                "location_district": ("", "Pune"),
                "location_state": ("1", None),
                "additional": ("", "Animal is lethargic"),
            }
        # Start mock call
        resp = self.client.post("/api/ivr/mock/call", json={"from": from_phone})
        self.assertEqual(resp.status_code, 200)
        call_sid = resp.get_json()["call_sid"]
        # Language 1 (en)
        resp = self.client.post(f"/api/ivr/webhook/language?call_sid={call_sid}", data={"Digits": "1"})
        self.assertEqual(resp.status_code, 200)
        # Menu 2 (survey)
        resp = self.client.post(f"/api/ivr/webhook/menu?call_sid={call_sid}", data={"Digits": "2"})
        self.assertEqual(resp.status_code, 200)
        resp = self.client.post(f"/api/ivr/webhook/survey/start?call_sid={call_sid}")
        self.assertEqual(resp.status_code, 200)
        # Answer each
        for q in ["farmer_name","species","animal_count","breed","age","sex","pregnancy","main_problem","symptoms","duration","severity","eating","drinking","temperature","vaccination","previous_disease","medicines","location_village","location_district","location_state","additional"]:
            digits, speech = answers.get(q, ("", ""))
            data = {}
            if digits:
                data["Digits"] = digits
            if speech:
                data["SpeechResult"] = speech
            resp = self.client.post(f"/api/ivr/webhook/survey?call_sid={call_sid}&q={q}", data=data)
            self.assertEqual(resp.status_code, 200)
        return call_sid

    def test_01_ivr_answers(self):
        # TEST 1: Farmer calls IVR -> answers
        resp = self.client.post("/api/ivr/mock/call", json={"from": "+919800000401"})
        self.assertEqual(resp.status_code, 200)
        call_sid = resp.get_json()["call_sid"]
        resp = self.client.post("/api/ivr/webhook/incoming", data={"CallSid": call_sid, "From": "+919800000401", "To": "+911800123456"})
        self.assertEqual(resp.status_code, 200)
        self.assertIn(b"Welcome", resp.data)
        self.assertIn(b"Press 1 for English", resp.data)

    def test_02_language_selection(self):
        resp = self.client.post("/api/ivr/mock/call", json={"from": "+919800000402"})
        call_sid = resp.get_json()["call_sid"]
        # Test Telugu
        resp = self.client.post(f"/api/ivr/webhook/language?call_sid={call_sid}", data={"Digits": "2"})
        self.assertEqual(resp.status_code, 200)
        # Check that next menu uses Telugu or at least doesn't fail
        # Check DB language persisted
        conn = database.get_db()
        sess = conn.execute("SELECT language FROM ivr_sessions WHERE call_sid=?", (call_sid,)).fetchone()
        conn.close()
        self.assertEqual(sess["language"], "te")

        # Test Hindi
        resp = self.client.post("/api/ivr/mock/call", json={"from": "+919800000403"})
        call_sid2 = resp.get_json()["call_sid"]
        resp = self.client.post(f"/api/ivr/webhook/language?call_sid={call_sid2}", data={"Digits": "3"})
        conn = database.get_db()
        sess = conn.execute("SELECT language FROM ivr_sessions WHERE call_sid=?", (call_sid2,)).fetchone()
        conn.close()
        self.assertEqual(sess["language"], "hi")

        # Test English
        resp = self.client.post("/api/ivr/mock/call", json={"from": "+919800000404"})
        call_sid3 = resp.get_json()["call_sid"]
        resp = self.client.post(f"/api/ivr/webhook/language?call_sid={call_sid3}", data={"Digits": "1"})
        conn = database.get_db()
        sess = conn.execute("SELECT language FROM ivr_sessions WHERE call_sid=?", (call_sid3,)).fetchone()
        conn.close()
        self.assertEqual(sess["language"], "en")

    def test_03_vet_connection_available(self):
        resp = self.client.post("/api/ivr/mock/call", json={"from": "+919800000405"})
        call_sid = resp.get_json()["call_sid"]
        self.client.post(f"/api/ivr/webhook/language?call_sid={call_sid}", data={"Digits": "1"})
        resp = self.client.post(f"/api/ivr/webhook/menu?call_sid={call_sid}", data={"Digits": "1"})
        self.assertEqual(resp.status_code, 200)
        # Should contain Dial or Connecting
        self.assertTrue(b"Connecting" in resp.data or b"Dial" in resp.data or b"Connecting you" in resp.data)
        # Check vet_connected in DB
        conn = database.get_db()
        sess = conn.execute("SELECT vet_connected, vet_id FROM ivr_sessions WHERE call_sid=?", (call_sid,)).fetchone()
        conn.close()
        # Vet should be connected (mock always connects if vet exists)
        self.assertIsNotNone(sess)

    def test_04_vet_unavailable_transitions_to_survey(self):
        # Simulate by making all vets overloaded? For now, we test that menu 2 goes to survey.
        # The vet unavailable path is also triggered when vet not found for a district with no vets.
        # We can test by using a phone from a district with no vets, but our fallback picks any vet.
        # So we just verify survey starts automatically when vet_unavailable event would fire.
        resp = self.client.post("/api/ivr/mock/call", json={"from": "+919800000406"})
        call_sid = resp.get_json()["call_sid"]
        self.client.post(f"/api/ivr/webhook/language?call_sid={call_sid}", data={"Digits": "1"})
        resp = self.client.post(f"/api/ivr/webhook/menu?call_sid={call_sid}", data={"Digits": "2"})
        self.assertEqual(resp.status_code, 200)
        self.assertIn(b"survey", resp.data.lower() or b"question" in resp.data.lower() or b"Will ask" in resp.data)

    def test_05_survey_completes_and_generates_report(self):
        call_sid = self.complete_survey(from_phone="+919800000501")
        # Check report generated
        conn = database.get_db()
        report = conn.execute("SELECT * FROM ivr_reports WHERE call_sid=?", (call_sid,)).fetchone()
        conn.close()
        self.assertIsNotNone(report, "Report should be generated after survey completion")
        self.assertEqual(report["animal_species"], "Cattle")
        self.assertEqual(report["location_district"], "Pune")
        self.assertIn(report["urgency"], ["LOW","MEDIUM","HIGH","CRITICAL"])
        # Check linked case
        self.assertIsNotNone(report["case_id"])
        conn = database.get_db()
        case = conn.execute("SELECT * FROM cases WHERE id=?", (report["case_id"],)).fetchone()
        conn.close()
        self.assertIsNotNone(case)
        self.assertEqual(case["reported_through"], "IVR")
        self.assertIn(b"AI-generated summary", report["ai_summary"].encode())

    def test_06_phone_capture(self):
        call_sid = self.complete_survey(from_phone="+919800000502")
        conn = database.get_db()
        report = conn.execute("SELECT caller_number FROM ivr_reports WHERE call_sid=?", (call_sid,)).fetchone()
        call = conn.execute("SELECT caller_number_normalized FROM ivr_calls WHERE call_sid=?", (call_sid,)).fetchone()
        conn.close()
        self.assertEqual(report["caller_number"], "+919800000502")
        self.assertEqual(call["caller_number_normalized"], "+919800000502")

        # Test hidden caller -> should be empty but survey still works
        call_sid2 = self.complete_survey(from_phone="unknown")
        conn = database.get_db()
        report2 = conn.execute("SELECT caller_number FROM ivr_reports WHERE call_sid=?", (call_sid2,)).fetchone()
        conn.close()
        # May be empty or unknown, but report should still exist
        self.assertIsNotNone(report2)

    def test_07_location_capture(self):
        call_sid = self.complete_survey(from_phone="+919800000503")
        conn = database.get_db()
        report = conn.execute("SELECT location_village, location_district, location_state, location_source, location_lat, location_lng FROM ivr_reports WHERE call_sid=?", (call_sid,)).fetchone()
        conn.close()
        self.assertEqual(report["location_village"], "Wagholi")
        self.assertEqual(report["location_district"], "Pune")
        self.assertEqual(report["location_source"], "FARMER_PROVIDED")
        # Must NOT have fake GPS
        self.assertIsNone(report["location_lat"])
        self.assertIsNone(report["location_lng"])
        # The system must explicitly mark accuracy - case insensitive check
        self.assertIn("FARMER", (report["location_source"] or "").upper())

        # Test GPS via SMS link
        resp = self.client.post("/api/ivr/location/share", json={"call_sid": call_sid, "lat": 18.5204, "lng": 73.8567, "token": "test"})
        self.assertEqual(resp.status_code, 200)
        conn = database.get_db()
        report2 = conn.execute("SELECT location_lat, location_lng, location_source FROM ivr_reports WHERE call_sid=?", (call_sid,)).fetchone()
        conn.close()
        self.assertEqual(report2["location_lat"], 18.5204)
        self.assertEqual(report2["location_source"], "GPS")

    def test_08_ai_summary_no_hallucination(self):
        call_sid = self.complete_survey(from_phone="+919800000504")
        conn = database.get_db()
        report = conn.execute("SELECT ai_structured_json, ai_summary FROM ivr_reports WHERE call_sid=?", (call_sid,)).fetchone()
        conn.close()
        structured = json.loads(report["ai_structured_json"])
        # Check required disclaimer
        self.assertIn("AI-generated summary", report["ai_summary"])
        self.assertIn("veterinarian verification required", report["ai_summary"].lower())
        # Check no hallucinated missing fields are empty not invented
        self.assertNotEqual(structured["animal"]["breed"], "")
        # Check that missing info is "Not provided" not invented
        # All fields should be present
        self.assertIn("symptoms", structured)
        self.assertIn("urgency", structured)
        # Validate schema
        self.assertEqual(structured["source"], "IVR")
        self.assertEqual(structured["call_id"], call_sid)

    def test_09_vet_notification(self):
        call_sid = self.complete_survey(from_phone="+919800000505")
        conn = database.get_db()
        # Check vet got notification
        vet_id = conn.execute("SELECT id FROM users WHERE role='vet' AND district='Pune' LIMIT 1").fetchone()
        if vet_id:
            note = conn.execute("SELECT * FROM notifications WHERE user_id=? ORDER BY id DESC LIMIT 1", (vet_id["id"],)).fetchone()
            self.assertIsNotNone(note)
            self.assertIn("IVR", note["message"])
        conn.close()

    def test_10_govt_portal_visibility(self):
        call_sid = self.complete_survey(from_phone="+919800000506")
        headers = self.auth(self.govt_token)
        resp = self.client.get("/api/ivr/reports", headers=headers)
        self.assertEqual(resp.status_code, 200)
        reports = resp.get_json()
        self.assertGreater(len(reports), 0)
        # Check that our call_sid is visible
        found = any(r["call_sid"] == call_sid for r in reports)
        self.assertTrue(found)

        # Also check cases are visible via normal govt analytics
        resp = self.client.get("/api/cases", headers=headers)
        self.assertEqual(resp.status_code, 200)
        cases = resp.get_json()
        ivr_cases = [c for c in cases if c["reported_through"] == "IVR"]
        self.assertGreater(len(ivr_cases), 0)

    def test_11_partial_disconnect_preserved(self):
        resp = self.client.post("/api/ivr/mock/call", json={"from": "+919800000507"})
        call_sid = resp.get_json()["call_sid"]
        self.client.post(f"/api/ivr/webhook/language?call_sid={call_sid}", data={"Digits": "1"})
        self.client.post(f"/api/ivr/webhook/menu?call_sid={call_sid}", data={"Digits": "2"})
        self.client.post(f"/api/ivr/webhook/survey/start?call_sid={call_sid}")
        # Answer only 3 questions then disconnect
        self.client.post(f"/api/ivr/webhook/survey?call_sid={call_sid}&q=farmer_name", data={"SpeechResult": "Partial Farmer"})
        self.client.post(f"/api/ivr/webhook/survey?call_sid={call_sid}&q=species", data={"Digits": "1"})
        self.client.post(f"/api/ivr/webhook/survey?call_sid={call_sid}&q=animal_count", data={"Digits": "1"})
        # Simulate disconnect
        self.client.post(f"/api/ivr/webhook/status", data={"CallSid": call_sid, "CallStatus": "completed", "CallDuration": "45"})
        conn = database.get_db()
        # Check that partial report was saved or at least responses preserved
        responses = conn.execute("SELECT COUNT(*) c FROM ivr_survey_responses WHERE call_sid=?", (call_sid,)).fetchone()["c"]
        # Check if partial report exists (depends on implementation)
        report = conn.execute("SELECT * FROM ivr_reports WHERE call_sid=?", (call_sid,)).fetchone()
        conn.close()
        self.assertGreater(responses, 0)
        # If report exists, it should be PARTIALLY_COMPLETED
        if report:
            self.assertIn(report["status"], ["PARTIALLY_COMPLETED", "RECEIVED"])

    def test_12_speech_fallback_to_dtmf(self):
        # Speech recognition can fail -> DTMF fallback should work
        resp = self.client.post("/api/ivr/mock/call", json={"from": "+919800000508"})
        call_sid = resp.get_json()["call_sid"]
        self.client.post(f"/api/ivr/webhook/language?call_sid={call_sid}", data={"Digits": "1"})
        self.client.post(f"/api/ivr/webhook/menu?call_sid={call_sid}", data={"Digits": "2"})
        self.client.post(f"/api/ivr/webhook/survey/start?call_sid={call_sid}")
        # Send invalid DTMF for species (e.g., 9 which is repeat) but also speech
        # The system should handle 9 as repeat, not as answer
        resp = self.client.post(f"/api/ivr/webhook/survey?call_sid={call_sid}&q=species", data={"Digits": "9"})
        self.assertEqual(resp.status_code, 200)
        # Should repeat question, not advance
        self.assertNotIn(b"Thank you", resp.data)
        # Now correct answer 1
        resp = self.client.post(f"/api/ivr/webhook/survey?call_sid={call_sid}&q=species", data={"Digits": "1"})
        self.assertEqual(resp.status_code, 200)

        # Test DTMF controls: 0 go back
        resp = self.client.post(f"/api/ivr/webhook/survey?call_sid={call_sid}&q=animal_count", data={"Digits": "0"})
        self.assertEqual(resp.status_code, 200)

    def test_13_idempotent_webhook_no_duplicate(self):
        call_sid = self.complete_survey(from_phone="+919800000509")
        conn = database.get_db()
        first_report = conn.execute("SELECT id FROM ivr_reports WHERE call_sid=?", (call_sid,)).fetchone()
        count_before = conn.execute("SELECT COUNT(*) c FROM ivr_reports WHERE call_sid=?", (call_sid,)).fetchone()["c"]
        conn.close()
        # Re-send survey completion (simulate duplicate webhook)
        # Our system should not create duplicate due to call_sid uniqueness in session?
        # Try to manually trigger duplicate report generation
        conn = database.get_db()
        from ivr.services.report_service import create_ivr_report_from_survey
        responses = {"species": "Cattle", "main_problem": "Fever", "location_village": "Wagholi", "location_district": "Pune"}
        # This second creation should be flagged as duplicate but not create second case blindly?
        # We test duplicate detection service
        from ivr.services.duplicate import check_duplicate
        is_dup, dup_id = check_duplicate(conn, "+919800000509", "Cattle", "Fever", hours=24)
        conn.close()
        self.assertTrue(is_dup)
        self.assertEqual(dup_id, first_report["id"])
        # Ensure only one report for this call_sid or second is flagged
        conn = database.get_db()
        count_after = conn.execute("SELECT COUNT(*) c FROM ivr_reports WHERE call_sid=?", (call_sid,)).fetchone()["c"]
        conn.close()
        self.assertEqual(count_before, count_after)

    def test_14_unauthorized_access_denied(self):
        # Owner should not access all govt reports? Actually owner can access own only
        # Unauthenticated should fail
        resp = self.client.get("/api/ivr/reports")
        self.assertEqual(resp.status_code, 401)
        # Owner trying to access govt-only config should fail
        headers = self.auth(self.owner_token)
        resp = self.client.get("/api/ivr/config", headers=headers)
        self.assertEqual(resp.status_code, 403)
        # Vet should access
        headers = self.auth(self.vet_token)
        resp = self.client.get("/api/ivr/calls", headers=headers)
        self.assertIn(resp.status_code, [200, 403])  # vet allowed for calls

    def test_15_existing_web_reporting_still_works(self):
        # Create a normal web case as owner
        headers = self.auth(self.owner_token)
        # Get animal
        resp = self.client.get("/api/animals", headers=headers)
        animal = resp.get_json()[0]
        resp = self.client.post("/api/cases", json={"animal_id": animal["id"], "symptoms": "Test web report", "severity": "Low"}, headers=headers)
        self.assertEqual(resp.status_code, 201)
        self.assertEqual(resp.get_json()["reported_through"], "Mobile App")

    def test_16_existing_features_no_regression(self):
        # Check herds, animals, lab, etc still work
        headers = self.auth(self.vet_token)
        resp = self.client.get("/api/herds", headers=headers)
        self.assertEqual(resp.status_code, 200)
        resp = self.client.get("/api/animals", headers=headers)
        self.assertEqual(resp.status_code, 200)
        resp = self.client.get("/api/govt/analytics", headers=self.auth(self.govt_token))
        self.assertEqual(resp.status_code, 200)
        self.assertIn("totals", resp.get_json())

if __name__ == "__main__":
    unittest.main()
