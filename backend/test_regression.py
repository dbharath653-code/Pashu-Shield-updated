import unittest
from app import app, make_token
import database

class TestRegressionExistingFeatures(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        database.init_db()
        cls.client = app.test_client()
        conn = database.get_db()
        cls.owner = dict(conn.execute("SELECT * FROM users WHERE role='owner' LIMIT 1").fetchone())
        cls.vet = dict(conn.execute("SELECT * FROM users WHERE role='vet' LIMIT 1").fetchone())
        cls.govt = dict(conn.execute("SELECT * FROM users WHERE role='govt' LIMIT 1").fetchone())
        conn.close()

        cls.owner_token = make_token(cls.owner)
        cls.vet_token = make_token(cls.vet)
        cls.govt_token = make_token(cls.govt)

    def auth_headers(self, token):
        return {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}

    def test_r01_auth_me(self):
        resp = self.client.get("/api/users/me", headers=self.auth_headers(self.owner_token))
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.get_json()["email"], self.owner["email"])

    def test_r02_herds(self):
        resp = self.client.get("/api/herds", headers=self.auth_headers(self.owner_token))
        self.assertEqual(resp.status_code, 200)
        self.assertIsInstance(resp.get_json(), list)

    def test_r03_animals(self):
        resp = self.client.get("/api/animals", headers=self.auth_headers(self.owner_token))
        self.assertEqual(resp.status_code, 200)
        self.assertGreater(len(resp.get_json()), 0)

    def test_r04_cases(self):
        resp = self.client.get("/api/cases", headers=self.auth_headers(self.vet_token))
        self.assertEqual(resp.status_code, 200)
        cases = resp.get_json()
        self.assertGreater(len(cases), 0)

        c1 = cases[0]
        resp_detail = self.client.get(f"/api/cases/{c1['id']}", headers=self.auth_headers(self.vet_token))
        self.assertEqual(resp_detail.status_code, 200)

    def test_r05_lab_reports(self):
        resp = self.client.get("/api/lab/reports", headers=self.auth_headers(self.vet_token))
        self.assertEqual(resp.status_code, 200)
        self.assertIsInstance(resp.get_json(), list)

    def test_r06_prescriptions(self):
        resp = self.client.get("/api/prescriptions", headers=self.auth_headers(self.owner_token))
        self.assertEqual(resp.status_code, 200)
        self.assertIsInstance(resp.get_json(), list)

    def test_r07_notifications(self):
        resp = self.client.get("/api/notifications", headers=self.auth_headers(self.owner_token))
        self.assertEqual(resp.status_code, 200)
        notes = resp.get_json()
        if notes:
            nid = notes[0]["id"]
            resp_read = self.client.put(f"/api/notifications/{nid}/read", headers=self.auth_headers(self.owner_token))
            self.assertEqual(resp_read.status_code, 200)

    def test_r08_summaries(self):
        resp_o = self.client.get("/api/owner/summary", headers=self.auth_headers(self.owner_token))
        self.assertEqual(resp_o.status_code, 200)
        self.assertIn("animals", resp_o.get_json())

        resp_v = self.client.get("/api/vet/summary", headers=self.auth_headers(self.vet_token))
        self.assertEqual(resp_v.status_code, 200)
        self.assertIn("new_cases", resp_v.get_json())

    def test_r09_govt_analytics_and_geo(self):
        resp_a = self.client.get("/api/govt/analytics", headers=self.auth_headers(self.govt_token))
        self.assertEqual(resp_a.status_code, 200)
        self.assertIn("totals", resp_a.get_json())

        resp_g = self.client.get("/api/govt/geo", headers=self.auth_headers(self.govt_token))
        self.assertEqual(resp_g.status_code, 200)
        self.assertIsInstance(resp_g.get_json(), list)

    def test_r10_campaigns_and_diseases(self):
        resp_c = self.client.get("/api/campaigns", headers=self.auth_headers(self.vet_token))
        self.assertEqual(resp_c.status_code, 200)

        resp_d = self.client.get("/api/diseases", headers=self.auth_headers(self.vet_token))
        self.assertEqual(resp_d.status_code, 200)
        self.assertGreater(len(resp_d.get_json()), 0)

if __name__ == "__main__":
    unittest.main()
