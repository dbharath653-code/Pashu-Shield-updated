"""
End-to-end tests for the IVR reporting channel.

Covers the 16 acceptance scenarios in the IVR specification plus regression
checks on the pre-existing web reporting flow.

Run:
    python -m unittest test_ivr -v

Notes
-----
* The webhook tests use a local test telephony provider. That provider only
  translates between the HTTP request and the same provider-neutral markup
  the real Twilio/Exotel/Plivo drivers produce - it places no calls and
  fabricates no data.
* Speech-to-text and LLM providers are left unconfigured, so the pipeline
  runs its evidence-based deterministic extractor. That is exactly the
  "never invent missing information" path and is asserted explicitly.
"""
import base64
import hashlib
import hmac
import json
import os
import tempfile
import unittest

# ---- isolate the database BEFORE importing the app -------------------------
_TMP_DB = os.path.join(tempfile.gettempdir(), "pashu_ivr_test.db")
os.environ["SIH_DB_PATH"] = _TMP_DB
os.environ["IVR_WORKER_ENABLED"] = "false"
os.environ["IVR_DISABLE_INLINE_WORKER"] = "1"
os.environ["IVR_REQUIRE_WEBHOOK_SIGNATURE"] = "false"
os.environ["IVR_PROVIDER"] = "twilio"
os.environ["IVR_PHONE_NUMBER"] = "+912200000000"
os.environ["TELEPHONY_PHONE_NUMBER"] = "+912200000000"
os.environ["TELEPHONY_ACCOUNT_ID"] = "ACtestaccountid0000000000000000000"
os.environ["TELEPHONY_AUTH_TOKEN"] = "test-authtoken"
os.environ["IVR_PUBLIC_BASE_URL"] = "https://ivr.example.org"
os.environ["IVR_SUPPORTED_LANGUAGES"] = "en,hi,te,mr"
os.environ["IVR_STT_PROVIDER"] = "none"
os.environ["IVR_AI_PROVIDER"] = "none"
os.environ["IVR_NOTIFY_GOV_USERS"] = "true"
os.environ["IVR_AUTO_ASSIGN_VET"] = "true"

from app import app, make_token  # noqa: E402
import database  # noqa: E402
from ivr import jobs, report_service, rules  # noqa: E402
from ivr import security as ivr_security  # noqa: E402
from ivr.config import Settings  # noqa: E402
from ivr.providers.base import TelephonyProvider  # noqa: E402
from ivr.service import IVRService  # noqa: E402
from ivr.survey import SurveyEngine, get_active_survey  # noqa: E402

if os.path.exists(_TMP_DB):
    os.remove(_TMP_DB)


class TestTelephonyProvider(TelephonyProvider):
    """Local driver used only by this test module (no network, no PSTN)."""

    name = "test"
    serializer_flavor = "twilio_xml"

    def configuration_errors(self):
        return []


PROVIDER = TestTelephonyProvider()


def twilio_signature(url, params, token):
    data = url + "".join(f"{k}{params[k]}" for k in sorted(params))
    return base64.b64encode(
        hmac.new(token.encode(), data.encode(), hashlib.sha1).digest()
    ).decode()


class IVRTestBase(unittest.TestCase):
    # Settings is a class-level singleton resolved at import time; pin the
    # values the test suite depends on (and restore them afterwards) so the
    # tests are independent of process env and test ordering.
    PINNED = {
        "IVR_PHONE_NUMBER": "+912200000000",
        "TELEPHONY_PHONE_NUMBER": "+912200000000",
        "TELEPHONY_ACCOUNT_ID": "ACtestaccountid0000000000000000000",
        "TELEPHONY_AUTH_TOKEN": "test-authtoken",
        "PUBLIC_BASE_URL": "https://ivr.example.org",
        "SUPPORTED_LANGUAGES": ["en", "hi", "te", "mr"],
        "DEFAULT_LANGUAGE": "en",
        "PROVIDER": "twilio",
        "STT_PROVIDER": "none",
        "AI_PROVIDER": "none",
        "REQUIRE_WEBHOOK_SIGNATURE": False,
        "RECORDING_ENABLED": False,
        "RECORDING_CONSENT_REQUIRED": True,
        "NOTIFY_GOV_USERS": True,
        "AUTO_ASSIGN_VET": True,
        "MAX_RETRIES": 2,
        "DUPLICATE_WINDOW_MINUTES": 1440,
        "DEV_MODE": False,
    }

    @classmethod
    def setUpClass(cls):
        from ivr.config import Settings as S
        cls._saved_settings = {k: getattr(S, k) for k in cls.PINNED}
        for k, v in cls.PINNED.items():
            setattr(S, k, v)
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

    def setUp(self):
        self.conn = database.get_db()
        # Every veterinarian on duty for the whole day (avoids clock flakiness)
        self.conn.execute("DELETE FROM ivr_vet_availability")
        self.conn.commit()
        rules.ensure_vet_availability(self.conn)
        self.conn.execute("UPDATE ivr_vet_availability SET is_available=1, available_from='00:00', "
                          "available_to='23:59', max_open_cases=100")
        self.conn.commit()

    def tearDown(self):
        try:
            self.conn.close()
        except Exception:
            pass

    @classmethod
    def tearDownClass(cls):
        from ivr.config import Settings as S
        for k, v in cls._saved_settings.items():
            setattr(S, k, v)

    # ------------------------------------------------------------ helpers --
    def headers(self, token=None):
        h = {"Content-Type": "application/x-www-form-urlencoded"}
        if token:
            h["Authorization"] = f"Bearer {token}"
        return h

    def api_headers(self, token):
        return {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}

    def service(self):
        return IVRService(PROVIDER, context={
            "notify": lambda conn, uid, msg, t="info": conn.execute(
                "INSERT INTO notifications (user_id, message, type) VALUES (?,?,?)", (uid, msg, t)),
            "audit_log": database.audit_log,
            "hash_password": database.hash_password,
        })

    def start_call(self, caller="+919800000042", call_sid=None):
        call_sid = call_sid or f"CA{os.urandom(6).hex()}"
        values = {"CallSid": call_sid, "From": caller, "To": "+912200000000",
                  "CallStatus": "ringing", "Direction": "inbound"}
        svc = self.service()
        ctype, body = svc.handle_incoming(self.conn, PROVIDER.parse_incoming(values), values)
        return call_sid, body, svc

    def send(self, call_sid, digits=None, speech=None, confidence=None, extra=None):
        values = {"CallSid": call_sid, "From": "+919800000042", "To": "+912200000000"}
        if digits is not None:
            values["Digits"] = digits
        if speech is not None:
            values["SpeechResult"] = speech
        if confidence is not None:
            values["Confidence"] = str(confidence)
        if extra:
            values.update(extra)
        svc = self.service()
        ctype, body = svc.handle_input(self.conn, PROVIDER.parse_gather(values), values)
        return body

    def run_jobs(self, limit=40):
        jobs.run_pending(self.conn, limit=limit, context={"notify": lambda conn, uid, msg, t="info": conn.execute(
            "INSERT INTO notifications (user_id, message, type) VALUES (?,?,?)", (uid, msg, t)),
            "audit_log": database.audit_log, "hash_password": database.hash_password,
            "provider": PROVIDER})

    def finish_call(self, call_sid, duration=90, status="completed"):
        values = {"CallSid": call_sid, "CallStatus": status, "CallDuration": str(duration)}
        svc = self.service()
        svc.handle_status(self.conn, PROVIDER.parse_status(values), values)
        self.run_jobs()

    def current_questions(self, answers=None):
        _, definition = get_active_survey(self.conn)
        engine = SurveyEngine(definition, "en")
        return engine.visible_questions(answers or {})


# ============================================================ TEST 1 - 16 ==
class TestIVRFlow(IVRTestBase):

    # ------------------------------------------------------------- TEST 1 --
    def test_01_call_is_answered_with_language_menu(self):
        call_sid, body, _ = self.start_call()
        self.assertIn("<Response", body)
        self.assertIn("<Gather", body)
        for snippet in ("English", "हिंदी", "తెలుగు", "मराठी"):
            self.assertIn(snippet, body)
        call = self.conn.execute("SELECT * FROM ivr_calls WHERE provider_call_id=?", (call_sid,)).fetchone()
        self.assertIsNotNone(call, "call row must be persisted")
        self.assertEqual(call["status"], "IN_PROGRESS")

    # ------------------------------------------------------------- TEST 2 --
    def test_02_language_selection_persists(self):
        call_sid, _, _ = self.start_call()
        body = self.send(call_sid, digits="2")  # Hindi
        self.assertIn("पशु चिकित्सक", body)
        call = self.conn.execute("SELECT * FROM ivr_calls WHERE provider_call_id=?", (call_sid,)).fetchone()
        self.assertEqual(call["language"], "hi")
        self.assertEqual(call["language_source"], "DTMF")
        # ...and continues in Hindi
        body = self.send(call_sid, digits="2")   # report a problem -> survey
        self.assertIn("पशु के बारे में", body)

    def test_02b_language_selection_by_speech(self):
        call_sid, _, _ = self.start_call()
        self.send(call_sid, speech="telugu", confidence="0.9")
        call = self.conn.execute("SELECT * FROM ivr_calls WHERE provider_call_id=?", (call_sid,)).fetchone()
        self.assertEqual(call["language"], "te")
        self.assertEqual(call["language_source"], "SPEECH")

    def test_02c_invalid_language_is_retried(self):
        call_sid, _, _ = self.start_call()
        body = self.send(call_sid, digits="7")
        self.assertIn("Sorry, that language is not available.", body)
        self.assertIn("For English, press 1.", body)   # menu repeated
        call = self.conn.execute("SELECT * FROM ivr_calls WHERE provider_call_id=?", (call_sid,)).fetchone()
        self.assertEqual(call["language_source"], "DEFAULT", "no language is locked in on invalid input")

    # ------------------------------------------------------------- TEST 3 --
    def test_03_available_veterinarian_is_connected(self):
        call_sid, _, _ = self.start_call()
        self.send(call_sid, digits="1")          # English
        body = self.send(call_sid, digits="1")   # Talk to a vet
        self.assertIn("<Dial", body)
        vet_mobiles = [v["mobile"] for v in self.conn.execute(
            "SELECT mobile FROM users WHERE role='vet'").fetchall()]
        dialled = body.split("<Number>")[1].split("</Number>")[0]
        self.assertIn(dialled.replace("+91", ""), [m.replace("+91", "") for m in vet_mobiles])
        call = self.conn.execute("SELECT * FROM ivr_calls WHERE provider_call_id=?", (call_sid,)).fetchone()
        self.assertEqual(call["flow"], "VET_CONNECT")
        self.assertIn(call["status"], ("VET_CONNECTING", "VET_CONNECTED"))
        events = [dict(r) for r in self.conn.execute(
            "SELECT * FROM ivr_events WHERE call_id=?", (call["id"],)).fetchall()]
        self.assertTrue(any(e["event_type"] == "VET_CONNECTING" for e in events))

    def test_03b_vet_call_produces_report_after_transcription(self):
        call_sid, _, _ = self.start_call()
        self.send(call_sid, digits="1")
        self.send(call_sid, digits="1")
        # farmer and vet talked; provider reports the bridged call ended
        values = {"CallSid": call_sid, "DialCallStatus": "completed", "DialCallDuration": "120"}
        svc = self.service()
        svc.handle_input(self.conn, PROVIDER.parse_gather(values), values)
        self.run_jobs()
        report = self.conn.execute(
            "SELECT r.* FROM ivr_reports r JOIN ivr_calls c ON c.id=r.call_id "
            "WHERE c.provider_call_id=?", (call_sid,)).fetchone()
        self.assertIsNotNone(report)
        self.assertEqual(report["flow"], "VET_CONNECT")
        self.assertEqual(report["status"], "VET_NOTIFIED")

    # ------------------------------------------------------------- TEST 4 --
    def test_04_no_veterinarian_falls_back_to_survey(self):
        self.conn.execute("UPDATE ivr_vet_availability SET is_available=0")
        self.conn.commit()
        call_sid, _, _ = self.start_call()
        self.send(call_sid, digits="1")
        body = self.send(call_sid, digits="1")
        self.assertIn("No veterinarian is available", body)
        self.assertNotIn("<Dial", body)
        # next request (no input) starts the automated survey
        body = self.send(call_sid)
        self.assertIn("Please say your name", body)
        events = [dict(r) for r in self.conn.execute(
            "SELECT event_type FROM ivr_events e JOIN ivr_calls c ON c.id=e.call_id "
            "WHERE c.provider_call_id=?", (call_sid,)).fetchall()]
        self.assertIn("VET_UNAVAILABLE", [e["event_type"] for e in events])

    # ------------------------------------------------------------- TEST 5 --
    def _answer_full_survey(self, call_sid, language_digit="1"):
        """Answer every question of the active survey with valid input.

        The question order and the conditional questions are taken from the
        live (administrator configurable) survey definition, and progress is
        tracked from what the IVR actually recorded - so the test keeps
        working when the survey is edited.
        """
        self.send(call_sid, digits=language_digit)   # language
        self.send(call_sid, digits="2")              # main menu: report a problem
        _, definition = get_active_survey(self.conn)
        engine = SurveyEngine(definition, "en")
        call = self.conn.execute("SELECT id FROM ivr_calls WHERE provider_call_id=?",
                                 (call_sid,)).fetchone()
        inputs = {
            "farmer_name": ("digits", "3"),            # skip the name
            "species": ("digits", "1"),                # cattle
            "animal_count": ("digits", "2"),
            "breed": ("digits", "3"),                  # don't know
            "age": ("digits", "4"),
            "sex": ("digits", "1"),                    # female
            "main_problem": ("digits", "1"),           # fever
            "symptoms": ("speech", "fever and not eating"),
            "duration": ("digits", "2"),
            "severity": ("digits", "2"),
            "eating": ("digits", "2"),                 # not eating
            "drinking": ("digits", "2"),               # not drinking
            "temperature": ("digits", "104"),
            "vaccination_status": ("digits", "3"),
            "previous_disease": ("digits", "2"),
            "treatment_given": ("digits", "2"),
            "treatment_detail": ("digits", "3"),
            "pregnancy_status": ("digits", "2"),       # not pregnant
            "other_animals_affected": ("digits", "1"),
            "village": ("speech", "Wagholi"),
            "district": ("speech", "Pune"),
            "state": ("digits", "1"),                  # Maharashtra
            "additional_notes": ("digits", "3"),       # finish
        }
        recorded = {}
        for _ in range(60):
            recorded = {r["question_key"]: r["normalized_value"] for r in self.conn.execute(
                "SELECT question_key, normalized_value FROM ivr_responses WHERE call_id=?",
                (call["id"],)).fetchall()}
            pending = [q for q in engine.visible_questions(recorded) if q["key"] not in recorded]
            if not pending:
                break
            question = pending[0]
            mode, value = inputs.get(question["key"], ("digits", "3"))
            if mode == "speech":
                body = self.send(call_sid, speech=value, confidence="0.9")
            else:
                body = self.send(call_sid, digits=value)
            if "Press 1 to confirm" in body:
                self.send(call_sid, digits="1")
        return recorded

    def test_05_completed_survey_generates_report_automatically(self):
        call_sid, _, _ = self.start_call()
        self._answer_full_survey(call_sid)
        self.finish_call(call_sid)

        report = self.conn.execute(
            "SELECT r.* FROM ivr_reports r JOIN ivr_calls c ON c.id=r.call_id "
            "WHERE c.provider_call_id=?", (call_sid,)).fetchone()
        self.assertIsNotNone(report, "a report must be created without manual entry")
        self.assertEqual(report["completion_state"], "COMPLETE")
        self.assertIn(report["status"], ("AI_SUMMARIZED", "VET_NOTIFIED"))
        self.assertEqual(report["urgency"], "HIGH")  # not eating + not drinking rule

        case = self.conn.execute("SELECT * FROM cases WHERE id=?", (report["case_id"],)).fetchone()
        self.assertEqual(case["reported_through"], "IVR")
        self.assertIn("IVR", case["description"])
        self.assertEqual(case["severity"], "High")

        structured = json.loads(report["structured_json"])
        self.assertEqual(structured["animal"]["species"], "cattle")
        self.assertEqual(structured["animal"]["count"], 2)

        # government + veterinarian were notified through the existing table
        notes = [dict(r) for r in self.conn.execute(
            "SELECT * FROM notifications WHERE message LIKE ?", (f"%{report['report_no']}%",)).fetchall()]
        self.assertGreaterEqual(len(notes), 2)

    # ------------------------------------------------------------- TEST 6 --
    def test_06_caller_number_captured_and_normalised(self):
        call_sid, _, _ = self.start_call(caller="9800000042")  # 10-digit Indian CLI
        call = self.conn.execute("SELECT * FROM ivr_calls WHERE provider_call_id=?", (call_sid,)).fetchone()
        self.assertEqual(call["caller_number_status"], "CAPTURED")
        self.assertEqual(call["caller_number_masked"], "+********0042")
        ivr_security_PHONE_ENCRYPTION_KEY = os.environ.get("IVR_PHONE_ENCRYPTION_KEY")
        if ivr_security_PHONE_ENCRYPTION_KEY:
            self.assertIsNotNone(call["caller_number_encrypted"])

    def test_06b_hidden_number_asks_for_callback_number(self):
        call_sid, _, _ = self.start_call(caller="anonymous")  # withheld caller id
        body = self.send(call_sid, digits="1")  # language
        self.assertIn("ten digit mobile number", body)
        call = self.conn.execute("SELECT * FROM ivr_calls WHERE provider_call_id=?", (call_sid,)).fetchone()
        self.assertNotEqual(call["caller_number_status"], "CAPTURED")
        self.send(call_sid, digits="9876543210")
        call = self.conn.execute("SELECT * FROM ivr_calls WHERE provider_call_id=?", (call_sid,)).fetchone()
        self.assertEqual(call["caller_number_status"], "USER_PROVIDED")
        self.assertEqual(call["caller_number_masked"], "+********3210")

    # ------------------------------------------------------------- TEST 7 --
    def test_07_location_is_never_fabricated(self):
        call_sid, _, _ = self.start_call()
        self._answer_full_survey(call_sid)
        self.finish_call(call_sid)
        report = self.conn.execute(
            "SELECT * FROM ivr_reports r JOIN ivr_calls c ON c.id=r.call_id "
            "WHERE c.provider_call_id=?", (call_sid,)).fetchone()
        self.assertEqual(report["location_source"], "FARMER_PROVIDED")
        self.assertIsNone(report["lat"])
        self.assertIsNone(report["lng"])
        self.assertEqual(report["district"], "Pune")

    def test_07b_gps_consent_link_captures_real_coordinates(self):
        call_sid, _, _ = self.start_call()
        call = self.conn.execute("SELECT * FROM ivr_calls WHERE provider_call_id=?", (call_sid,)).fetchone()
        from ivr import location as ivr_location
        link, token = ivr_location.create_consent_link(self.conn, call["id"])
        self.assertTrue(link.startswith("https://ivr.example.org/ivr/location/"))

        resp = self.client.post(f"/ivr/location/{token}",
                                json={"lat": 18.5793, "lng": 73.9787, "accuracy": 12.5, "consent": True})
        self.assertEqual(resp.status_code, 200)
        row = self.conn.execute(
            "SELECT * FROM ivr_location_captures WHERE call_id=? AND source='GPS'", (call["id"],)).fetchone()
        self.assertIsNotNone(row)
        self.assertAlmostEqual(row["lat"], 18.5793, places=3)

        # a tampered token is rejected
        bad = token[:-4] + "ffff"
        resp = self.client.post(f"/ivr/location/{bad}", json={"lat": 1, "lng": 1, "consent": True})
        self.assertEqual(resp.status_code, 400)

    # ------------------------------------------------------------- TEST 8 --
    def test_08_ai_summary_never_invents_missing_information(self):
        from ivr.ai_pipeline import summarise, validate_structure, SchemaError, ground

        transcript = "My cow has fever since two days and is not eating."
        result = summarise(transcript, language="en", call_id="CA-TEST")
        structured = result["structured"]
        self.assertIn("fever", " ".join(structured["symptoms"]).lower())
        # things that were never said must stay empty
        self.assertEqual(structured["animal"]["breed"], "Not provided")
        self.assertEqual(structured["vaccination_status"], "Not provided")
        self.assertEqual(structured["veterinarian_advice"], [])
        self.assertIsNone(structured["follow_up_required"])
        self.assertIn("veterinarian", result["disclaimer"].lower())

        # grounding removes anything not supported by the transcript
        hallucinated = validate_structure({
            "animal": {"species": "Camel"},
            "symptoms": ["fever", "teleportation"],
            "vaccination_status": "FMD done",
        })
        cleaned, removed = ground(hallucinated, transcript)
        self.assertEqual(cleaned["animal"]["species"], "Not provided")
        self.assertEqual(cleaned["symptoms"], ["fever"])
        self.assertEqual(cleaned["vaccination_status"], "Not provided")
        self.assertTrue(removed)

        # malformed output is rejected
        with self.assertRaises(SchemaError):
            validate_structure("not an object")

    # ------------------------------------------------------------- TEST 9 --
    def test_09_veterinarian_receives_notification(self):
        call_sid, _, _ = self.start_call()
        self._answer_full_survey(call_sid)
        self.finish_call(call_sid)
        report = self.conn.execute(
            "SELECT * FROM ivr_reports r JOIN ivr_calls c ON c.id=r.call_id "
            "WHERE c.provider_call_id=?", (call_sid,)).fetchone()
        self.assertIsNotNone(report["notified_vet_at"])
        self.assertIsNotNone(report["assigned_vet_id"])
        note = self.conn.execute(
            "SELECT * FROM notifications WHERE user_id=? AND message LIKE ?",
            (report["assigned_vet_id"], f"%{report['report_no']}%")).fetchone()
        self.assertIsNotNone(note)
        for field in ("IVR", "Urgency", "Location"):
            self.assertIn(field, note["message"])

    # ------------------------------------------------------------ TEST 10 --
    def test_10_government_portal_visibility_and_rbac(self):
        call_sid, _, _ = self.start_call()
        self._answer_full_survey(call_sid)
        self.finish_call(call_sid)

        resp = self.client.get("/api/ivr/reports", headers=self.api_headers(self.govt_token))
        self.assertEqual(resp.status_code, 200)
        self.assertGreaterEqual(len(resp.get_json()), 1)
        item = resp.get_json()[0]
        for field in ("report_no", "urgency", "district", "status", "language", "caller_number_masked"):
            self.assertIn(field, item)

        resp = self.client.get("/api/ivr/analytics", headers=self.api_headers(self.govt_token))
        self.assertEqual(resp.status_code, 200)
        analytics = resp.get_json()
        self.assertGreaterEqual(analytics["totals"]["reports"], 1)
        self.assertTrue(analytics["reports_by_district"])
        self.assertTrue(analytics["problem_categories"])
        self.assertTrue(analytics["location_sources"])

        resp = self.client.get("/api/ivr/analytics", headers=self.api_headers(self.vet_token))
        self.assertEqual(resp.status_code, 200)

    # ------------------------------------------------------------ TEST 11 --
    def test_11_disconnection_preserves_partial_data(self):
        call_sid, _, _ = self.start_call()
        self.send(call_sid, digits="1")            # language
        self.send(call_sid, digits="2")            # survey
        self.send(call_sid, digits="3")            # farmer name: don't know
        self.send(call_sid, digits="1")            # species = cattle
        self.send(call_sid, digits="1")            # confirm
        # farmer hangs up in the middle of the survey
        self.finish_call(call_sid, duration=25)

        report = self.conn.execute(
            "SELECT * FROM ivr_reports r JOIN ivr_calls c ON c.id=r.call_id "
            "WHERE c.provider_call_id=?", (call_sid,)).fetchone()
        self.assertIsNotNone(report, "partial data must still produce a report")
        self.assertEqual(report["completion_state"], "PARTIALLY_COMPLETED")
        missing = json.loads(report["missing_fields"] or "[]")
        self.assertTrue(missing)
        self.assertIn("main_problem", missing)
        structured = json.loads(report["structured_json"])
        self.assertEqual(structured["animal"]["species"], "cattle")
        self.assertEqual(structured["location"]["source"], "NOT_AVAILABLE")

    # ------------------------------------------------------------ TEST 12 --
    def test_12_speech_failure_falls_back_to_dtmf(self):
        call_sid, _, _ = self.start_call()
        self.send(call_sid, digits="1")   # language
        self.send(call_sid, digits="2")   # survey
        # low confidence speech must not be accepted
        body = self.send(call_sid, speech="mmm", confidence="0.2")
        self.assertIn("That answer was not understood", body)
        rows = self.conn.execute(
            "SELECT * FROM ivr_responses r JOIN ivr_calls c ON c.id=r.call_id "
            "WHERE c.provider_call_id=?", (call_sid,)).fetchall()
        self.assertEqual(len(rows), 0, "low confidence speech must not be stored as an answer")
        # DTMF fallback works: skip the name question, then answer species
        self.send(call_sid, digits="3")
        body = self.send(call_sid, digits="1")
        self.assertIn("Press 1 to confirm", body)
        self.send(call_sid, digits="1")
        rows = self.conn.execute(
            "SELECT * FROM ivr_responses r JOIN ivr_calls c ON c.id=r.call_id "
            "WHERE c.provider_call_id=? AND r.question_key='species'", (call_sid,)).fetchall()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["normalized_value"], "cattle")

    def test_12b_repeat_and_back_navigation(self):
        call_sid, _, _ = self.start_call()
        self.send(call_sid, digits="1")
        self.send(call_sid, digits="2")
        self.send(call_sid, digits="3")          # farmer_name -> don't know
        body = self.send(call_sid, digits="9")   # repeat the species question
        self.assertIn("Which animal is sick", body)
        self.send(call_sid, digits="1")          # cattle
        self.send(call_sid, digits="1")          # confirm
        body = self.send(call_sid, digits="0")   # go back to the species question
        self.assertIn("Which animal is sick", body)

    # ------------------------------------------------------------ TEST 13 --
    def test_13_repeated_webhook_does_not_create_duplicate(self):
        from ivr.config import Settings as S
        saved = S.REQUIRE_WEBHOOK_SIGNATURE
        S.REQUIRE_WEBHOOK_SIGNATURE = True
        try:
            call_sid = f"CA{os.urandom(6).hex()}"
            payload = {"CallSid": call_sid, "From": "+919800000077", "To": "+912200000000",
                       "CallStatus": "ringing"}
            url = "https://ivr.example.org/ivr/voice"
            sig = twilio_signature(url, payload, os.environ["TELEPHONY_AUTH_TOKEN"])
            first = self.client.post("/ivr/voice", data=payload,
                                     headers={"X-Twilio-Signature": sig})
            self.assertEqual(first.status_code, 200)

            # same provider event delivered twice (identical params)
            second = self.client.post("/ivr/voice", data=payload,
                                      headers={"X-Twilio-Signature": sig})
            self.assertEqual(second.status_code, 200)
            calls = self.conn.execute("SELECT COUNT(*) c FROM ivr_calls WHERE provider_call_id=?",
                                      (call_sid,)).fetchone()["c"]
            self.assertEqual(calls, 1)

            # forged signature is rejected
            forged = self.client.post("/ivr/voice", data=payload,
                                      headers={"X-Twilio-Signature": "AAAA"})
            self.assertEqual(forged.status_code, 403)
        finally:
            S.REQUIRE_WEBHOOK_SIGNATURE = saved

    def test_13b_duplicate_report_is_flagged_not_discarded(self):
        call_sid, _, _ = self.start_call(caller="+919800000099")
        self._answer_full_survey(call_sid)
        self.finish_call(call_sid)
        first = self.conn.execute(
            "SELECT * FROM ivr_reports r JOIN ivr_calls c ON c.id=r.call_id "
            "WHERE c.provider_call_id=?", (call_sid,)).fetchone()

        call_sid2, _, _ = self.start_call(caller="+919800000099")
        self._answer_full_survey(call_sid2)
        self.finish_call(call_sid2)
        second = self.conn.execute(
            "SELECT * FROM ivr_reports r JOIN ivr_calls c ON c.id=r.call_id "
            "WHERE c.provider_call_id=?", (call_sid2,)).fetchone()

        self.assertIsNotNone(second, "duplicate reports are flagged, never discarded")
        self.assertEqual(second["is_duplicate"], 1)
        self.assertEqual(second["duplicate_of"], first["id"])
        self.assertIn("same caller", second["duplicate_reasons"])

        # authorised user can merge
        resp = self.client.post(f"/api/ivr/reports/{second['id']}/merge",
                                headers=self.api_headers(self.govt_token),
                                json={"target_report_id": first["id"]})
        self.assertEqual(resp.status_code, 200)

    # ------------------------------------------------------------ TEST 14 --
    def test_14_unauthorized_access_is_denied(self):
        self.assertEqual(self.client.get("/api/ivr/reports").status_code, 401)
        resp = self.client.get("/api/ivr/reports", headers=self.api_headers(self.lab_token))
        self.assertEqual(resp.status_code, 403)
        resp = self.client.get("/api/ivr/config", headers=self.api_headers(self.vet_token))
        self.assertEqual(resp.status_code, 403)
        resp = self.client.get("/api/ivr/config", headers=self.api_headers(self.govt_token))
        self.assertEqual(resp.status_code, 200)
        resp = self.client.put("/api/ivr/survey", headers=self.api_headers(self.vet_token), json={})
        self.assertEqual(resp.status_code, 403)
        # dev simulation stays off unless explicitly enabled
        resp = self.client.post("/api/ivr/dev/simulate", json={"inputs": []})
        self.assertEqual(resp.status_code, 403)
        # IVR case panel honours the same ownership rules as the case endpoint
        case_row = self.conn.execute(
            "SELECT r.case_id, r.owner_user_id FROM ivr_reports r WHERE r.case_id IS NOT NULL LIMIT 1").fetchone()
        if case_row:
            other = self.conn.execute(
                "SELECT * FROM users WHERE role='owner' AND id != ? LIMIT 1",
                (case_row["owner_user_id"],)).fetchone()
            if other:
                other_token = make_token(dict(other))
                resp = self.client.get(f"/api/ivr/case/{case_row['case_id']}",
                                       headers=self.api_headers(other_token))
                self.assertEqual(resp.status_code, 200)
                self.assertIsNone(resp.get_json()["report"], "other owners must not see the IVR report")

    def test_14b_config_and_survey_are_administrable(self):
        resp = self.client.put("/api/ivr/config", headers=self.api_headers(self.govt_token),
                               json={"IVR_DUPLICATE_WINDOW_MINUTES": 720})
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(Settings.DUPLICATE_WINDOW_MINUTES, 720)

        resp = self.client.get("/api/ivr/survey", headers=self.api_headers(self.govt_token))
        self.assertEqual(resp.status_code, 200)
        definition = resp.get_json()["definition"]
        definition["questions"] = definition["questions"][:2]
        resp = self.client.put("/api/ivr/survey", headers=self.api_headers(self.govt_token),
                               json={"definition": definition})
        self.assertEqual(resp.status_code, 200)
        _, updated = get_active_survey(self.conn)
        self.assertEqual(len(updated["questions"]), 2)
        # restore the full survey for the remaining tests
        from ivr.default_survey import DEFAULT_SURVEY
        from ivr import survey as survey_mod
        survey_mod.save_survey(self.conn, DEFAULT_SURVEY)

    # ------------------------------------------------------------ TEST 15 --
    def test_15_existing_web_reporting_still_works(self):
        animal = self.conn.execute(
            "SELECT * FROM animals WHERE owner_id=? LIMIT 1", (self.owner["id"],)).fetchone()
        resp = self.client.post("/api/cases", headers=self.api_headers(self.owner_token), json={
            "animal_id": animal["id"],
            "symptoms": "Fever, reduced eating",
            "severity": "Medium",
            "description": "Reported from the web app",
        })
        self.assertEqual(resp.status_code, 201)
        case = resp.get_json()
        self.assertEqual(case["reported_through"], "Mobile App")

        resp = self.client.get("/api/cases", headers=self.api_headers(self.owner_token))
        self.assertEqual(resp.status_code, 200)
        self.assertGreaterEqual(len(resp.get_json()), 1)

        # the legacy IVR intake endpoint from the previous build still works
        resp = self.client.post("/api/ivr/report", json={
            "mobile": "+919800001234", "transcript": "cow has fever", "district": "Pune"})
        self.assertIn(resp.status_code, (200, 201))

    # ------------------------------------------------------------ TEST 16 --
    def test_16_no_regression_in_existing_features(self):
        checks = [
            ("/api/users/me", self.owner_token),
            ("/api/animals", self.owner_token),
            ("/api/herds", self.owner_token),
            ("/api/vets", self.vet_token),
            ("/api/notifications", self.owner_token),
            ("/api/owner/summary", self.owner_token),
            ("/api/vet/summary", self.vet_token),
            ("/api/govt/analytics", self.govt_token),
            ("/api/govt/geo", self.govt_token),
            ("/api/lab/queue", self.lab_token),
            ("/api/campaigns", self.vet_token),
            ("/api/farm-alerts", self.vet_token),
            ("/api/national/surveillance", self.govt_token),
            ("/api/audit-logs", self.govt_token),
            ("/api/diseases", self.vet_token),
        ]
        for path, token in checks:
            with self.subTest(path=path):
                resp = self.client.get(path, headers=self.api_headers(token))
                self.assertEqual(resp.status_code, 200, f"{path} failed")
        # IVR cases must show up in the normal case list / detail
        resp = self.client.get("/api/cases", headers=self.api_headers(self.vet_token))
        self.assertEqual(resp.status_code, 200)
        ivr_cases = [c for c in resp.get_json() if c.get("reported_through") == "IVR"]
        self.assertTrue(ivr_cases, "IVR cases must be visible in the existing case workflow")
        report_row = self.conn.execute(
            "SELECT case_id FROM ivr_reports WHERE case_id IS NOT NULL ORDER BY id DESC LIMIT 1").fetchone()
        self.assertIsNotNone(report_row)
        case_id = report_row["case_id"]
        detail = self.client.get(f"/api/cases/{case_id}",
                                 headers=self.api_headers(self.vet_token)).get_json()
        self.assertEqual(detail["reported_through"], "IVR")
        panel = self.client.get(f"/api/ivr/case/{case_id}",
                                headers=self.api_headers(self.vet_token)).get_json()
        self.assertIsNotNone(panel["report"])
        self.assertEqual(panel["report"]["case_id"], case_id)

    # --------------------------------------------------------- extra tests --
    def test_17_phone_security_helpers(self):
        self.assertEqual(ivr_security.normalize_phone("9800000042")[0], "+919800000042")
        self.assertEqual(ivr_security.normalize_phone("+91 98000 00042")[0], "+919800000042")
        self.assertEqual(ivr_security.normalize_phone("anonymous")[1], "WITHHELD")
        self.assertEqual(ivr_security.mask_phone("+919800000042"), "+********0042")
        self.assertIn(ivr_security.encryption_mode(), ("fernet", "hash_only"))
        token = ivr_security.sign_token({"a": 1})
        self.assertEqual(ivr_security.verify_token(token)["a"], 1)
        self.assertIsNone(ivr_security.verify_token(token[:-3] + "abc"))

    def test_18_urgency_rules_are_transparent(self):
        urgency, fired, _ = rules.evaluate_urgency({"symptoms": ["death"]}, "two animals died")
        self.assertEqual(urgency, "CRITICAL")
        self.assertIn("DEATH_REPORTED", fired)
        urgency, fired, _ = rules.evaluate_urgency({"animal": {"count": 1}}, "mild cough")
        self.assertIn(urgency, ("LOW", "MEDIUM"))

    def test_19_report_status_lifecycle(self):
        call_sid, _, _ = self.start_call()
        self._answer_full_survey(call_sid)
        self.finish_call(call_sid)
        report = self.conn.execute(
            "SELECT * FROM ivr_reports r JOIN ivr_calls c ON c.id=r.call_id "
            "WHERE c.provider_call_id=?", (call_sid,)).fetchone()
        resp = self.client.post(f"/api/ivr/reports/{report['id']}/status",
                                headers=self.api_headers(self.vet_token),
                                json={"status": "UNDER_REVIEW", "note": "reviewing"})
        self.assertEqual(resp.status_code, 200)
        case = self.conn.execute("SELECT * FROM cases WHERE id=?", (report["case_id"],)).fetchone()
        self.assertEqual(case["status"], "UNDER INVESTIGATION")

        resp = self.client.post(f"/api/ivr/reports/{report['id']}/verify",
                                headers=self.api_headers(self.vet_token), json={"note": "checked"})
        self.assertEqual(resp.status_code, 200)
        report = self.conn.execute("SELECT * FROM ivr_reports WHERE id=?", (report["id"],)).fetchone()
        self.assertEqual(report["ai_verified_by_vet"], 1)

    def test_20_unconfigured_provider_does_not_fake_calls(self):
        """With no credentials, no provider claims to be ready and no call is placed."""
        from ivr.config import settings as ivr_settings
        from ivr.providers import TwilioProvider, UnconfiguredProvider
        unconfigured = UnconfiguredProvider()
        self.assertFalse(unconfigured.is_configured())
        self.assertTrue(unconfigured.configuration_errors())
        with self.assertRaises(Exception):
            unconfigured.render(unconfigured.response())

        keys = ("TELEPHONY_ACCOUNT_ID", "TELEPHONY_AUTH_TOKEN", "TELEPHONY_PHONE_NUMBER",
                "IVR_PHONE_NUMBER")
        saved = {k: getattr(ivr_settings, k) for k in keys}
        for k in keys:
            setattr(ivr_settings, k, "")
        try:
            errors = TwilioProvider().configuration_errors()
            self.assertTrue(errors, "missing credentials must be reported")
            self.assertIn("TELEPHONY_ACCOUNT_ID", "; ".join(errors))
        finally:
            for k, v in saved.items():
                setattr(ivr_settings, k, v)

    def test_21_stt_unavailable_is_reported_not_faked(self):
        from ivr import stt
        self.assertFalse(stt.is_configured())
        self.assertIn("IVR_STT_PROVIDER", stt.unavailability_reason())
        with self.assertRaises(stt.STTUnavailable):
            stt.transcribe(audio_bytes=b"123", language="en")


if __name__ == "__main__":
    unittest.main(verbosity=2)
