import unittest
import json
import base64
import uuid
from app import app, make_token
import database

class TestPashuHealthChain(unittest.TestCase):
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

    def auth_headers(self, token):
        return {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}

    # 1. Auth & Roles
    def test_01_lab_auth(self):
        resp = self.client.post("/api/auth/login", json={"identifier": self.lab["email"], "password": "password123"})
        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        self.assertEqual(data["user"]["role"], "lab")

    # 2. Animal QR Identity
    def test_02_animal_qr_lifecycle(self):
        # Fetch animal 1
        resp = self.client.get("/api/animals/1", headers=self.auth_headers(self.owner_token))
        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        self.assertIn("qr", data)
        self.assertIsNotNone(data["qr"])
        self.assertTrue(data["qr"]["qr_image"].startswith("data:image/png;base64,"))

        # Regenerate QR
        resp = self.client.post("/api/animals/1/qr", headers=self.auth_headers(self.owner_token))
        self.assertEqual(resp.status_code, 201)
        new_qr = resp.get_json()
        token = new_qr["qr_token"]

        # Lookup QR
        resp = self.client.get(f"/api/animals/lookup-qr?token={token}", headers=self.auth_headers(self.owner_token))
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.get_json()["id"], 1)

        # Lookup manual fallback with animal_code
        resp = self.client.get(f"/api/animals/lookup-qr?code={data['animal_code']}", headers=self.auth_headers(self.owner_token))
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.get_json()["animal_code"], data["animal_code"])

    # 3. Reproductive Health
    def test_03_reproductive_health(self):
        resp = self.client.post("/api/animals/1/reproductive",
                                json={"pregnancy_status": "Confirmed Pregnant", "breeding_date": "2026-06-01", "event_type": "Pregnancy Check"},
                                headers=self.auth_headers(self.vet_token))
        self.assertEqual(resp.status_code, 201)
        data = resp.get_json()
        self.assertEqual(data["pregnancy_status"], "Confirmed Pregnant")
        self.assertTrue(data["expected_delivery_date"].startswith("2027-"))

        # Check in animal record
        resp = self.client.get("/api/animals/1", headers=self.auth_headers(self.owner_token))
        self.assertEqual(resp.status_code, 200)
        self.assertGreater(len(resp.get_json()["reproductive_records"]), 0)

    # 4. Medication & Allergy with Conflict Checking
    def test_04_allergy_prescription_conflict(self):
        # Ensure Penicillin allergy is recorded for animal 1
        resp = self.client.post("/api/animals/1/allergies",
                                json={"allergen": "Penicillin", "allergy_severity": "Severe", "reaction": "Anaphylaxis", "notes": "Life threatening"},
                                headers=self.auth_headers(self.vet_token))
        self.assertIn(resp.status_code, [201, 200])

        # Attempt to prescribe Amoxicillin (Penicillin class) WITHOUT override -> 409
        resp = self.client.post("/api/prescriptions",
                                json={"case_id": 1, "medicine": "Amoxicillin 500mg", "dosage": "1 tab", "frequency": "Daily", "duration": "5 days"},
                                headers=self.auth_headers(self.vet_token))
        self.assertEqual(resp.status_code, 409)
        err = resp.get_json()
        self.assertTrue(err.get("conflict"))
        self.assertTrue(err.get("requires_override"))

        # Prescribe WITH authorized override -> 201
        resp = self.client.post("/api/prescriptions",
                                json={"case_id": 1, "medicine": "Amoxicillin 500mg", "dosage": "1 tab", "frequency": "Daily", "duration": "5 days",
                                      "override": True, "override_reason": "Pre-treated with antihistamine; no alternative drug available."},
                                headers=self.auth_headers(self.vet_token))
        self.assertEqual(resp.status_code, 201)
        presc = resp.get_json()
        self.assertEqual(presc["allergy_override"], 1)

    # 5. Digital Sample Lifecycle & Chain of Custody
    def test_05_digital_sample_workflow(self):
        # Create sample
        resp = self.client.post("/api/samples",
                                json={"case_id": 1, "sample_type": "Nasal Swab", "collection_lat": 18.5204, "collection_lng": 73.8567, "is_manual_location": 0, "collection_notes": "Sterile swab from nasal cavity"},
                                headers=self.auth_headers(self.vet_token))
        self.assertEqual(resp.status_code, 201)
        s = resp.get_json()
        sid = s["id"]
        stoken = s["qr_token"]

        # Transport progression
        resp = self.client.post(f"/api/samples/{sid}/transport",
                                json={"status": "READY_FOR_PICKUP", "transporter_name": "Ramesh", "notes": "Packed in cold ice box"},
                                headers=self.auth_headers(self.vet_token))
        self.assertEqual(resp.status_code, 200)

        resp = self.client.post(f"/api/samples/{sid}/transport",
                                json={"status": "PICKED_UP", "transporter_name": "Ramesh", "transporter_phone": "9811223344"},
                                headers=self.auth_headers(self.vet_token))
        self.assertEqual(resp.status_code, 200)

        # Lookup sample by QR token
        resp = self.client.get(f"/api/samples/lookup-qr?token={stoken}", headers=self.auth_headers(self.lab_token))
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.get_json()["status"], "PICKED_UP")
        self.assertGreaterEqual(len(resp.get_json()["custody_events"]), 3)

        # Lab receives sample
        resp = self.client.post(f"/api/samples/{sid}/receive",
                                json={"action": "accept", "notes": "Cold chain verified at 4°C"},
                                headers=self.auth_headers(self.lab_token))
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.get_json()["status"], "LAB_RECEIVED")

        # Lab starts test
        resp = self.client.post(f"/api/samples/{sid}/test", headers=self.auth_headers(self.lab_token))
        self.assertEqual(resp.status_code, 200)

        # Lab enters results
        resp = self.client.post(f"/api/samples/{sid}/results",
                                json={"test_name": "FMD ELISA", "result": "NEGATIVE", "quantitative_result": 0.05, "units": "OD", "abnormal_flag": "Normal", "comments": "No antibodies detected"},
                                headers=self.auth_headers(self.lab_token))
        self.assertEqual(resp.status_code, 201)
        rep = resp.get_json()
        repid = rep["id"]

        # Lab verifies and publishes report -> triggers notification
        resp = self.client.post(f"/api/lab/reports/{repid}/verify", headers=self.auth_headers(self.lab_token))
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.get_json()["verification_status"], "VERIFIED")

        # Verify vet received automatic notification
        resp = self.client.get("/api/notifications", headers=self.auth_headers(self.vet_token))
        self.assertEqual(resp.status_code, 200)
        notes = resp.get_json()
        self.assertTrue(any("Lab Report" in n["message"] or "lab report" in n["message"].lower() for n in notes))

    # 6. Animal AI Decision Support
    def test_06_animal_ai_assessment(self):
        resp = self.client.get("/api/animals/1/ai-assessment", headers=self.auth_headers(self.vet_token))
        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        self.assertIn("risk_score", data)
        self.assertIn("abnormal_findings", data)
        self.assertIn("suggested_next_steps", data)
        self.assertIn("disclaimer", data)
        self.assertIn("veterinary confirmation required", data["disclaimer"])

    # 7. Treatment Response Tracking
    def test_07_treatment_response(self):
        resp = self.client.post("/api/cases/1/treatment-responses",
                                json={"response": "improved", "objective_observations": "Temp 101.2 F, appetite improved", "notes": "Continuing meloxicam"},
                                headers=self.auth_headers(self.vet_token))
        self.assertEqual(resp.status_code, 201)
        self.assertEqual(resp.get_json()["response"], "improved")

    # 8. Farm Intelligence & Alerts
    def test_08_farm_intelligence(self):
        resp = self.client.get("/api/herds/1/intelligence", headers=self.auth_headers(self.vet_token))
        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        self.assertIn("total_animals", data)
        self.assertIn("vaccination_coverage", data)
        self.assertIn("risk_level", data)

    # 9. Real Weather & Spatiotemporal Clusters
    def test_09_weather_and_clusters(self):
        # Weather
        resp = self.client.get("/api/weather/Pune", headers=self.auth_headers(self.vet_token))
        self.assertEqual(resp.status_code, 200)
        w = resp.get_json()
        self.assertIn("temperature", w)
        self.assertIn("source", w)

        # Clusters
        resp = self.client.get("/api/govt/clusters", headers=self.auth_headers(self.govt_token))
        self.assertEqual(resp.status_code, 200)
        cl = resp.get_json()
        self.assertIn("clusters", cl)

    # 10. National Surveillance
    def test_10_national_surveillance(self):
        resp = self.client.get("/api/national/surveillance", headers=self.auth_headers(self.govt_token))
        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        self.assertEqual(data["country"], "India")
        self.assertGreater(len(data["states"]), 0)

    # 11. Complete Traceability (Audit Logs)
    def test_11_audit_logs(self):
        resp = self.client.get("/api/audit-logs", headers=self.auth_headers(self.govt_token))
        self.assertEqual(resp.status_code, 200)
        logs = resp.get_json()
        self.assertGreater(len(logs), 0)

    # 12. Offline Synchronization
    def test_12_offline_sync(self):
        txn_id = f"test_txn_{uuid.uuid4().hex[:10]}"
        batch = {
            "items": [
                {
                    "client_txn_id": txn_id,
                    "action": "RECORD_TREATMENT",
                    "payload": {"case_id": 1, "animal_id": 1, "response": "improved", "notes": "Recorded while offline"}
                }
            ]
        }
        resp = self.client.post("/api/sync/queue", json=batch, headers=self.auth_headers(self.vet_token))
        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        self.assertEqual(data["synced_count"], 1)

        # Deduplication check
        resp2 = self.client.post("/api/sync/queue", json=batch, headers=self.auth_headers(self.vet_token))
        self.assertEqual(resp2.status_code, 200)
        self.assertEqual(resp2.get_json()["results"][0]["status"], "already_synced")

if __name__ == "__main__":
    unittest.main()
