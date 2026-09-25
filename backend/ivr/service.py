"""
IVR call state machine (the controller).

    Farmer call -> LANGUAGE_SELECT -> MAIN_MENU
                                        |-- 1: vet   -> RECORDING_CONSENT? -> VET_DIAL
                                        |                  (no answer) -> SURVEY
                                        '-- 2: survey -> SURVEY -> END

State lives in the database (ivr_sessions) keyed by the provider call id, so
webhooks are stateless and survive worker restarts.

Every branch renders real provider markup; nothing is simulated. If the
telephony provider is not configured the webhook answers with a spoken
"service not configured" message and logs the reason.
"""
import json
from datetime import datetime

from . import audit as audit_mod, jobs, location as location_mod, report_service
from .config import settings
from .locales import (dtmf_to_language, language_menu_prompts, meta, speech_hints,
                      stt_language, supported_language_codes, t, tts_voice)
from .providers import get_provider
from .security import encrypt_phone, mask_phone, normalize_phone, phone_hash
from .survey import NOT_PROVIDED, SurveyEngine, get_active_survey
from .stt import is_configured as stt_is_configured

STEPS = ("LANGUAGE_SELECT", "PHONE_CAPTURE", "MAIN_MENU", "RECORDING_CONSENT",
         "VET_DIAL", "SURVEY", "END")


def _row(row, key, default=None):
    try:
        value = row[key]
        return default if value is None else value
    except (IndexError, KeyError, TypeError):
        return default


class IVRService:
    def __init__(self, provider=None, context=None):
        self.provider = provider or get_provider()
        self.context = context or {}

    # ---------------------------------------------------------------- utils --
    def _log(self, conn, call_id, event_type, actor="system", actor_type="SYSTEM",
             report_id=None, details=None):
        audit_mod.log_event(conn, call_id, event_type, actor=actor, actor_type=actor_type,
                            report_id=report_id, details=details)

    def _audit(self, conn, action, entity_type, entity_id, details=None,
               actor_name="IVR System", actor_role="SYSTEM"):
        audit_mod.audit(conn, self.context, action, entity_type, entity_id, details=details,
                        actor_name=actor_name, actor_role=actor_role)

    def _say(self, builder, text, lang):
        voice, language = tts_voice(lang, self.provider.name)
        return builder.say(text, voice=voice, language=language)

    def _action_url(self, path="/ivr/voice"):
        return self.provider.webhook_url(path)

    def _speech_enabled(self):
        """Speech input is only offered when an STT backend really exists."""
        return stt_is_configured()

    def _gather(self, builder, num_digits=1, lang="en", hints=None, input_modes=None, timeout=None):
        modes = tuple(input_modes or ("dtmf",))
        return builder.gather_start(
            action_url=self._action_url("/ivr/voice"),
            num_digits=num_digits,
            timeout=timeout or settings.GATHER_TIMEOUT,
            input_modes=modes,
            speech_hints=speech_hints(lang, hints) if "speech" in modes else None,
            speech_language=stt_language(lang) if "speech" in modes else None,
        )

    # ------------------------------------------------------------ incoming --
    def handle_incoming(self, conn, incoming, values=None):
        values = values or {}
        normalized, status = normalize_phone(incoming.from_raw)
        encrypted = encrypt_phone(normalized)
        phash = phone_hash(normalized)
        ivr_number = settings.IVR_PHONE_NUMBER or settings.TELEPHONY_PHONE_NUMBER or (incoming.to_raw or "")

        now = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")
        row = conn.execute(
            "SELECT * FROM ivr_calls WHERE provider=? AND provider_call_id=?",
            (self.provider.name, incoming.provider_call_id),
        ).fetchone()
        if row:
            call_id = row["id"]
        else:
            cur = conn.execute(
                "INSERT INTO ivr_calls (provider, provider_call_id, ivr_phone_number, "
                "caller_number_encrypted, caller_number_hash, caller_number_masked, caller_number_status, "
                "caller_country, direction, status, started_at, provider_payload) "
                "VALUES (?,?,?,?,?,?,?,?,'inbound','IN_PROGRESS',?,?)",
                (self.provider.name, incoming.provider_call_id, ivr_number, encrypted, phash,
                 mask_phone(normalized), status, incoming.country,
                 now, json.dumps(values, default=str)[:8000]),
            )
            call_id = cur.lastrowid
            conn.execute(
                "INSERT INTO ivr_sessions (call_id, current_step, step_index, language, state) "
                "VALUES (?,'LANGUAGE_SELECT',0,?,?)",
                (call_id, settings.DEFAULT_LANGUAGE, json.dumps({"retries": 0})),
            )
            self._log(conn, call_id, "CALL_RECEIVED", actor=self.provider.name, actor_type="PROVIDER",
                      details={"from_status": status, "masked": mask_phone(normalized), "to": ivr_number})
            self._audit(conn, "IVR_CALL_RECEIVED", "ivr_call", call_id,
                        details={"provider": self.provider.name, "caller_status": status,
                                 "masked_caller": mask_phone(normalized)})

            # Network location, only when the provider actually supplies it
            net_meta = {k: values.get(k) for k in ("Latitude", "Longitude", "lat", "lng", "longitude")
                        if values.get(k)}
            if net_meta.get("Latitude") or net_meta.get("lat"):
                location_mod.capture_from_provider(
                    conn, call_id, self.provider,
                    {"lat": net_meta.get("Latitude") or net_meta.get("lat"),
                     "lng": net_meta.get("Longitude") or net_meta.get("lng")},
                )
        conn.commit()
        return self._render_current_step(conn, call_id)

    # --------------------------------------------------------------- input --
    def handle_input(self, conn, gather, values=None):
        values = values or {}
        call = conn.execute(
            "SELECT * FROM ivr_calls WHERE provider=? AND provider_call_id=?",
            (self.provider.name, gather.provider_call_id or values.get("CallSid") or values.get("CallUUID")),
        ).fetchone()
        if not call:
            # Unknown call: safest possible response is to start over.
            return self._render_unknown_call()
        call_id = call["id"]
        session = conn.execute(
            "SELECT * FROM ivr_sessions WHERE call_id=? ORDER BY id DESC LIMIT 1", (call_id,)
        ).fetchone()
        if not session:
            conn.execute(
                "INSERT INTO ivr_sessions (call_id, current_step, step_index, language, state) VALUES (?,'LANGUAGE_SELECT',0,?,?)",
                (call_id, settings.DEFAULT_LANGUAGE, json.dumps({"retries": 0})),
            )
            conn.commit()
            session = conn.execute("SELECT * FROM ivr_sessions WHERE call_id=? ORDER BY id DESC LIMIT 1",
                                   (call_id,)).fetchone()

        step = session["current_step"]

        # Post-dial callback (farmer <-> vet leg finished)
        dial_status = self._dial_status(values)
        if dial_status:
            return self._after_vet_call(conn, call_id, session, dial_status, values)

        if step == "LANGUAGE_SELECT":
            return self._step_language(conn, call_id, session, gather)
        if step == "PHONE_CAPTURE":
            return self._step_phone(conn, call_id, session, gather)
        if step == "MAIN_MENU":
            return self._step_menu(conn, call_id, session, gather)
        if step == "RECORDING_CONSENT":
            return self._step_recording_consent(conn, call_id, session, gather)
        if step == "SURVEY":
            return self._step_survey(conn, call_id, session, gather)
        return self._render_end(conn, call_id, session)

    def _dial_status(self, values):
        for key in ("DialCallStatus", "DialStatus", "DialBLegStatus", "DialActionStatus"):
            if values.get(key):
                return values.get(key)
        return None

    def _render_unknown_call(self):
        builder = self.provider.response()
        self._say(builder, t("en", "error"), "en")
        builder.hangup()
        return self.provider.render(builder)

    def _render_current_step(self, conn, call_id, error=False):
        """Render whatever the session is currently waiting for."""
        session = conn.execute(
            "SELECT * FROM ivr_sessions WHERE call_id=? ORDER BY id DESC LIMIT 1", (call_id,)
        ).fetchone()
        if not session:
            conn.execute(
                "INSERT INTO ivr_sessions (call_id, current_step, step_index, language, state) "
                "VALUES (?,'LANGUAGE_SELECT',0,?,?)", (call_id, settings.DEFAULT_LANGUAGE, json.dumps({"retries": 0})))
            conn.commit()
            session = conn.execute("SELECT * FROM ivr_sessions WHERE call_id=? ORDER BY id DESC LIMIT 1",
                                   (call_id,)).fetchone()
        step = session["current_step"]
        if step == "LANGUAGE_SELECT":
            return self._render_language_menu(conn, call_id, session, error=error)
        if step == "PHONE_CAPTURE":
            return self._render_phone_capture(conn, call_id, session, error=error)
        if step == "MAIN_MENU":
            return self._render_main_menu(conn, call_id, session, error=error)
        if step == "RECORDING_CONSENT":
            return self._render_recording_consent(conn, call_id, session, error=error)
        if step == "SURVEY":
            builder = self.provider.response()
            self._render_question(builder, conn, call_id, session)
            return self.provider.render(builder)
        return self._render_end(conn, call_id, session)

    # -------------------------------------------------------------- steps ---
    def _set_step(self, conn, session_id, step, step_index=None, state=None):
        conn.execute(
            "UPDATE ivr_sessions SET current_step=?, step_index=COALESCE(?, step_index), state=?, "
            "updated_at=datetime('now') WHERE id=?",
            (step, step_index, json.dumps(state or {}), session_id),
        )
        return conn.execute("SELECT * FROM ivr_sessions WHERE id=?", (session_id,)).fetchone()

    def _step_language(self, conn, call_id, session, gather):
        state = json.loads(session["state"] or "{}")
        languages = supported_language_codes()
        digits = (gather.digits or "").strip()
        speech = (gather.speech or "").strip()

        chosen = None
        source = "DTMF"
        if digits:
            chosen = dtmf_to_language(languages).get(digits)
        elif speech:
            chosen = self._match_language(speech, languages)
            source = "SPEECH"

        if not chosen:
            state["retries"] = int(state.get("retries", 0)) + 1
            self._set_step(conn, session["id"], "LANGUAGE_SELECT", 0, state)
            conn.commit()
            if state["retries"] > settings.MAX_RETRIES:
                chosen = settings.DEFAULT_LANGUAGE
                source = "DEFAULT"
                self._log(conn, call_id, "LANGUAGE_DEFAULTED",
                          details={"language": chosen, "retries": state["retries"]})
            else:
                self._log(conn, call_id, "LANGUAGE_INVALID_INPUT", details={"digits": digits, "speech": speech})
                return self._render_language_menu(conn, call_id, session, error=True)

        conn.execute(
            "UPDATE ivr_calls SET language=?, language_source=?, updated_at=datetime('now') WHERE id=?",
            (chosen, source, call_id),
        )
        session = self._set_step(conn, session["id"], session["current_step"], None,
                                 {**state, "retries": 0})
        conn.execute("UPDATE ivr_sessions SET language=?, updated_at=datetime('now') WHERE id=?",
                     (chosen, session["id"]))
        session = conn.execute("SELECT * FROM ivr_sessions WHERE id=?", (session["id"],)).fetchone()
        self._log(conn, call_id, "LANGUAGE_SELECTED", actor="farmer", actor_type="FARMER",
                  details={"language": chosen, "source": source})
        self._audit(conn, "IVR_LANGUAGE_SELECTED", "ivr_call", call_id, details={"language": chosen})

        call = conn.execute("SELECT * FROM ivr_calls WHERE id=?", (call_id,)).fetchone()
        if _row(call, "caller_number_status") != "CAPTURED":
            self._set_step(conn, session["id"], "PHONE_CAPTURE", 0, {**state, "retries": 0})
            conn.commit()
            return self._render_phone_capture(conn, call_id, session)
        self._set_step(conn, session["id"], "MAIN_MENU", 0, {**state, "retries": 0})
        conn.commit()
        return self._render_main_menu(conn, call_id, session)

    def _match_language(self, speech, languages):
        text = (speech or "").lower()
        for code in languages:
            m = meta(code)
            for name in (m.get("name"), m.get("native_name"), code):
                if name and str(name).lower() in text:
                    return code
        return None

    def _render_language_menu(self, conn, call_id, session, error=False):
        builder = self.provider.response()
        self._say(builder, t("en", "welcome"), "en")
        if error:
            self._say(builder, t("en", "invalid_language"), "en")
        languages = supported_language_codes()
        self._gather(builder, num_digits=1, lang="en",
                     input_modes=("dtmf", "speech") if self._speech_enabled() else ("dtmf",),
                     hints=[meta(c).get("name") for c in languages])
        for code, prompt in language_menu_prompts(languages):
            self._say(builder, prompt, code)
        builder.gather_end()
        builder.redirect(self._action_url("/ivr/voice"))
        return self.provider.render(builder)

    def _render_phone_capture(self, conn, call_id, session, error=False):
        lang = session["language"] or "en"
        builder = self.provider.response()
        if error:
            self._say(builder, t(lang, "phone_invalid"), lang)
        self._gather(builder, num_digits=10, lang=lang,
                     input_modes=("dtmf", "speech") if self._speech_enabled() else ("dtmf",))
        self._say(builder, t(lang, "phone_request"), lang)
        builder.gather_end()
        builder.redirect(self._action_url("/ivr/voice"))
        return self.provider.render(builder)

    def _step_phone(self, conn, call_id, session, gather):
        from .survey import SurveyEngine  # local import (engine is stateless)

        engine = SurveyEngine({"questions": [{
            "key": "callback_number", "type": "phone", "required": True,
            "text": {"en": "phone"}, "map_to": "farmer.phone",
        }]}, session["language"] or "en")
        answer = engine.normalize({"type": "phone", "allow_unknown": False}, gather)
        if not answer.ok:
            state = json.loads(session["state"] or "{}")
            state["retries"] = int(state.get("retries", 0)) + 1
            self._set_step(conn, session["id"], "PHONE_CAPTURE", 0, state)
            conn.commit()
            if state["retries"] > settings.MAX_RETRIES:
                self._log(conn, call_id, "PHONE_CAPTURE_FAILED", details={"retries": state["retries"]})
                return self._render_main_menu(conn, call_id, session)
            return self._render_phone_capture(conn, call_id, session, error=True)

        number = answer.value
        conn.execute(
            "UPDATE ivr_calls SET caller_number_encrypted=?, caller_number_hash=?, caller_number_masked=?, "
            "caller_number_status=?, updated_at=datetime('now') WHERE id=?",
            (encrypt_phone(number), phone_hash(number), mask_phone(number), "USER_PROVIDED", call_id),
        )
        self._log(conn, call_id, "PHONE_CAPTURED", actor="farmer", actor_type="FARMER",
                  details={"masked": mask_phone(number), "mode": answer.mode})
        state = json.loads(session["state"] or "{}")
        state["retries"] = 0
        session = self._set_step(conn, session["id"], "MAIN_MENU", 0, state)
        conn.commit()
        return self._render_main_menu(conn, call_id, session)

    def _render_main_menu(self, conn, call_id, session, error=False):
        lang = session["language"] or "en"
        builder = self.provider.response()
        if error:
            self._say(builder, t(lang, "invalid_option"), lang)
        self._gather(builder, num_digits=1, lang=lang,
                     input_modes=("dtmf", "speech") if self._speech_enabled() else ("dtmf",),
                     hints=["veterinarian", "report", "doctor"])
        self._say(builder, t(lang, "main_menu"), lang)
        builder.gather_end()
        builder.redirect(self._action_url("/ivr/voice"))
        return self.provider.render(builder)

    def _step_menu(self, conn, call_id, session, gather):
        lang = session["language"] or "en"
        digits = (gather.digits or "").strip()
        speech = (gather.speech or "").strip().lower()
        state = json.loads(session["state"] or "{}")

        choice = digits
        if not choice and speech:
            if any(w in speech for w in ("vet", "doctor", "डॉक्टर", "डॉक्टरां", "డాక్టర్")):
                choice = "1"
            elif any(w in speech for w in ("report", "problem", "sick", "रिपोर्ट", "రిపోర్ట్", "अहवाल")):
                choice = "2"

        if choice == "1":
            conn.execute("UPDATE ivr_calls SET flow='VET_CONNECT', updated_at=datetime('now') WHERE id=?", (call_id,))
            self._log(conn, call_id, "MENU_VET_SELECTED", actor="farmer", actor_type="FARMER")
            if settings.RECORDING_ENABLED and settings.RECORDING_CONSENT_REQUIRED:
                self._set_step(conn, session["id"], "RECORDING_CONSENT", 0, state)
                conn.commit()
                return self._render_recording_consent(conn, call_id, session)
            return self._start_vet_call(conn, call_id, session)
        if choice == "2":
            conn.execute("UPDATE ivr_calls SET flow='SURVEY', updated_at=datetime('now') WHERE id=?", (call_id,))
            self._log(conn, call_id, "MENU_SURVEY_SELECTED", actor="farmer", actor_type="FARMER")
            return self._start_survey(conn, call_id, session)
        if choice == "9":
            return self._render_main_menu(conn, call_id, session)

        state["retries"] = int(state.get("retries", 0)) + 1
        self._set_step(conn, session["id"], "MAIN_MENU", 0, state)
        conn.commit()
        if state["retries"] > settings.MAX_RETRIES:
            self._log(conn, call_id, "MENU_ABANDONED_NO_INPUT", details={"retries": state["retries"]})
            return self._render_end(conn, call_id, session, say_key="goodbye")
        return self._render_main_menu(conn, call_id, session, error=True)

    # ------------------------------------------------------------ recording --
    def _render_recording_consent(self, conn, call_id, session, error=False):
        lang = session["language"] or "en"
        builder = self.provider.response()
        if error:
            self._say(builder, t(lang, "invalid_input"), lang)
        self._gather(builder, num_digits=1, lang=lang,
                     input_modes=("dtmf", "speech") if self._speech_enabled() else ("dtmf",),
                     hints=["yes", "no"])
        self._say(builder, t(lang, "recording_consent"), lang)
        builder.gather_end()
        builder.redirect(self._action_url("/ivr/voice"))
        return self.provider.render(builder)

    def _step_recording_consent(self, conn, call_id, session, gather):
        lang = session["language"] or "en"
        state = json.loads(session["state"] or "{}")
        digits = (gather.digits or "").strip()
        speech = (gather.speech or "").strip().lower()
        consent = None
        if digits == "1" or (not digits and any(w in speech for w in ("yes", "हाँ", "हो", "అవును"))):
            consent = 1
        elif digits == "2" or (not digits and any(w in speech for w in ("no", "नहीं", "नाही", "కాదు"))):
            consent = 0
        if consent is None:
            state["retries"] = int(state.get("retries", 0)) + 1
            self._set_step(conn, session["id"], "RECORDING_CONSENT", 0, state)
            conn.commit()
            if state["retries"] > settings.MAX_RETRIES:
                consent = 0  # no consent -> continue without recording
                self._log(conn, call_id, "RECORDING_CONSENT_DEFAULTED", details={"consent": 0})
            else:
                return self._render_recording_consent(conn, call_id, session, error=True)

        conn.execute(
            "UPDATE ivr_calls SET consent_recording=?, consent_recording_at=datetime('now'), "
            "recording_requested=? WHERE id=?",
            (consent, consent, call_id),
        )
        self._log(conn, call_id, "RECORDING_CONSENT", actor="farmer", actor_type="FARMER",
                  details={"consent": bool(consent)})
        state["retries"] = 0
        self._set_step(conn, session["id"], "VET_DIAL", 0, state)
        conn.commit()
        return self._start_vet_call(conn, call_id, session, consent=bool(consent))

    # -------------------------------------------------------------- vet call --
    def _start_vet_call(self, conn, call_id, session, consent=False):
        from .rules import assign_vet

        lang = session["language"] or "en"
        call = conn.execute("SELECT * FROM ivr_calls WHERE id=?", (call_id,)).fetchone()
        district = None
        phone = None
        try:
            from .security import decrypt_phone
            phone = decrypt_phone(call["caller_number_encrypted"])
        except Exception:
            phone = None
        if phone:
            user = conn.execute("SELECT district FROM users WHERE mobile LIKE ?", (f"%{phone.lstrip('+')[-10:]}",)).fetchone()
            district = user["district"] if user else None

        vet, reason = assign_vet(conn, district=district, require_available=True)
        builder = self.provider.response()
        if not vet:
            self._log(conn, call_id, "VET_UNAVAILABLE", details={"reason": reason, "district": district})
            self._audit(conn, "IVR_VET_UNAVAILABLE", "ivr_call", call_id, details={"reason": reason})
            self._say(builder, t(lang, "vet_unavailable"), lang)
            state = json.loads(session["state"] or "{}")
            self._set_step(conn, session["id"], "SURVEY", 0, state)
            conn.commit()
            builder.redirect(self._action_url("/ivr/voice"))
            return self.provider.render(builder)

        vet_number, _status = normalize_phone(vet["mobile"])
        if not vet_number:
            self._log(conn, call_id, "VET_NUMBER_INVALID", details={"vet_id": vet["id"]})
            self._say(builder, t(lang, "vet_unavailable"), lang)
            state = json.loads(session["state"] or "{}")
            self._set_step(conn, session["id"], "SURVEY", 0, state)
            conn.commit()
            builder.redirect(self._action_url("/ivr/voice"))
            return self.provider.render(builder)

        record_call = bool(settings.RECORDING_ENABLED and (consent or not settings.RECORDING_CONSENT_REQUIRED))
        conn.execute(
            "UPDATE ivr_calls SET status='VET_CONNECTING', flow='VET_CONNECT', updated_at=datetime('now') WHERE id=?",
            (call_id,),
        )
        self._log(conn, call_id, "VET_CONNECTING", details={"vet_id": vet["id"], "reason": reason,
                                                            "recording": record_call})
        self._audit(conn, "IVR_VET_CONNECTING", "ivr_call", call_id,
                    details={"vet_id": vet["id"], "vet": vet["full_name"]})
        conn.execute(
            "INSERT INTO ivr_responses (call_id, session_id, question_key, question_text, input_mode, "
            "normalized_value, provenance) VALUES (?,?,'vet_assignment','Veterinarian assignment','SYSTEM',?,'SYSTEM_GENERATED')",
            (call_id, session["id"], f"vet:{vet['id']}:{vet['full_name']}"),
        )
        state = json.loads(session["state"] or "{}")
        state["vet_id"] = vet["id"]
        state["vet_name"] = vet["full_name"]
        self._set_step(conn, session["id"], "VET_DIAL", 0, state)
        conn.commit()

        self._say(builder, t(lang, "vet_connecting"), lang)
        builder.dial(
            vet_number,
            action_url=self._action_url("/ivr/voice"),
            timeout=settings.VET_RING_TIMEOUT,
            caller_id=settings.TELEPHONY_PHONE_NUMBER or settings.IVR_PHONE_NUMBER,
            record=record_call,
        )
        builder.redirect(self._action_url("/ivr/voice"))
        return self.provider.render(builder)

    def _after_vet_call(self, conn, call_id, session, dial_status, values):
        lang = session["language"] or "en"
        answered = str(dial_status).lower() in ("completed", "answered", "answer", "success")
        duration = values.get("DialCallDuration") or values.get("Duration")
        try:
            duration = int(duration) if duration else None
        except (TypeError, ValueError):
            duration = None

        conn.execute(
            "UPDATE ivr_calls SET status=?, updated_at=datetime('now') WHERE id=?",
            ("VET_CONNECTED" if answered else "IN_PROGRESS", call_id),
        )
        self._log(conn, call_id, "VET_CALL_FINISHED", details={"status": dial_status, "duration": duration,
                                                               "answered": answered})
        self._audit(conn, "IVR_VET_CALL_FINISHED", "ivr_call", call_id,
                    details={"status": dial_status, "answered": answered, "duration": duration})

        if answered:
            conn.execute(
                "INSERT INTO ivr_transcripts (call_id, participant_role, language, text, source, provenance) "
                "VALUES (?, 'SYSTEM', ?, ?, 'SYSTEM', 'SYSTEM_GENERATED')",
                (call_id, lang,
                 f"Veterinarian consultation completed (duration {duration or 0}s). "
                 "Transcript attached automatically when speech-to-text completes."),
            )
            conn.commit()
            report_service.finalise_call(conn, call_id, context=self.context)
            builder = self.provider.response()
            self._say(builder, t(lang, "vet_call_ended"), lang)
            return self.provider.render(self._finish_builder(builder, lang))

        # No answer / busy / failed -> automated survey instead of hanging up
        state = json.loads(session["state"] or "{}")
        state["retries"] = 0
        self._set_step(conn, session["id"], "SURVEY", 0, state)
        conn.commit()
        builder = self.provider.response()
        self._say(builder, t(lang, "vet_no_answer"), lang)
        builder.redirect(self._action_url("/ivr/voice"))
        return self.provider.render(builder)

    def _finish_builder(self, builder, lang):
        builder.hangup()
        return builder

    # ---------------------------------------------------------------- survey --
    def _start_survey(self, conn, call_id, session):
        survey_row, definition = get_active_survey(conn)
        state = json.loads(session["state"] or "{}")
        state["survey_id"] = survey_row["id"] if survey_row else None
        state["retries"] = 0
        self._set_step(conn, session["id"], "SURVEY", 0, state)
        conn.execute(
            "UPDATE ivr_calls SET flow='SURVEY', status='SURVEY', updated_at=datetime('now') WHERE id=?", (call_id,)
        )
        self._log(conn, call_id, "SURVEY_STARTED", actor="farmer", actor_type="FARMER",
                  details={"survey_id": state.get("survey_id")})
        self._audit(conn, "IVR_SURVEY_STARTED", "ivr_call", call_id, details={"survey_id": state.get("survey_id")})
        conn.commit()
        session = conn.execute("SELECT * FROM ivr_sessions WHERE id=?", (session["id"],)).fetchone()
        builder = self.provider.response()
        self._say(builder, t(session["language"] or "en", "survey_intro"), session["language"] or "en")
        self._render_question(builder, conn, call_id, session)
        return self.provider.render(builder)

    def _engine(self, conn, session):
        survey_row, definition = get_active_survey(conn)
        return SurveyEngine(definition, session["language"] or "en"), definition

    def _current_question(self, conn, call_id, session):
        """The pending question is the first visible question that has not
        been answered yet - progress is tracked by question key, so it stays
        correct even when conditional questions change the visible set."""
        engine, definition = self._engine(conn, session)
        answers = report_service.answers_map(conn, call_id)
        questions = engine.visible_questions(answers)
        pending = [q for q in questions if q["key"] not in answers]
        return engine, definition, (pending[0] if pending else None)

    def _render_question(self, builder, conn, call_id, session, error_key=None, confirm_ctx=None):
        engine, definition, question = self._current_question(conn, call_id, session)
        lang = session["language"] or "en"
        if not question:
            self._say(builder, t(lang, "survey_complete"), lang)
            self._complete_survey(conn, call_id, session)
            builder.hangup()
            return builder

        if confirm_ctx:
            self._say(builder, t(lang, "confirm_heard", value=confirm_ctx.get("label") or confirm_ctx.get("value")), lang)
            self._say(builder, t(lang, "confirm_instruction"), lang)
            self._gather(builder, num_digits=1, lang=lang,
                         input_modes=("dtmf", "speech") if self._speech_enabled() else ("dtmf",),
                         hints=["yes", "no"])
            builder.gather_end()
            builder.redirect(self._action_url("/ivr/voice"))
            return builder

        if error_key:
            self._say(builder, t(lang, error_key), lang)
        self._say(builder, engine.prompt(question), lang)
        if question.get("options"):
            self._say(builder, engine.option_menu_text(question), lang)
        unknown_digit = engine.unknown_digit(question)
        if unknown_digit:
            self._say(builder, t(lang, "press_unknown"), lang)
        self._say(builder, engine.nav_help(question), lang)

        modes = ("dtmf",)
        if self._speech_enabled() and question.get("type") in ("speech", "menu", "boolean", "numeric", "phone"):
            modes = ("dtmf", "speech")
        num_digits = 1 if question.get("type") in ("menu", "boolean", "speech") else int(question.get("max_digits") or 3)
        self._gather(builder, num_digits=num_digits, lang=lang, input_modes=modes,
                     hints=[o.get("value") for o in (question.get("options") or [])] or None)
        builder.gather_end()
        builder.redirect(self._action_url("/ivr/voice"))
        return builder

    def _step_survey(self, conn, call_id, session, gather):
        lang = session["language"] or "en"
        state = json.loads(session["state"] or "{}")
        engine, definition, question = self._current_question(conn, call_id, session)

        # --- awaiting confirmation of a previous answer -------------------
        if state.get("awaiting_confirm"):
            ctx = state["awaiting_confirm"]
            digits = (gather.digits or "").strip()
            speech = (gather.speech or "").strip().lower()
            if digits == "1" or (not digits and any(w in speech for w in ("yes", "हाँ", "हो", "అవును"))):
                self._commit_answer(conn, call_id, session, ctx["question_key"], ctx)
                state.pop("awaiting_confirm", None)
                state["retries"] = 0
                self._set_step(conn, session["id"], "SURVEY", None, state)
                conn.commit()
                return self._render_survey_step(conn, call_id, session)
            if digits == "2" or (not digits and any(w in speech for w in ("no", "नहीं", "नाही", "కాదు"))):
                state.pop("awaiting_confirm", None)
                state["retries"] = 0
                self._set_step(conn, session["id"], "SURVEY", None, state)
                conn.commit()
                builder = self.provider.response()
                self._render_question(builder, conn, call_id, session)
                return self.provider.render(builder)
            # anything else: repeat the confirmation
            builder = self.provider.response()
            self._say(builder, t(lang, "invalid_input"), lang)
            self._render_question(builder, conn, call_id, session, confirm_ctx=ctx)
            return self.provider.render(builder)

        if not question:
            builder = self.provider.response()
            self._say(builder, t(lang, "survey_complete"), lang)
            self._complete_survey(conn, call_id, session)
            builder.hangup()
            return self.provider.render(builder)

        answer = engine.normalize(question, gather, min_confidence=settings.STT_MIN_CONFIDENCE)

        if answer.error == "back":
            last = conn.execute(
                "SELECT * FROM ivr_responses WHERE call_id=? AND input_mode!='SYSTEM' "
                "ORDER BY id DESC LIMIT 1", (call_id,)).fetchone()
            if last:
                conn.execute("DELETE FROM ivr_responses WHERE id=?", (last["id"],))
                self._log(conn, call_id, "SURVEY_BACK", actor="farmer", actor_type="FARMER",
                          details={"undone": last["question_key"]})
            else:
                self._log(conn, call_id, "SURVEY_BACK", actor="farmer", actor_type="FARMER",
                          details={"undone": None})
            self._set_step(conn, session["id"], "SURVEY", None, {**state, "retries": 0})
            conn.commit()
            return self._render_survey_step(conn, call_id, session)

        if answer.error in ("repeat", "no_input", "invalid", "low_confidence"):
            state["retries"] = int(state.get("retries", 0)) + 1
            self._set_step(conn, session["id"], "SURVEY", None, state)
            conn.commit()
            self._log(conn, call_id, "SURVEY_INPUT_ERROR", actor="farmer", actor_type="FARMER",
                      details={"question": question["key"], "error": answer.error,
                               "retries": state["retries"]})
            if state["retries"] > settings.MAX_RETRIES:
                # Never trap the farmer: record "Not provided" and move on
                self._commit_answer(conn, call_id, session, question["key"], {
                    "value": NOT_PROVIDED, "label": NOT_PROVIDED, "mode": "SKIPPED",
                    "raw": answer.raw, "question_text": engine.prompt(question),
                    "confidence": answer.confidence, "transcript": answer.transcript,
                })
                state["retries"] = 0
                self._set_step(conn, session["id"], "SURVEY", None, state)
                conn.commit()
                return self._render_survey_step(conn, call_id, session)
            builder = self.provider.response()
            error_key = {"no_input": "no_input", "invalid": "invalid_input",
                         "low_confidence": "invalid_input", "repeat": None}.get(answer.error)
            self._render_question(builder, conn, call_id, session, error_key=error_key)
            return self.provider.render(builder)

        # ---- valid answer ---------------------------------------------------
        if question.get("confirm"):
            state["awaiting_confirm"] = {
                "question_key": question["key"],
                "question_text": engine.prompt(question),
                "value": answer.value,
                "label": answer.label or str(answer.value),
                "mode": answer.mode,
                "raw": answer.raw,
                "confidence": answer.confidence,
                "transcript": answer.transcript,
            }
            self._set_step(conn, session["id"], "SURVEY", None, state)
            conn.commit()
            builder = self.provider.response()
            self._render_question(builder, conn, call_id, session, confirm_ctx=state["awaiting_confirm"])
            return self.provider.render(builder)

        self._commit_answer(conn, call_id, session, question["key"], {
            "value": answer.value, "label": answer.label or str(answer.value),
            "mode": answer.mode, "raw": answer.raw, "confidence": answer.confidence,
            "transcript": answer.transcript, "question_text": engine.prompt(question),
        })
        state["retries"] = 0
        self._set_step(conn, session["id"], "SURVEY", None, state)
        conn.commit()
        return self._render_survey_step(conn, call_id, session)

    def _render_survey_step(self, conn, call_id, session):
        builder = self.provider.response()
        engine, definition, question = self._current_question(conn, call_id, session)
        if not question:
            self._say(builder, t(session["language"] or "en", "survey_complete"), session["language"] or "en")
            self._complete_survey(conn, call_id, session)
            builder.hangup()
            return self.provider.render(builder)
        self._render_question(builder, conn, call_id, session)
        return self.provider.render(builder)

    def _commit_answer(self, conn, call_id, session, question_key, ctx):
        conn.execute(
            "INSERT INTO ivr_responses (call_id, session_id, question_key, question_text, input_mode, "
            "raw_input, dtmf_value, transcript, normalized_value, option_label, confidence, provenance) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,'FARMER_REPORTED')",
            (call_id, session["id"], question_key, ctx.get("question_text"),
             ctx.get("mode") or "DTMF",
             str(ctx.get("raw")) if ctx.get("raw") is not None else None,
             ctx.get("raw") if (ctx.get("mode") == "DTMF") else None,
             ctx.get("transcript"),
             str(ctx.get("value")) if ctx.get("value") is not None else None,
             ctx.get("label"), ctx.get("confidence")),
        )
        self._log(conn, call_id, "SURVEY_ANSWER", actor="farmer", actor_type="FARMER",
                  details={"question": question_key, "value": ctx.get("value"), "mode": ctx.get("mode")})
        conn.commit()

    def _complete_survey(self, conn, call_id, session):
        conn.execute("UPDATE ivr_sessions SET completed=1, current_step='END', updated_at=datetime('now') WHERE id=?",
                     (session["id"],))
        self._log(conn, call_id, "SURVEY_COMPLETED", actor="farmer", actor_type="FARMER")
        self._audit(conn, "IVR_SURVEY_COMPLETED", "ivr_call", call_id)
        conn.commit()
        report_service.finalise_call(conn, call_id, context=self.context)

    def _render_end(self, conn, call_id, session, say_key="goodbye"):
        lang = session["language"] or "en"
        builder = self.provider.response()
        self._say(builder, t(lang, say_key), lang)
        builder.hangup()
        return self.provider.render(builder)

    # ------------------------------------------------------ status/recording --
    def handle_status(self, conn, event, values=None):
        call = conn.execute(
            "SELECT * FROM ivr_calls WHERE provider=? AND provider_call_id=?",
            (self.provider.name, event.provider_call_id),
        ).fetchone()
        if not call:
            return None
        call = dict(call)
        status = (event.status or "").lower()
        ended_states = ("completed", "failed", "busy", "no-answer", "no_answer", "canceled", "cancelled")
        mapped = {
            "completed": "COMPLETED",
            "failed": "FAILED",
            "busy": "BUSY",
            "no-answer": "NO_ANSWER",
            "no_answer": "NO_ANSWER",
            "canceled": "COMPLETED",
            "cancelled": "COMPLETED",
        }.get(status, None)

        conn.execute(
            "UPDATE ivr_calls SET status=COALESCE(?, status), duration_seconds=COALESCE(?, duration_seconds), "
            "ended_at=datetime('now'), updated_at=datetime('now') WHERE id=?",
            (mapped, event.duration, call["id"]),
        )
        self._log(conn, call["id"], "CALL_STATUS", actor=self.provider.name, actor_type="PROVIDER",
                  details={"provider_status": event.status, "duration": event.duration})
        conn.commit()

        session = conn.execute("SELECT * FROM ivr_sessions WHERE call_id=? ORDER BY id DESC LIMIT 1",
                               (call["id"],)).fetchone()
        if mapped and session and not session["completed"]:
            partial = mapped != "COMPLETED"
            self._log(conn, call["id"], "CALL_ENDED", actor=self.provider.name, actor_type="PROVIDER",
                      details={"status": mapped, "partial": partial})
            report_service.finalise_call(conn, call["id"], context=self.context,
                                         status_override=("PARTIALLY_COMPLETED" if partial else "COMPLETED"))
        return mapped

    def handle_recording(self, conn, event, values=None):
        call = conn.execute(
            "SELECT * FROM ivr_calls WHERE provider=? AND provider_call_id=?",
            (self.provider.name, event.provider_call_id),
        ).fetchone()
        if not call:
            return None
        cur = conn.execute(
            "INSERT INTO ivr_recordings (call_id, provider_recording_id, storage_backend, storage_uri, "
            "duration_seconds, status, consent_obtained) VALUES (?,?,?,?,?,?,?)",
            (call["id"], event.recording_id, settings.RECORDING_STORAGE or "provider", event.url,
             event.duration, "AVAILABLE" if event.url else "PENDING",
             int(bool(call["consent_recording"]))),
        )
        conn.execute("UPDATE ivr_calls SET recording_requested=1 WHERE id=?", (call["id"],))
        self._log(conn, call["id"], "RECORDING_AVAILABLE", actor=self.provider.name, actor_type="PROVIDER",
                  details={"recording_id": event.recording_id, "duration": event.duration})
        conn.commit()
        recording_id = cur.lastrowid
        if settings.STT_PROVIDER and settings.STT_PROVIDER != "none":
            jobs.enqueue(conn, "TRANSCRIBE", {"call_id": call["id"], "recording_id": recording_id},
                         call_id=call["id"], dedupe_key=f"transcribe-{recording_id}")
            conn.commit()
        return recording_id

    def handle_transcription(self, conn, data, values=None):
        call = conn.execute(
            "SELECT * FROM ivr_calls WHERE provider=? AND provider_call_id=?",
            (self.provider.name, data.get("provider_call_id")),
        ).fetchone()
        if not call:
            return None
        from .stt import store_provider_transcript
        row_id = store_provider_transcript(
            conn, call["id"], data.get("text") or "", language=call["language"] or "en",
            participant_role="FARMER", provenance="FARMER_REPORTED",
            provider=f"{self.provider.name}_native",
        )
        self._log(conn, call["id"], "TRANSCRIPT_CREATED", actor=self.provider.name, actor_type="PROVIDER",
                  details={"chars": len(data.get("text") or "")})
        conn.commit()
        jobs.enqueue(conn, "SUMMARISE", {"call_id": call["id"]}, call_id=call["id"],
                     dedupe_key=f"summarise-{call['id']}")
        conn.commit()
        return row_id
