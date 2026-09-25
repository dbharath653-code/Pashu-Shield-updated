"""
Tests for the self-hosted SIP/PBX voice gateway (pbx/) and its backend side.

Covers: SIPProvider contract + strict auth, gateway endpoints
(heartbeat / authorize-dial), health-signal honesty (nothing can fake
pstn_connected), vet-leg status honesty, and the AGI TwiML interpreter
(imported from pbx/agi/pashu_ivr.py - stdlib only, no Asterisk needed).

Nothing here touches a real phone network. Gateway state is always cleaned
up so the shared dev DB keeps honestly reporting pstn_connected=false.
"""
import hashlib
import hmac
import importlib.util
import json
import os
import sys
import unittest

import database
from app import app

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
AGI_PATH = os.path.join(REPO_ROOT, "pbx", "agi", "pashu_ivr.py")
RENDER_PATH = os.path.join(REPO_ROOT, "pbx", "render.py")

TEST_SECRET = "test-gateway-secret-0123456789abcdef"


def load_module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


pashu_ivr = load_module("pashu_ivr", AGI_PATH)
render_py = load_module("pbx_render", RENDER_PATH)


class FakeHeaders(dict):
    def get(self, k, default=None):
        return super().get(k, default)


class FakeRequest:
    def __init__(self, headers=None, body=""):
        self.headers = FakeHeaders(headers or {})
        self._body = body

    def get_data(self, as_text=False):
        return self._body if as_text else self._body.encode()


def hmac_sig(secret, body):
    return hmac.new(secret.encode(), body.encode(), hashlib.sha256).hexdigest()


class TestSIPProvider(unittest.TestCase):
    def test_01_factory_returns_sip_provider(self):
        import ivr.config as cfg
        from ivr.telephony import get_telephony_provider
        from ivr.telephony.sip_provider import SIPProvider
        from ivr.telephony.mock_provider import MockTelephonyProvider
        old = cfg.TELEPHONY_PROVIDER
        try:
            cfg.TELEPHONY_PROVIDER = "sip"
            self.assertIsInstance(get_telephony_provider(), SIPProvider)
            cfg.TELEPHONY_PROVIDER = "mock"
            self.assertIsInstance(get_telephony_provider(), MockTelephonyProvider)
        finally:
            cfg.TELEPHONY_PROVIDER = old

    def test_02_sip_generates_same_twiml(self):
        from ivr.telephony.sip_provider import SIPProvider
        p = SIPProvider("secret")
        welcome = p.generate_welcome_twiml("SIP-1", "en")
        menu = p.generate_menu_twiml("SIP-1", "te")
        dial = p.generate_connect_vet_twiml("SIP-1", "+919800000001", "hi")
        self.assertIn("<Response>", welcome)
        self.assertIn("Gather", menu)
        self.assertIn("<Dial", dial)
        self.assertIn("+919800000001", dial)
        # Consent + unavailable helpers exist for the real transport path
        self.assertIn("Gather", p.generate_recording_consent_twiml("SIP-1", "en"))
        self.assertIn("survey/start", p.generate_vet_unavailable_twiml("SIP-1", "en"))

    def test_03_strict_signature_no_secret_fails_closed(self):
        from ivr.telephony.sip_provider import SIPProvider
        old = os.environ.get("PBX_WEBHOOK_SECRET", None)
        try:
            if "PBX_WEBHOOK_SECRET" in os.environ:
                del os.environ["PBX_WEBHOOK_SECRET"]
            if "TELEPHONY_WEBHOOK_SECRET" in os.environ:
                del os.environ["TELEPHONY_WEBHOOK_SECRET"]
            p = SIPProvider("")
            # Even the mock bypass header must NOT work here
            self.assertFalse(p.verify_webhook_signature(
                FakeRequest({"X-Mock-Bypass": "true"})))
            self.assertFalse(p.verify_webhook_signature(FakeRequest()))
        finally:
            if old is not None:
                os.environ["PBX_WEBHOOK_SECRET"] = old

    def test_04_signature_accepts_secret_and_hmac_only(self):
        from ivr.telephony.sip_provider import SIPProvider
        p = SIPProvider(TEST_SECRET)
        self.assertTrue(p.verify_webhook_signature(
            FakeRequest({"X-PBX-Secret": TEST_SECRET})))
        self.assertFalse(p.verify_webhook_signature(
            FakeRequest({"X-PBX-Secret": "wrong"})))
        body = "CallSid=SIP-1&Digits=1"
        self.assertTrue(p.verify_webhook_signature(
            FakeRequest({"X-Webhook-Signature": hmac_sig(TEST_SECRET, body)}, body)))
        self.assertFalse(p.verify_webhook_signature(
            FakeRequest({"X-Webhook-Signature": "deadbeef"}, body)))
        self.assertFalse(p.verify_webhook_signature(FakeRequest()))

    def test_05_outbound_disabled_inbound_only(self):
        from ivr.telephony.sip_provider import SIPProvider
        r = SIPProvider(TEST_SECRET).initiate_outbound_call("+919800000001", "7382210251", "http://x")
        self.assertEqual(r["status"], "unsupported")
        self.assertIn("inbound", r["reason"].lower())
        self.assertIsNone(SIPProvider(TEST_SECRET).get_recording_url("SIP-1"))


class TestGatewayEndpoints(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        database.init_db()
        cls.client = app.test_client()
        cls._old_secret = os.environ.get("PBX_WEBHOOK_SECRET")
        cls._old_ips = os.environ.get("PBX_ALLOWED_IPS")
        os.environ["PBX_WEBHOOK_SECRET"] = TEST_SECRET
        if "PBX_ALLOWED_IPS" in os.environ:
            del os.environ["PBX_ALLOWED_IPS"]
        conn = database.get_db()
        vets = conn.execute("SELECT * FROM users WHERE role='vet' ORDER BY id LIMIT 2").fetchall()
        conn.close()
        assert len(vets) >= 1, "seed vets required"
        cls.vet = dict(vets[0])

    @classmethod
    def tearDownClass(cls):
        if cls._old_secret is None:
            os.environ.pop("PBX_WEBHOOK_SECRET", None)
        else:
            os.environ["PBX_WEBHOOK_SECRET"] = cls._old_secret
        if cls._old_ips is None:
            os.environ.pop("PBX_ALLOWED_IPS", None)
        else:
            os.environ["PBX_ALLOWED_IPS"] = cls._old_ips
        # Always leave the shared DB honestly unconnected.
        conn = database.get_db()
        conn.execute("DELETE FROM ivr_gateway_state")
        conn.commit()
        conn.close()

    def tearDown(self):
        conn = database.get_db()
        conn.execute("DELETE FROM ivr_gateway_state")
        conn.commit()
        conn.close()
        if "PBX_ALLOWED_IPS" in os.environ:
            del os.environ["PBX_ALLOWED_IPS"]

    def gw(self, extra=None):
        h = {"X-PBX-Secret": TEST_SECRET, "Content-Type": "application/json"}
        if extra:
            h.update(extra)
        return h

    # -- heartbeat ------------------------------------------------------
    def test_10_heartbeat_requires_secret(self):
        r = self.client.post("/api/ivr/gateway/heartbeat", json={"sip_registered": True})
        self.assertEqual(r.status_code, 401)
        r = self.client.post("/api/ivr/gateway/heartbeat", json={"sip_registered": True},
                             headers={"X-PBX-Secret": "nope"})
        self.assertEqual(r.status_code, 401)

    def test_11_heartbeat_ok_and_health_reflects_it(self):
        r = self.client.post("/api/ivr/gateway/heartbeat",
                             json={"pbx_host": "pbx-test", "sip_registered": True,
                                   "trunk": "carrier-trunk", "asterisk_version": "22.1.0"},
                             headers=self.gw())
        self.assertEqual(r.status_code, 200)
        body = r.get_json()
        self.assertTrue(body["pbx_healthy"])
        self.assertTrue(body["sip_registered"])
        h = self.client.get("/api/ivr/health").get_json()
        self.assertTrue(h["application"])
        self.assertTrue(h["ivr"])
        self.assertTrue(h["pbx"])
        self.assertTrue(h["sip_registered"])
        # Heartbeat alone must NEVER imply PSTN termination.
        self.assertFalse(h["pstn_connected"])

    def test_12_heartbeat_hmac_variant(self):
        raw = json.dumps({"sip_registered": False})
        r = self.client.post("/api/ivr/gateway/heartbeat", data=raw,
                             content_type="application/json",
                             headers={"X-Webhook-Signature": hmac_sig(TEST_SECRET, raw)})
        self.assertEqual(r.status_code, 200)
        h = self.client.get("/api/ivr/health").get_json()
        self.assertTrue(h["pbx"])
        self.assertFalse(h["sip_registered"])

    def test_13_ip_allowlist_enforced(self):
        os.environ["PBX_ALLOWED_IPS"] = "203.0.113.99"
        r = self.client.post("/api/ivr/gateway/heartbeat", json={},
                             headers=self.gw(),
                             environ_overrides={"REMOTE_ADDR": "127.0.0.1"})
        self.assertEqual(r.status_code, 403)

    # -- authorize-dial ---------------------------------------------------
    def _vet_call(self):
        """Real-shaped call row with this test's vet already selected."""
        import uuid as _uuid
        sid = "SIP-TEST-%s" % _uuid.uuid4().hex[:10].upper()
        conn = database.get_db()
        conn.execute(
            "INSERT INTO ivr_calls (call_sid, provider, from_number, to_number, status, channel, is_mock) "
            "VALUES (?,?,?,?,?,?,?)", (sid, "sip", "+919800099001", "7382210251", "IN_PROGRESS", "HELPLINE", 0))
        conn.execute(
            "INSERT INTO ivr_sessions (call_sid, current_state, language, vet_id) VALUES (?,?,?,?)",
            (sid, "ROUTING", "en", self.vet["id"]))
        conn.commit()
        conn.close()
        return sid

    def _drop_call(self, sid):
        conn = database.get_db()
        for t in ("ivr_call_participants", "ivr_events", "ivr_survey_responses",
                  "ivr_transcripts", "ivr_jobs", "ivr_sessions", "ivr_calls"):
            try:
                conn.execute(f"DELETE FROM {t} WHERE call_sid=?", (sid,))
            except Exception:
                pass
        conn.commit()
        conn.close()

    def test_14_authorize_dial_requires_secret(self):
        r = self.client.post("/api/ivr/gateway/authorize-dial",
                             json={"call_sid": "x", "number": "+919999999999"})
        self.assertEqual(r.status_code, 401)

    def test_15_authorize_dial_allows_only_selected_vet(self):
        sid = self._vet_call()
        try:
            r = self.client.post("/api/ivr/gateway/authorize-dial",
                                 json={"call_sid": sid, "number": self.vet["mobile"]},
                                 headers=self.gw())
            self.assertEqual(r.status_code, 200)
            self.assertTrue(r.get_json()["allowed"])
            # Same number, different formatting -> still the vet
            r = self.client.post("/api/ivr/gateway/authorize-dial",
                                 json={"call_sid": sid, "number": "+91" + self.vet["mobile"][-10:]},
                                 headers=self.gw())
            self.assertTrue(r.get_json()["allowed"])
            # Arbitrary toll number -> refused
            r = self.client.post("/api/ivr/gateway/authorize-dial",
                                 json={"call_sid": sid, "number": "+919009009009"},
                                 headers=self.gw())
            self.assertEqual(r.status_code, 403)
            self.assertFalse(r.get_json()["allowed"])
            # Unknown call -> refused
            r = self.client.post("/api/ivr/gateway/authorize-dial",
                                 json={"call_sid": "SIP-NOPE", "number": self.vet["mobile"]},
                                 headers=self.gw())
            self.assertEqual(r.status_code, 403)
        finally:
            self._drop_call(sid)

    def test_16_authorize_dial_record_needs_consent(self):
        sid = self._vet_call()
        try:
            conn = database.get_db()
            conn.execute("UPDATE ivr_calls SET recording_enabled=1, recording_consent=1 WHERE call_sid=?", (sid,))
            conn.commit()
            conn.close()
            r = self.client.post("/api/ivr/gateway/authorize-dial",
                                 json={"call_sid": sid, "number": self.vet["mobile"]},
                                 headers=self.gw())
            self.assertTrue(r.get_json()["record"])
            conn = database.get_db()
            conn.execute("UPDATE ivr_calls SET recording_consent=0 WHERE call_sid=?", (sid,))
            conn.commit()
            conn.close()
            r = self.client.post("/api/ivr/gateway/authorize-dial",
                                 json={"call_sid": sid, "number": self.vet["mobile"]},
                                 headers=self.gw())
            self.assertFalse(r.get_json()["record"])
        finally:
            self._drop_call(sid)


class TestPSTNHonesty(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        database.init_db()
        cls.client = app.test_client()
        cls._old_secret = os.environ.get("PBX_WEBHOOK_SECRET")
        os.environ["PBX_WEBHOOK_SECRET"] = TEST_SECRET
        conn = database.get_db()
        conn.execute("DELETE FROM ivr_gateway_state")
        conn.commit()
        conn.close()

    @classmethod
    def tearDownClass(cls):
        if cls._old_secret is None:
            os.environ.pop("PBX_WEBHOOK_SECRET", None)
        else:
            os.environ["PBX_WEBHOOK_SECRET"] = cls._old_secret
        conn = database.get_db()
        conn.execute("DELETE FROM ivr_gateway_state")
        conn.commit()
        conn.close()

    def tearDown(self):
        conn = database.get_db()
        conn.execute("DELETE FROM ivr_gateway_state")
        conn.commit()
        conn.close()

    def _drop_call(self, sid):
        conn = database.get_db()
        for t in ("ivr_call_participants", "ivr_events", "ivr_survey_responses",
                  "ivr_transcripts", "ivr_jobs", "ivr_sessions", "ivr_calls"):
            try:
                conn.execute(f"DELETE FROM {t} WHERE call_sid=?", (sid,))
            except Exception:
                pass
        conn.commit()
        conn.close()

    def test_20_health_keys_present_and_honest_by_default(self):
        h = self.client.get("/api/ivr/health").get_json()
        for k in ("application", "ivr", "pbx", "sip_registered", "pstn_connected",
                  "pbx_detail", "pstn_detail", "config", "missing_env",
                  "provider_ready", "status", "version"):
            self.assertIn(k, h)
        self.assertTrue(h["application"])
        self.assertTrue(h["ivr"])
        self.assertFalse(h["pbx"])
        self.assertFalse(h["sip_registered"])
        self.assertFalse(h["pstn_connected"])
        self.assertFalse(h["config"]["pstn_connected"])
        info = self.client.get("/api/ivr/info").get_json()
        self.assertFalse(info["config"]["pstn_connected"])

    def test_21_transport_claim_without_secret_changes_nothing(self):
        import uuid as _uuid
        sid = "SIP-FAKE-%s" % _uuid.uuid4().hex[:8].upper()
        try:
            r = self.client.post("/api/ivr/webhook/call",
                                 data={"From": "+919800011111", "To": "7382210251",
                                       "CallSid": sid, "transport": "sip"})
            self.assertEqual(r.status_code, 200)
            conn = database.get_db()
            call = conn.execute("SELECT provider, is_mock FROM ivr_calls WHERE call_sid=?", (sid,)).fetchone()
            conn.close()
            # Treated as an ordinary (mock-mode) call, NOT a real inbound.
            self.assertEqual(call["provider"], "mock")
            self.assertEqual(call["is_mock"], 1)
            h = self.client.get("/api/ivr/health").get_json()
            self.assertFalse(h["pstn_connected"])
        finally:
            self._drop_call(sid)

    def test_22_gateway_authed_inbound_marks_real(self):
        import uuid as _uuid
        sid = "SIP-REAL-%s" % _uuid.uuid4().hex[:8].upper()
        try:
            r = self.client.post("/api/ivr/webhook/call",
                                 data={"From": "+919800022222", "To": "7382210251",
                                       "CallSid": sid, "transport": "sip"},
                                 headers={"X-PBX-Secret": TEST_SECRET})
            self.assertEqual(r.status_code, 200)
            self.assertIn(b"Response", r.data)
            conn = database.get_db()
            call = conn.execute("SELECT provider, is_mock, channel FROM ivr_calls WHERE call_sid=?", (sid,)).fetchone()
            conn.close()
            self.assertEqual(call["provider"], "sip")
            self.assertEqual(call["is_mock"], 0)
            self.assertEqual(call["channel"], "HELPLINE")
            h = self.client.get("/api/ivr/health").get_json()
            self.assertTrue(h["pstn_connected"])
            self.assertIsNotNone(h["pstn_detail"]["first_real_inbound_at_utc"])
            self.assertEqual(h["pstn_detail"]["first_real_inbound_sid"], sid)
            info = self.client.get("/api/ivr/info").get_json()
            self.assertTrue(info["config"]["pstn_connected"])
        finally:
            self._drop_call(sid)

    def test_23_no_endpoint_sets_pstn_directly(self):
        # Heartbeat + authorize-dial + status posts must never flip the flag.
        self.client.post("/api/ivr/gateway/heartbeat", json={"sip_registered": True},
                         headers={"X-PBX-Secret": TEST_SECRET})
        self.client.post("/api/ivr/webhook/status",
                         data={"CallSid": "SIP-GHOST", "CallStatus": "completed"})
        h = self.client.get("/api/ivr/health").get_json()
        self.assertTrue(h["pbx"])
        self.assertFalse(h["pstn_connected"])

    def test_24_unanswered_vet_leg_creates_no_consult_report(self):
        # Mock flow to a connected vet, then a NO_ANSWER leg outcome.
        r = self.client.post("/api/ivr/mock/call", json={"from": "+919800033331"})
        sid = r.get_json()["call_sid"]
        try:
            self.client.post(f"/api/ivr/webhook/language?call_sid={sid}", data={"Digits": "1"})
            self.client.post(f"/api/ivr/webhook/menu?call_sid={sid}", data={"Digits": "1"})
            conn = database.get_db()
            sess = conn.execute("SELECT vet_connected FROM ivr_sessions WHERE call_sid=?", (sid,)).fetchone()
            conn.close()
            self.assertEqual(sess["vet_connected"], 1)
            self.client.post("/api/ivr/webhook/status",
                             data={"CallSid": sid, "CallStatus": "no-answer", "CallDuration": "12"})
            conn = database.get_db()
            n = conn.execute("SELECT COUNT(*) c FROM ivr_reports WHERE call_sid=?", (sid,)).fetchone()["c"]
            conn.close()
            self.assertEqual(n, 0)
        finally:
            self._drop_call(sid)

    def test_25_answered_vet_leg_keeps_minimal_report(self):
        r = self.client.post("/api/ivr/mock/call", json={"from": "+919800033332"})
        sid = r.get_json()["call_sid"]
        try:
            self.client.post(f"/api/ivr/webhook/language?call_sid={sid}", data={"Digits": "1"})
            self.client.post(f"/api/ivr/webhook/menu?call_sid={sid}", data={"Digits": "1"})
            self.client.post("/api/ivr/webhook/status",
                             data={"CallSid": sid, "CallStatus": "completed", "CallDuration": "95"})
            conn = database.get_db()
            rep = conn.execute("SELECT * FROM ivr_reports WHERE call_sid=?", (sid,)).fetchone()
            conn.close()
            self.assertIsNotNone(rep)
            conn = database.get_db()
            conn.execute("DELETE FROM ivr_reports WHERE call_sid=?", (sid,))
            # report creation may also create a case; drop it for tidiness
            try:
                case_id = rep["case_id"]
            except Exception:
                case_id = None
            if case_id:
                try:
                    conn.execute("DELETE FROM cases WHERE id=?", (case_id,))
                except Exception:
                    pass
            conn.commit()
            conn.close()
        finally:
            self._drop_call(sid)


class FakeAGI:
    """Scripted stand-in for pashu_ivr.AGI (same method shapes)."""

    def __init__(self, get_data_results=None, dial_status="ANSWER"):
        self.calls = []
        self._get_data = list(get_data_results or [])
        self._dial_status = dial_status
        self.vars = {}

    def answer(self):
        self.calls.append(("answer",))
        return True

    def stream_file(self, sound, escape_digits=""):
        self.calls.append(("stream", sound, escape_digits))
        return 0, ""

    def get_data(self, sound, timeout_ms, max_digits):
        self.calls.append(("get_data", sound, timeout_ms, max_digits))
        if not self._get_data:
            return 0, "(timeout)"
        return self._get_data.pop(0)

    def exec_dial(self, dial_string, timeout):
        self.calls.append(("dial", dial_string, timeout))
        return 0

    def exec_monitor(self, path_base):
        self.calls.append(("monitor", path_base))
        return 0

    def get_variable(self, name):
        self.calls.append(("getvar", name))
        return self._dial_status if name == "DIALSTATUS" else ""

    def set_variable(self, name, value):
        self.vars[name] = value
        return True

    def wait(self, seconds):
        self.calls.append(("wait", seconds))
        return 0

    def hangup(self):
        self.calls.append(("hangup",))


class FakeBackend(pashu_ivr.BackendClient):
    def __init__(self, actions=None, authorize=True, record=True):
        super().__init__(base_url="https://backend.test", secret="s")
        self.actions = actions or {}
        self.posts = []
        self._authorize = authorize
        self._record = record

    def post_form(self, url, fields):
        self.posts.append(("form", url, dict(fields)))
        if url.endswith("/api/ivr/webhook/call"):
            return self.actions.get("__incoming__", "<Response><Hangup/></Response>")
        return self.actions.get(url, "<Response><Hangup/></Response>")

    def post_json(self, url, obj):
        self.posts.append(("json", url, dict(obj)))
        if url.endswith("/api/ivr/gateway/authorize-dial"):
            if self._authorize:
                return {"allowed": True, "vet_id": 3, "record": self._record}
            return {"allowed": False, "reason": "number-not-selected-vet"}
        return {"ok": True}


WELCOME_DOC = ('<?xml version="1.0" encoding="UTF-8"?><Response>'
               '<Say voice="alice" language="en">Welcome.</Say>'
               '<Gather action="/api/ivr/webhook/menu?call_sid=SIP-1" numDigits="1" '
               'timeout="10" finishOnKey="#" input="dtmf speech">'
               '<Say voice="alice" language="en">Press 1.</Say></Gather>'
               '<Redirect>/api/ivr/webhook/menu?call_sid=SIP-1</Redirect></Response>')

DIAL_DOC = ('<Response><Say language="en">Connecting.</Say>'
            '<Dial timeout="30"><Number>+919800000001</Number></Dial></Response>')


class TestAGIInterpreter(unittest.TestCase):
    def test_30_parse_verbs(self):
        verbs = pashu_ivr.parse_twiml(WELCOME_DOC)
        self.assertEqual([v["verb"] for v in verbs], ["say", "gather", "redirect"])
        g = verbs[1]
        self.assertEqual(g["action"], "/api/ivr/webhook/menu?call_sid=SIP-1")
        self.assertEqual(g["num_digits"], 1)
        self.assertEqual(g["timeout"], 10)
        self.assertEqual(len(g["says"]), 1)
        verbs = pashu_ivr.parse_twiml(DIAL_DOC)
        self.assertEqual(verbs[1]["verb"], "dial")
        self.assertEqual(verbs[1]["number"], "+919800000001")
        self.assertTrue(verbs[1]["timeout"] > 0)
        self.assertEqual(pashu_ivr.parse_twiml("<Response><Hangup/></Response>")[0]["verb"], "hangup")
        self.assertEqual(pashu_ivr.parse_twiml('<Response><Pause length="2"/></Response>')[0]["length"], 2)
        # Unknown verbs are skipped, not fatal
        self.assertEqual(pashu_ivr.parse_twiml("<Response><Sms>hi</Sms></Response>"), [])
        with self.assertRaises(pashu_ivr.BackendError):
            pashu_ivr.parse_twiml("<Response><Say>oops")

    def test_31_strip_terminator(self):
        self.assertEqual(pashu_ivr.strip_terminator("12#"), "12")
        self.assertEqual(pashu_ivr.strip_terminator("1"), "1")
        self.assertEqual(pashu_ivr.strip_terminator(""), "")

    def test_32_dial_string_formats(self):
        self.assertEqual(pashu_ivr.build_vet_dial_string("+919800000001", trunk="t"),
                         "PJSIP/9800000001@t")
        self.assertEqual(pashu_ivr.build_vet_dial_string("+919800000001", trunk="t", fmt="e164"),
                         "PJSIP/+919800000001@t")
        with self.assertRaises(pashu_ivr.BackendError):
            pashu_ivr.build_vet_dial_string("123", trunk="t")

    def test_33_resolve_url_ssrf_guard(self):
        b = pashu_ivr.BackendClient(base_url="https://backend.test", secret="s")
        self.assertEqual(b.resolve_url("/api/x"), "https://backend.test/api/x")
        self.assertEqual(b.resolve_url("https://backend.test/api/y"), "https://backend.test/api/y")
        with self.assertRaises(pashu_ivr.BackendError):
            b.resolve_url("https://evil.example/steal")
        with self.assertRaises(pashu_ivr.BackendError):
            b.resolve_url("")

    def test_34_select_voice_fallback(self):
        self.assertEqual(pashu_ivr.select_voice("te"), pashu_ivr.TTS_VOICES["te"])
        self.assertEqual(pashu_ivr.select_voice("xx"), pashu_ivr.TTS_VOICES["en"])

    def test_35_full_flow_gather_then_vet_bridge(self):
        actions = {
            "__incoming__": WELCOME_DOC,
            "https://backend.test/api/ivr/webhook/menu?call_sid=SIP-1": DIAL_DOC,
        }
        agi = FakeAGI(get_data_results=[(1, "")], dial_status="ANSWER")
        be = FakeBackend(actions)
        old_render = pashu_ivr.render_prompt
        pashu_ivr.render_prompt = lambda text, lang="en": "/tmp/fake.wav"
        try:
            runner = pashu_ivr.CallRunner(agi, be, caller_id="+919800011111",
                                          called_number="7382210251",
                                          unique_id="u1", call_sid="SIP-1")
            self.assertEqual(runner.run(), 0)
        finally:
            pashu_ivr.render_prompt = old_render
        kinds = [c[0] for c in agi.calls]
        self.assertIn("answer", kinds)
        self.assertIn("get_data", kinds)
        dials = [c for c in agi.calls if c[0] == "dial"]
        self.assertEqual(len(dials), 1)
        self.assertIn("PJSIP/9800000001@", dials[0][1])
        self.assertIn("hangup", kinds)
        # digits posted to the menu action
        forms = [p for p in be.posts if p[0] == "form"]
        menu_posts = [p for p in forms if "webhook/menu" in p[1]]
        self.assertTrue(menu_posts)
        self.assertEqual(menu_posts[0][2].get("Digits"), "1")
        # authorize-dial consulted, status + call-ended reported
        urls = [p[1] for p in be.posts if p[0] == "json"]
        self.assertTrue(any(u.endswith("authorize-dial") for u in urls))
        form_urls = [p[1] for p in be.posts if p[0] == "form"]
        answered = [p for p in be.posts if p[0] == "form" and p[1].endswith("webhook/status")]
        self.assertTrue(answered)
        self.assertEqual(answered[0][2].get("CallStatus"), "COMPLETED")
        self.assertTrue(any(u.endswith("webhook/call-ended") for u in urls))

    def test_36_dial_refused_never_dials(self):
        actions = {"__incoming__": DIAL_DOC}
        agi = FakeAGI()
        be = FakeBackend(actions, authorize=False)
        old_render = pashu_ivr.render_prompt
        pashu_ivr.render_prompt = lambda text, lang="en": "/tmp/fake.wav"
        try:
            runner = pashu_ivr.CallRunner(agi, be, unique_id="u2", call_sid="SIP-2")
            self.assertEqual(runner.run(), 0)
        finally:
            pashu_ivr.render_prompt = old_render
        kinds = [c[0] for c in agi.calls]
        self.assertNotIn("dial", kinds)
        self.assertIn("stream", kinds)   # failure prompt played
        self.assertIn("hangup", kinds)
        urls = [p[1] for p in be.posts if p[0] == "json"]
        self.assertTrue(any(u.endswith("webhook/call-ended") for u in urls))

    def test_37_vet_noanswer_falls_back_to_survey(self):
        survey_doc = "<Response><Say>Survey.</Say><Hangup/></Response>"
        actions = {
            "__incoming__": DIAL_DOC,
            "https://backend.test/api/ivr/webhook/survey/start?call_sid=SIP-3": survey_doc,
        }
        agi = FakeAGI(dial_status="NOANSWER")
        be = FakeBackend(actions)
        old_render = pashu_ivr.render_prompt
        pashu_ivr.render_prompt = lambda text, lang="en": None  # tts down: still works
        try:
            runner = pashu_ivr.CallRunner(agi, be, unique_id="u3", call_sid="SIP-3")
            self.assertEqual(runner.run(), 0)
        finally:
            pashu_ivr.render_prompt = old_render
        forms = [p for p in be.posts if p[0] == "form"]
        self.assertTrue(any("survey/start" in p[1] for p in forms))
        kinds = [c[0] for c in agi.calls]
        self.assertIn("dial", kinds)
        self.assertIn("hangup", kinds)

    def test_38_hangup_mid_call_posts_call_ended(self):
        class HangAGI(FakeAGI):
            def stream_file(self, sound, escape_digits=""):
                self.calls.append(("stream", sound, escape_digits))
                return -1, ""  # channel hung up
        agi = HangAGI()
        be = FakeBackend({"__incoming__": "<Response><Say>Hi.</Say></Response>"})
        old_render = pashu_ivr.render_prompt
        pashu_ivr.render_prompt = lambda text, lang="en": "/tmp/fake.wav"
        try:
            runner = pashu_ivr.CallRunner(agi, be, unique_id="u4", call_sid="SIP-4")
            self.assertEqual(runner.run(), 0)
        finally:
            pashu_ivr.render_prompt = old_render
        urls = [p[1] for p in be.posts if p[0] == "json"]
        self.assertTrue(any(u.endswith("webhook/call-ended") for u in urls))

    def test_39_render_py_templates(self):
        import tempfile as _tf
        envf = _tf.NamedTemporaryFile("w", suffix=".env", delete=False)
        envf.write("A=hello\nB=\nC=false\n")
        envf.close()
        try:
            env = render_py.load_env(envf.name)
            self.assertEqual(env["A"], "hello")
            out = render_py.render("x={{A}} {{#IF B}}Y{{/IF}}{{#IF C}}Z{{/IF}}{{#IF A}}W{{/IF}}", env)
            self.assertEqual(out, "x=hello W")
        finally:
            os.unlink(envf.name)


if __name__ == "__main__":
    unittest.main()
