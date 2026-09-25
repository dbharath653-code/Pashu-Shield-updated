"""Auth robustness: identifier tolerance (spaces/case) + registration normalisation.

Phone keyboards autocapitalise and append trailing spaces; login must not
fail for that. Registration stores normalised identity values so later
logins and duplicate checks behave consistently.
"""
import unittest
import uuid

import database
from app import app


class TestAuthTolerance(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        database.init_db()
        cls.client = app.test_client()

    def _drop_user(self, email):
        conn = database.get_db()
        row = conn.execute("SELECT id FROM users WHERE email=?", (email,)).fetchone()
        if row:
            conn.execute("DELETE FROM audit_events WHERE actor_id=?", (row["id"],))
            conn.execute("DELETE FROM users WHERE id=?", (row["id"],))
            conn.commit()
        conn.close()

    def test_01_register_normalises_identity(self):
        tag = uuid.uuid4().hex[:8]
        num = str(uuid.uuid4().int)[-5:]
        email = f"CaseUser-{tag}@Example.COM"
        digits = str(uuid.uuid4().int)[-5:]
        mobile_raw = f"98 76-{digits}"
        resp = self.client.post("/api/auth/register", json={
            "full_name": "  Case User  ", "mobile": mobile_raw, "email": f"  {email}  ",
            "password": "password123", "confirm_password": "password123",
            "role": "owner", "village": "X", "district": "Pune"})
        self.assertIn(resp.status_code, (200, 201))
        try:
            conn = database.get_db()
            row = conn.execute("SELECT email, mobile, full_name FROM users WHERE email=?",
                               (email.strip().lower(),)).fetchone()
            conn.close()
            self.assertIsNotNone(row)
            self.assertEqual(row["email"], email.strip().lower())
            self.assertNotIn(" ", row["mobile"])
            self.assertNotIn("-", row["mobile"])
            self.assertEqual(row["full_name"], "Case User")
        finally:
            self._drop_user(email.strip().lower())

    def test_02_duplicate_detected_across_case_and_format(self):
        tag = uuid.uuid4().hex[:8]
        num = str(uuid.uuid4().int)[-5:]
        base = {"full_name": "Dup User", "password": "password123",
                "confirm_password": "password123", "role": "owner",
                "village": "X", "district": "Pune"}
        r1 = self.client.post("/api/auth/register", json={
            **base, "mobile": f"98111{num}", "email": f"dup-{tag}@example.com"})
        self.assertIn(r1.status_code, (200, 201))
        try:
            r2 = self.client.post("/api/auth/register", json={
                **base, "mobile": f"98111{num}", "email": f"DUP-{tag}@EXAMPLE.com"})
            self.assertEqual(r2.status_code, 409)
            r3 = self.client.post("/api/auth/register", json={
                **base, "mobile": "98 111 " + num, "email": f"other-{tag}@example.com"})
            self.assertEqual(r3.status_code, 409)
        finally:
            self._drop_user(f"dup-{tag}@example.com")

    def test_03_login_tolerates_spaces_case_mobile_format(self):
        tag = uuid.uuid4().hex[:8]
        num = str(uuid.uuid4().int)[-5:]
        email = f"tol-{tag}@example.com"
        mobile = f"98222{num}"
        r = self.client.post("/api/auth/register", json={
            "full_name": "Tol User", "mobile": mobile, "email": email,
            "password": "password123", "confirm_password": "password123",
            "role": "vet", "village": "X", "district": "Pune"})
        self.assertIn(r.status_code, (200, 201))
        try:
            for ident in (f"  {email}  ", email.upper(),
                          f"{mobile[:5]} {mobile[5:]}", f"{mobile[:5]}-{mobile[5:]}",
                          mobile):
                resp = self.client.post("/api/auth/login",
                                        json={"identifier": ident, "password": "password123"})
                self.assertEqual(resp.status_code, 200, f"identifier {ident!r} rejected")
                self.assertEqual(resp.get_json()["user"]["role"], "vet")
            # Wrong password still rejected
            resp = self.client.post("/api/auth/login",
                                    json={"identifier": email, "password": "wrongpass"})
            self.assertEqual(resp.status_code, 401)
        finally:
            self._drop_user(email)

    def test_04_access_token_query_fallback(self):
        # Intermediaries that strip the Authorization header must not lock
        # users out: ?access_token= is accepted when the header is absent.
        tag = uuid.uuid4().hex[:8]
        email = f"q-{tag}@example.com"
        r = self.client.post("/api/auth/register", json={
            "full_name": "Q User", "mobile": f"98333{str(uuid.uuid4().int)[-5:]}",
            "email": email, "password": "password123", "confirm_password": "password123",
            "role": "owner", "village": "X", "district": "Pune"})
        self.assertIn(r.status_code, (200, 201))
        token = r.get_json()["token"]
        try:
            # Header form (preferred) still works
            resp = self.client.get("/api/owner/summary",
                                   headers={"Authorization": f"Bearer {token}"})
            self.assertEqual(resp.status_code, 200)
            # Query fallback works when the header is missing
            resp = self.client.get(f"/api/owner/summary?access_token={token}")
            self.assertEqual(resp.status_code, 200)
            # Neither -> 401 as before
            resp = self.client.get("/api/owner/summary")
            self.assertEqual(resp.status_code, 401)
            # Wrong query token -> 401 (not authenticated)
            resp = self.client.get("/api/owner/summary?access_token=bogus")
            self.assertEqual(resp.status_code, 401)
            # Role enforcement unchanged through the fallback
            resp = self.client.get(f"/api/vet/summary?access_token={token}")
            self.assertEqual(resp.status_code, 403)
        finally:
            self._drop_user(email)


if __name__ == "__main__":
    unittest.main()
