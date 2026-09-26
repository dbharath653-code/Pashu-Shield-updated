"""Regression tests for the case-based GPS, laboratory and notification flows."""
import os
import unittest
from unittest.mock import patch

import database
from app import app, make_token
from notifications import SMSProvider


class TestNewWorkflows(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        database.init_db()
        cls.client = app.test_client()
        conn = database.get_db()
        cls.owner = dict(conn.execute("SELECT * FROM users WHERE email='rajesh@example.com'").fetchone())
        cls.vet = dict(conn.execute("SELECT * FROM users WHERE email='vet1@example.com'").fetchone())
        cls.lab = dict(conn.execute("SELECT * FROM users WHERE role='lab' LIMIT 1").fetchone())
        cls.govt = dict(conn.execute("SELECT * FROM users WHERE role='govt' LIMIT 1").fetchone())
        conn.close()

    def auth(self, user):
        return {"Authorization": f"Bearer {make_token(user)}", "Content-Type": "application/json"}

    def test_01_missing_sms_is_not_success(self):
        with patch.dict(os.environ, {"SMS_PROVIDER": "", "SMS_BASE_URL": "", "SMS_API_KEY": "", "SMS_SENDER_ID": ""}, clear=False):
            result = SMSProvider().send(to="9800000001", message="test", reference="test-missing-provider")
        self.assertEqual(result.status, "NOT_CONFIGURED")

    def test_02_farmer_location_can_be_unavailable_without_coordinates(self):
        response = self.client.post("/api/cases/1/location", json={"source": "NOT_AVAILABLE", "village": "Wagholi", "district": "Pune"}, headers=self.auth(self.owner))
        self.assertEqual(response.status_code, 201)
        self.assertIsNone(response.get_json()["latitude"])
        self.assertIsNone(response.get_json()["longitude"])

    def test_03_vet_tracking_accepts_only_real_device_locations(self):
        response = self.client.post("/api/cases/1/visit", json={}, headers=self.auth(self.vet))
        self.assertIn(response.status_code, (200, 201))
        visit_id = response.get_json()["id"]
        self.assertEqual(self.client.post(f"/api/visits/{visit_id}/start-tracking", json={}, headers=self.auth(self.vet)).status_code, 201)
        self.assertEqual(self.client.post(f"/api/visits/{visit_id}/location", json={"latitude": 18.52, "longitude": 73.85, "accuracy": 8, "idempotency_key": "new-workflow-gps-1"}, headers=self.auth(self.vet)).status_code, 201)
        farmer_view = self.client.get("/api/cases/1/track", headers=self.auth(self.owner))
        self.assertEqual(farmer_view.status_code, 200)
        self.assertEqual(farmer_view.get_json()["location_status"], "AVAILABLE")
        self.assertEqual(self.client.get("/api/cases/1/track", headers=self.auth(self.lab)).status_code, 403)
        self.assertEqual(self.client.post(f"/api/visits/{visit_id}/stop-tracking", json={}, headers=self.auth(self.vet)).status_code, 200)

    def test_04_vet_lab_report_endpoint_and_events(self):
        response = self.client.get("/api/veterinarian/lab-reports", headers=self.auth(self.vet))
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.get_json())
        self.assertIn("current_status", response.get_json()[0])
        events = self.client.get("/api/realtime/events", headers=self.auth(self.vet))
        self.assertEqual(events.status_code, 200)
        self.assertTrue(any(e["event_type"] in ("LAB_REPORT_SUBMITTED", "LAB_SAMPLE_COLLECTED", "TRACKING_STARTED") for e in events.get_json()))

    def test_05_realtime_stream_requires_short_lived_stream_token(self):
        token_response = self.client.get("/api/realtime/token", headers=self.auth(self.vet))
        self.assertEqual(token_response.status_code, 200)
        stream_token = token_response.get_json()["token"]
        response = self.client.get("/api/realtime/stream", query_string={"access_token": make_token(self.vet)})
        self.assertEqual(response.status_code, 401)
        response = self.client.get("/api/realtime/stream", query_string={"access_token": stream_token}, buffered=False)
        self.assertEqual(response.status_code, 200)
        response.close()


if __name__ == "__main__":
    unittest.main()
