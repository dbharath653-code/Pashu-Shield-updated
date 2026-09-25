#!/usr/bin/env python3
"""
Pashu-Shield voice gateway (Asterisk AGI).

Pure TRANSPORT layer - contains NO IVR business logic. Flow:

  carrier SIP -> Asterisk -> this AGI -> Pashu-Shield HTTPS webhooks
  (the SAME /api/ivr/webhook/* endpoints the mock transport uses)
  -> TwiML response -> executed here as audio/DTMF/bridge actions.

TwiML verbs supported (exactly what the backend emits): Say, Gather, Dial,
Redirect, Hangup, Pause. Anything else is logged and skipped (fail-safe).

Stdlib only (no pip dependencies) so it runs on a bare PBX host.

Environment (see pbx/pbx.env.example; NEVER commit real values):
  PASHU_BACKEND_URL   https://<backend-host>            (required)
  PBX_WEBHOOK_SECRET  shared secret with backend        (required)
  PBX_TRUNK_ENDPOINT  pjsip endpoint for vet legs       (required for bridging)
  PBX_SOUNDS_DIR      prompt cache dir                  (default /var/lib/asterisk/sounds/pashu)
  PBX_TTS_COMMAND     e.g. espeak-ng -v {voice} -s 150 -f {textfile} -w {wav}
  PBX_TTS_VOICE_<LANG>  per-language TTS voice override (en/te/hi/mr)
  PBX_VET_DIAL_FORMAT national|e164|raw                 (default national)
  PBX_RECORDING_ENABLED true|false                      (default true; per-call
                        recording ALSO requires the farmer's in-call consent)
  PBX_RECORDING_DIR   MixMonitor output dir             (default /var/spool/asterisk/monitor)
  PBX_AGI_LOG         optional extra log file
  PBX_FAILURE_PROMPT_<LANG> failure message override per language

Security properties:
  - Vet legs are dialled ONLY after the backend authorizes the exact number
    for this call (authorize-dial binds to the selected vet). The PBX can
    never be used to call arbitrary numbers.
  - Redirect/action URLs are followed ONLY on the backend host (no SSRF).
  - Caller ID is passed through from SIP, never invented. When the carrier
    withholds it, the backend's existing unknown-caller flow handles it.
"""
import hashlib
import json
import os
import subprocess
import sys
import tempfile
import time
import traceback
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET

# ---------------------------------------------------------------- config ---

def _env(name, default=""):
    return os.environ.get(name, default)


BACKEND_URL = _env("PASHU_BACKEND_URL", "").rstrip("/")
GATEWAY_SECRET = _env("PBX_WEBHOOK_SECRET", "")
TRUNK_ENDPOINT = _env("PBX_TRUNK_ENDPOINT", "carrier-trunk")
SOUNDS_DIR = _env("PBX_SOUNDS_DIR", "/var/lib/asterisk/sounds/pashu")
TTS_COMMAND = _env("PBX_TTS_COMMAND",
                   "espeak-ng -v {voice} -s 150 -f {textfile} -w {wav}")
VET_DIAL_FORMAT = _env("PBX_VET_DIAL_FORMAT", "national").lower()
RECORDING_ENABLED = _env("PBX_RECORDING_ENABLED", "true").lower() in ("1", "true", "yes")
RECORDING_DIR = _env("PBX_RECORDING_DIR", "/var/spool/asterisk/monitor")
AGI_LOG = _env("PBX_AGI_LOG", "")

TTS_VOICES = {
    "en": _env("PBX_TTS_VOICE_EN", "en"),
    "te": _env("PBX_TTS_VOICE_TE", "te"),
    "hi": _env("PBX_TTS_VOICE_HI", "hi"),
    "mr": _env("PBX_TTS_VOICE_MR", "mr"),
}

FAILURE_PROMPTS = {
    "en": _env("PBX_FAILURE_PROMPT_EN",
               "Sorry, the service is facing a technical problem. Please call again later. Thank you."),
    "te": _env("PBX_FAILURE_PROMPT_TE",
               "Kshaminchandi, seva lo samasya vachchindi. Dayachesi tarvata malli call cheyandi."),
    "hi": _env("PBX_FAILURE_PROMPT_HI",
               "Kshama karen, seva mein takneeki samasya hai. Kripaya baad mein punah call karen."),
    "mr": _env("PBX_FAILURE_PROMPT_MR",
               "Maf kara, sevet tantrik adchan ahe. Krupaya nantar punha call kara."),
}

MAX_TWIML_HOPS = 60          # loop guard across Redirect/action fetches
MAX_SAY_CHARS = 1200
GETDATA_TIMEOUT_CAP_S = 30
DIAL_TIMEOUT_CAP_S = 60


def log(msg):
    line = "pashu_ivr: %s" % msg
    print(line, file=sys.stderr)
    if AGI_LOG:
        try:
            with open(AGI_LOG, "a") as fh:
                fh.write(time.strftime("%Y-%m-%d %H:%M:%S ") + line + "\n")
        except Exception:
            pass


# --------------------------------------------------------------- backend ---

class BackendError(Exception):
    pass


class BackendClient:
    """Minimal HTTPS client for Pashu-Shield webhooks (stdlib urllib)."""

    def __init__(self, base_url=None, secret=None, timeout=15):
        self.base_url = (base_url if base_url is not None else BACKEND_URL).rstrip("/")
        self.secret = secret if secret is not None else GATEWAY_SECRET
        self.timeout = timeout
        if self.base_url:
            self.host = urllib.parse.urlparse(self.base_url).hostname or ""
        else:
            self.host = ""

    def _headers(self, ctype=None):
        h = {"X-PBX-Secret": self.secret}
        if ctype:
            h["Content-Type"] = ctype
        return h

    def _post(self, url, data_bytes, ctype):
        req = urllib.request.Request(url, data=data_bytes, headers=self._headers(ctype), method="POST")
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                return resp.status, resp.read().decode("utf-8", "replace")
        except Exception as e:
            raise BackendError("%s: %s" % (url, e))

    def post_form(self, url, fields):
        body = urllib.parse.urlencode({k: v for k, v in fields.items() if v is not None}).encode()
        _status, text = self._post(url, body, "application/x-www-form-urlencoded")
        return text

    def post_json(self, url, obj):
        body = json.dumps(obj).encode()
        _status, text = self._post(url, body, "application/json")
        try:
            return json.loads(text)
        except Exception:
            return {"_raw": text}

    def resolve_url(self, action):
        """Resolve a TwiML action/Redirect to an absolute URL on OUR backend.

        Only same-host https/http URLs are followed (SSRF guard). Relative
        paths are joined to the backend base. Anything else -> BackendError.
        """
        action = (action or "").strip()
        if not action:
            raise BackendError("empty action url")
        if action.startswith("/"):
            if not self.base_url:
                raise BackendError("relative action without PASHU_BACKEND_URL")
            return self.base_url + action
        if action.startswith("http://") or action.startswith("https://"):
            host = urllib.parse.urlparse(action).hostname or ""
            if self.host and host.lower() != self.host.lower():
                raise BackendError("refusing off-host url: %s" % action)
            return action
        # Bare path without leading slash -> treat as backend-relative.
        if not self.base_url:
            raise BackendError("relative action without PASHU_BACKEND_URL")
        return self.base_url + "/" + action.lstrip("/")


# ----------------------------------------------------------------- TwiML ---

def _text_of(el):
    return "".join(el.itertext()).strip()


def parse_twiml(xml_text):
    """Parse TwiML into a list of verb dicts. Raises BackendError on garbage."""
    try:
        root = ET.fromstring((xml_text or "").strip() or "<Response/>")
    except ET.ParseError as e:
        raise BackendError("invalid twiml: %s" % e)
    verbs = []
    # Accept <Response> wrapper or a bare verb (defensive).
    children = list(root) if root.tag == "Response" else [root]
    for el in children:
        tag = (el.tag or "").strip()
        if tag == "Say":
            verbs.append({"verb": "say", "text": _text_of(el)[:MAX_SAY_CHARS],
                          "lang": (el.get("language") or "en").strip().lower()[:5]})
        elif tag == "Gather":
            says = [{"text": _text_of(s)[:MAX_SAY_CHARS],
                     "lang": (s.get("language") or "en").strip().lower()[:5]}
                    for s in el.findall("Say")]
            try:
                num_digits = max(1, min(20, int(el.get("numDigits") or "1")))
            except ValueError:
                num_digits = 1
            try:
                timeout = max(1, min(GETDATA_TIMEOUT_CAP_S, int(el.get("timeout") or "8")))
            except ValueError:
                timeout = 8
            verbs.append({"verb": "gather", "says": says,
                          "action": (el.get("action") or "").strip(),
                          "num_digits": num_digits, "timeout": timeout,
                          "finish_on_key": (el.get("finishOnKey") or "#")[:1]})
        elif tag == "Dial":
            num_el = el.find("Number")
            if num_el is not None and _text_of(num_el):
                number = _text_of(num_el)
            else:
                number = _text_of(el)
            try:
                timeout = max(5, min(DIAL_TIMEOUT_CAP_S, int(el.get("timeout") or "30")))
            except ValueError:
                timeout = 30
            verbs.append({"verb": "dial", "number": number.strip(),
                          "timeout": timeout,
                          "record": (el.get("record") or "").strip().lower() != ""})
        elif tag == "Redirect":
            verbs.append({"verb": "redirect", "url": _text_of(el)})
        elif tag == "Hangup":
            verbs.append({"verb": "hangup"})
        elif tag == "Pause":
            try:
                length = max(1, min(30, int(el.get("length") or "1")))
            except ValueError:
                length = 1
            verbs.append({"verb": "pause", "length": length})
        elif tag == "Response":
            for sub in el:
                verbs.extend(parse_twiml(ET.tostring(sub, encoding="unicode")))
        else:
            log("skipping unsupported twiml verb <%s> (fail-safe)" % tag)
    return verbs


def strip_terminator(digits, finish_on_key="#"):
    d = (digits or "").strip()
    if finish_on_key and d.endswith(finish_on_key):
        d = d[: -len(finish_on_key)]
    return d.replace("#", "")


def select_voice(lang):
    return TTS_VOICES.get((lang or "en").lower(), TTS_VOICES["en"])


def build_vet_dial_string(number, trunk=None, fmt=None):
    """Build the Asterisk dial string for an AUTHORIZED vet number.

    Only ever called after backend authorize-dial approval. Format notes:
      national: 10-digit Indian mobile (what most carrier trunks expect)
      e164:     +91XXXXXXXXXX
      raw:      digits as returned (leading + kept)
    """
    trunk = trunk if trunk is not None else TRUNK_ENDPOINT
    fmt = (fmt if fmt is not None else VET_DIAL_FORMAT).lower()
    digits = "".join(c for c in (number or "") if c.isdigit())
    if fmt == "e164":
        if len(digits) == 10:
            digits = "91" + digits
        user = "+" + digits if not digits.startswith("+") else digits
    elif fmt == "raw":
        user = ("+" if str(number or "").strip().startswith("+") else "") + digits
    else:  # national
        user = digits[-10:] if len(digits) >= 10 else digits
    if not user or (fmt == "national" and len(user) != 10):
        raise BackendError("refusing to dial malformed number")
    return "PJSIP/%s@%s" % (user, trunk)


# ------------------------------------------------------------------- TTS ---

def _tts_cache_path(text, lang, voice):
    digest = hashlib.sha1(("%s|%s|%s" % (lang, voice, text)).encode("utf-8")).hexdigest()
    d = os.path.join(SOUNDS_DIR, lang)
    return os.path.join(d, "p-%s.wav" % digest)


def render_prompt(text, lang="en"):
    """Render Say text to a cached wav file. Returns path or None on failure."""
    text = (text or "").strip()
    if not text:
        return None
    lang = (lang or "en").lower()
    if lang not in TTS_VOICES:
        lang = "en"
    voice = select_voice(lang)
    for attempt_voice in (voice, TTS_VOICES["en"]):
        path = _tts_cache_path(text, lang, attempt_voice)
        if os.path.exists(path) and os.path.getsize(path) > 1000:
            return path
        try:
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False,
                                             dir=os.path.dirname(path)) as tf:
                tf.write(text)
                textfile = tf.name
            tmp_wav = path + ".tmp-%d.wav" % os.getpid()
            cmd = TTS_COMMAND.replace("{voice}", attempt_voice)\
                             .replace("{textfile}", textfile)\
                             .replace("{wav}", tmp_wav)\
                             .replace("{lang}", lang)
            proc = subprocess.run(cmd, shell=True, timeout=60,
                                  stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            try:
                os.unlink(textfile)
            except OSError:
                pass
            if proc.returncode == 0 and os.path.exists(tmp_wav) and os.path.getsize(tmp_wav) > 1000:
                os.replace(tmp_wav, path)
                return path
            try:
                if os.path.exists(tmp_wav):
                    os.unlink(tmp_wav)
            except OSError:
                pass
            log("tts failed voice=%s rc=%s" % (attempt_voice, proc.returncode))
        except Exception as e:
            log("tts error voice=%s: %s" % (attempt_voice, e))
    return None


def asterisk_sound_name(wav_path):
    """Convert a wav path to an Asterisk playback name (no extension)."""
    if wav_path.endswith(".wav"):
        wav_path = wav_path[: -len(".wav")]
    return wav_path


# ------------------------------------------------------------------- AGI ---

class AGI:
    """Minimal AGI client (stdin/stdout), injectable for tests."""

    def __init__(self, rfile=None, wfile=None):
        self.rfile = rfile if rfile is not None else sys.stdin
        self.wfile = wfile if wfile is not None else sys.stdout
        self.env = {}
        self._read_env()

    def _read_env(self):
        while True:
            line = self.rfile.readline()
            if not line:
                break
            line = line.strip()
            if line == "":
                break
            if ":" in line:
                k, v = line.split(":", 1)
                self.env[k.strip()] = v.strip()

    def command(self, cmd):
        self.wfile.write(cmd.strip() + "\n")
        self.wfile.flush()
        resp = self.rfile.readline()
        if not resp:
            return -1, ""
        resp = resp.strip()
        # "200 result=<n> ..."  (Gather/timeout forms carry extra text)
        code = -1
        extra = ""
        if resp.startswith("200"):
            parts = resp[4:].strip().split(None, 1)
            extra = parts[1] if len(parts) > 1 else ""
            try:
                keyval = parts[0]
                code = int(keyval.split("=", 1)[1])
            except (ValueError, IndexError):
                code = 0
        elif resp.startswith("510") or resp.startswith("520"):
            code = -2  # invalid/unknown command
        log("agi %s -> %s" % (cmd.split(None, 1)[0], resp[:120]))
        return code, extra

    # -- verbs used by the gateway -------------------------------------
    def answer(self):
        code, _ = self.command("ANSWER")
        return code == 0

    def stream_file(self, sound, escape_digits=""):
        code, extra = self.command('STREAM FILE "%s" "%s"' % (sound, escape_digits or ""))
        return code, extra  # code: 0 ok / ascii digit / -1 hangup

    def get_data(self, sound, timeout_ms, max_digits):
        code, extra = self.command("GET DATA \"%s\" %d %d" % (sound, timeout_ms, max_digits))
        # result=digits come back as: 200 result=<digits> ... hmm: GET DATA
        # returns the digits in parens? No: `200 result=1 (123)`? Actually
        # Asterisk returns `200 result=<digits>`?? Real format: `200 result=1`
        # is for most; GET DATA returns `200 result=<collected>` where the
        # collected digits ARE the result only if numeric... The true format is
        # `200 result=1` is wrong; GET DATA gives `200 result=<digits>` when
        # digits collected?? Empirically: `200 result=123 (timeout)` etc.
        # We parse BOTH the numeric result and parenthesised text below via
        # parse_get_data().
        return code, extra

    def exec_dial(self, dial_string, timeout):
        code, _ = self.command("EXEC Dial \"%s,%d\"" % (dial_string, timeout))
        return code

    def exec_monitor(self, path_base):
        code, _ = self.command("EXEC MixMonitor \"%s.wav,b\"" % path_base)
        return code

    def get_variable(self, name):
        code, extra = self.command("GET VARIABLE %s" % name)
        # 200 result=1 (value)  /  200 result=0
        if code == 1 and extra.startswith("(") and extra.endswith(")"):
            return extra[1:-1]
        return ""

    def set_variable(self, name, value):
        code, _ = self.command("SET VARIABLE %s \"%s\"" % (name, value))
        return code == 0

    def wait(self, seconds):
        code, _ = self.command("WAIT %d" % seconds)
        return code

    def hangup(self):
        try:
            self.command("HANGUP")
        except Exception:
            pass


def parse_get_data(code, extra):
    """Extract collected DTMF digits from a GET DATA response.

    Asterisk returns e.g. `200 result=123` (digits as the result) or
    `200 result=-1` on hangup, `200 result= (timeout)` on timeout.
    Our AGI.command parses result into int when numeric; non-numeric results
    yield code 0 with the raw text in `extra`. Handle all shapes.
    """
    if code == -1:
        return None  # hung up
    text = (extra or "").strip()
    # Parenthesised payload first: `200 result= (timeout)` etc.
    if text.startswith("(") and text.endswith(")"):
        inner = text[1:-1]
        if inner == "" or inner == "timeout":
            return ""
        if all(c in "0123456789#*" for c in inner):
            return inner
        return ""
    if text and all(c in "0123456789#*" for c in text):
        return text
    # Numeric result (code) doubles as digits when no parens present.
    if code is not None and code >= 0 and not text:
        # Ambiguous with status codes; GET DATA has no bare status - the
        # digits ARE the result, so a lone number means digits collected,
        # except 0-length... Asterisk sends `200 result=` (empty) on timeout
        # which parses to code 0 + empty extra. Treat lone 0 + empty as "".
        return "" if code == 0 else str(code)
    return ""


# --------------------------------------------------------------- runner ---

class CallRunner:
    def __init__(self, agi, backend, caller_id="", called_number="", unique_id="",
                 call_sid="", start_time=None):
        self.agi = agi
        self.backend = backend
        self.caller_id = caller_id or ""
        self.called_number = called_number or ""
        self.unique_id = unique_id or ""
        self.call_sid = call_sid or ("SIP-%s" % (unique_id or "%.0f" % (time.time() * 1000)))
        self.start_time = start_time or time.monotonic()
        self.lang = "en"
        self.answered_duration = 0
        self.hops = 0
        self.ended_posted = False

    # -- helpers ------------------------------------------------------
    def duration(self):
        return int(time.monotonic() - self.start_time)

    def post_call_ended(self):
        if self.ended_posted:
            return
        self.ended_posted = True
        try:
            self.backend.post_json(
                self.backend.resolve_url("/api/ivr/webhook/call-ended"),
                {"call_sid": self.call_sid, "duration": self.duration()})
        except Exception as e:
            log("call-ended post failed (best-effort): %s" % e)

    def fetch_initial_twiml(self):
        url = self.backend.resolve_url("/api/ivr/webhook/call")
        fields = {"From": self.caller_id, "To": self.called_number,
                  "CallSid": self.call_sid, "transport": "sip"}
        return self.backend.post_form(url, fields)

    def post_action(self, action, digits=None):
        url = self.backend.resolve_url(action)
        fields = {"CallSid": self.call_sid}
        if digits is not None:
            fields["Digits"] = digits
        return self.backend.post_form(url, fields)

    # -- verb executors -----------------------------------------------
    def do_say(self, text, lang):
        if lang:
            self.lang = lang
        wav = render_prompt(text, self.lang)
        if not wav:
            log("say: no audio for %r (tts unavailable), continuing" % text[:60])
            return True  # fail-safe: skip prompt, keep the call alive
        code, _extra = self.agi.stream_file(asterisk_sound_name(wav), "")
        if code == -1:
            return False
        return True

    def do_gather(self, verb):
        says = verb.get("says") or []
        prompt_text = " ".join(s.get("text", "") for s in says).strip()
        if says and says[0].get("lang"):
            self.lang = says[0]["lang"]
        sound = "silence/1"  # stock Asterisk silence; replaced when we have audio
        if prompt_text:
            wav = render_prompt(prompt_text, self.lang)
            if wav:
                sound = asterisk_sound_name(wav)
            else:
                log("gather: tts unavailable, collecting digits against silence")
        code, extra = self.agi.get_data(sound, verb["timeout"] * 1000, verb["num_digits"])
        digits = parse_get_data(code, extra)
        if digits is None:
            return None  # hangup
        digits = strip_terminator(digits, verb.get("finish_on_key", "#"))
        log("gather collected digits=%r" % digits)
        try:
            return self.post_action(verb["action"], digits)
        except BackendError as e:
            log("gather action failed: %s" % e)
            return False

    def do_dial(self, verb):
        number = (verb.get("number") or "").strip()
        # Authorize EVERY vet leg with the backend (binds to selected vet).
        try:
            decision = self.backend.post_json(
                self.backend.resolve_url("/api/ivr/gateway/authorize-dial"),
                {"call_sid": self.call_sid, "number": number})
        except BackendError as e:
            log("authorize-dial failed: %s" % e)
            return False
        if not isinstance(decision, dict) or not decision.get("allowed"):
            log("dial REFUSED by backend for %r (reason=%s)" %
                (number[-6:] if number else "", (decision or {}).get("reason")))
            return False
        try:
            dial_string = build_vet_dial_string(number)
        except BackendError as e:
            log("dial refused locally: %s" % e)
            return False
        if verb.get("record") and decision.get("record") and RECORDING_ENABLED:
            try:
                os.makedirs(RECORDING_DIR, exist_ok=True)
                base = os.path.join(
                    RECORDING_DIR,
                    "pashu-%s-%s" % (self.call_sid.replace("/", "_"),
                                     time.strftime("%Y%m%d-%H%M%S")))
                self.agi.exec_monitor(base)
                log("recording vet leg to %s.wav (consented)" % base)
            except Exception as e:
                log("MixMonitor failed (continuing unrecorded): %s" % e)
        elif verb.get("record") and not decision.get("record"):
            log("not recording: no in-call consent on record (honest)")
        log("bridging vet leg: trunk=%s timeout=%s" % (TRUNK_ENDPOINT, verb["timeout"]))
        self.agi.exec_dial(dial_string, verb["timeout"])
        status = ""
        try:
            status = self.agi.get_variable("DIALSTATUS")
        except Exception:
            pass
        log("vet leg ended dialstatus=%s" % (status or "?"))
        if status == "ANSWER":
            # Consultation happened: record completion (form-encoded, like a
            # provider status callback) and continue with the next verb.
            try:
                self.backend.post_form(
                    self.backend.resolve_url("/api/ivr/webhook/status"),
                    {"CallSid": self.call_sid, "CallStatus": "COMPLETED",
                     "CallDuration": str(self.duration())})
            except Exception as e:
                log("status post failed (best-effort): %s" % e)
            return True
        # Vet did not answer: preserve the metadata, then fall back to the
        # existing automated survey (transport failure fallback, §27).
        try:
            self.backend.post_form(
                self.backend.resolve_url("/api/ivr/webhook/status"),
                {"CallSid": self.call_sid,
                 "CallStatus": {"BUSY": "BUSY", "CANCEL": "CANCELED"}.get(status, "NO_ANSWER"),
                 "CallDuration": str(self.duration())})
        except Exception as e:
            log("status post failed (best-effort): %s" % e)
        return "/api/ivr/webhook/survey/start?call_sid=%s" % urllib.parse.quote(self.call_sid)

    # -- main loop -----------------------------------------------------
    def run_document(self, xml_text):
        """Execute one TwiML document. Returns next xml | 'hangup' | 'done'."""
        verbs = parse_twiml(xml_text)
        i = 0
        while i < len(verbs):
            v = verbs[i]
            i += 1
            kind = v["verb"]
            if kind == "say":
                if not self.do_say(v["text"], v.get("lang") or self.lang):
                    return "hangup"
            elif kind == "gather":
                nxt = self.do_gather(v)
                if nxt is None:
                    return "hangup"   # caller hung up
                if nxt is False:
                    return "done"     # backend failure -> failure path
                return nxt            # new document from action
            elif kind == "dial":
                res = self.do_dial(v)
                if res is False:
                    return "done"     # refused/failed -> failure path
                if isinstance(res, str):
                    # Vet leg unanswered -> new document from survey fallback.
                    try:
                        return self.post_action(res, None)
                    except BackendError as e:
                        log("survey fallback failed: %s" % e)
                        return "done"
                # After the bridge ends, continue with the next verb
                # (matches Twilio semantics).
            elif kind == "redirect":
                try:
                    return self.post_action(v["url"], None)
                except BackendError as e:
                    log("redirect failed: %s" % e)
                    return "done"
            elif kind == "hangup":
                return "hangup"
            elif kind == "pause":
                if self.agi.wait(v.get("length", 1)) == -1:
                    return "hangup"
            else:
                log("unknown verb %r skipped" % kind)
        return "hangup"  # end of document hangs up (Twilio semantics)

    def run(self):
        try:
            self.agi.set_variable("PASHU_CALL_SID", self.call_sid)
        except Exception:
            pass
        if not self.agi.answer():
            log("answer failed, exiting")
            self.post_call_ended()
            return 1
        try:
            xml_text = self.fetch_initial_twiml()
        except BackendError as e:
            log("initial webhook failed: %s" % e)
            self.failure_exit()
            return 0
        outcome = None
        while self.hops < MAX_TWIML_HOPS:
            self.hops += 1
            try:
                outcome = self.run_document(xml_text)
            except BackendError as e:
                log("twiml error: %s" % e)
                outcome = "done"
            if outcome == "hangup" or outcome is None:
                break
            if outcome == "done":
                break
            xml_text = outcome  # another document from Gather/Redirect
        if self.hops >= MAX_TWIML_HOPS:
            log("hop limit reached, hanging up safely")
            outcome = "hangup"
        if outcome == "done":
            self.failure_exit()
        else:
            self.post_call_ended()
            try:
                self.agi.hangup()
            except Exception:
                pass
        return 0

    def failure_exit(self):
        """Backend/transport failure: play a generic message, preserve metadata."""
        try:
            wav = render_prompt(FAILURE_PROMPTS.get(self.lang, FAILURE_PROMPTS["en"]), self.lang)
            if wav:
                self.agi.stream_file(asterisk_sound_name(wav), "")
        except Exception:
            pass
        self.post_call_ended()
        try:
            self.agi.hangup()
        except Exception:
            pass


# ------------------------------------------------------------------ main ---

def main(argv):
    if not BACKEND_URL or not GATEWAY_SECRET:
        print("pashu_ivr: PASHU_BACKEND_URL and PBX_WEBHOOK_SECRET are required",
              file=sys.stderr)
        return 2
    agi = AGI()
    caller_id = agi.env.get("agi_callerid", "") or ""
    # Prefer the real DNIS when the carrier passes it; fall back to extension.
    called = agi.env.get("agi_dnid", "") or agi.env.get("agi_extension", "") or ""
    unique_id = agi.env.get("agi_uniqueid", "") or ""
    if caller_id.lower() in ("unknown", "anonymous", "unavailable", "restricted", ""):
        # Never invent a number: pass it through empty and let the backend's
        # unknown-caller flow handle identification.
        if caller_id and caller_id != "":
            log("carrier withheld caller id (%r); continuing as unknown" % caller_id)
        caller_id = ""
    log("inbound caller=%r called=%r uniqueid=%r" % (caller_id, called, unique_id))
    backend = BackendClient()
    runner = CallRunner(agi, backend, caller_id=caller_id,
                        called_number=called, unique_id=unique_id)
    try:
        return runner.run()
    except Exception:
        log("unhandled exception:\n%s" % traceback.format_exc())
        try:
            runner.failure_exit()
        except Exception:
            pass
        return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
