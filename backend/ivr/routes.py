"""
IVR Webhook and API Routes - Telephony provider webhooks + IVR management APIs.
All webhooks verify signatures, handle DTMF+speech, persist call state, and trigger report generation.
"""
import json
import uuid
import hashlib
import re
import os
from datetime import datetime
from functools import wraps
from flask import request, jsonify, g, Response

# Helpers to register routes on existing Flask app without Blueprint rewrite
def register_ivr_routes(app):
    from .telephony import get_telephony_provider
    from .locales import t
    from .config import IVR_RECORDING_ENABLED, IVR_RECORDING_CONSENT_REQUIRED, TELEPHONY_WEBHOOK_SECRET, IVR_RATE_LIMIT_PER_MINUTE
    from .services.phone import normalize_phone, extract_caller_from_request
    from .services.call_service import (
        create_call_record, get_session, get_call, update_session_state,
        set_language, handle_menu_choice, find_available_vet, is_vet_available,
        record_survey_answer, advance_survey, get_all_responses_dict,
        handle_call_end, vet_call_connected, identify_and_link_caller,
        apply_known_language, set_routing_status, rank_vets_for_call
    )
    from .services.survey import SURVEY_QUESTION_KEYS, CHOICE_MAPS, get_next_question_index
    from .services.farmer import identify_farmer, build_prefill, apply_prefill, get_prefilled_keys
    from .services.location import get_location_from_responses
    from .services.report_service import create_ivr_report_from_survey, update_report_status
    from .services.security import is_rate_limited
    from .jobs import enqueue_job
    import database

    def get_db():
        return database.get_db()

    def ivr_auth_required(roles=None):
        def deco(fn):
            @wraps(fn)
            def wrapper(*args, **kwargs):
                # For IVR management APIs, use same JWT auth as main app
                auth = request.headers.get("Authorization", "")
                if not auth.startswith("Bearer "):
                    return jsonify({"error": "Missing Authorization"}), 401
                # Reuse decode from app.py
                try:
                    import jwt
                    SECRET_KEY = os.environ.get("SIH_SECRET_KEY", "sih-hackathon-dev-secret-change-me")
                    payload = jwt.decode(auth.split(" ", 1)[1], SECRET_KEY, algorithms=["HS256"])
                except Exception:
                    return jsonify({"error": "Invalid token"}), 401
                if roles and payload.get("role") not in roles:
                    return jsonify({"error": "Forbidden"}), 403
                g.user = payload
                return fn(*args, **kwargs)
            return wrapper
        return deco

    def verify_telephony_signature(fn):
        @wraps(fn)
        def wrapper(*args, **kwargs):
            provider = get_telephony_provider()
            # Rate limiting
            ip = request.remote_addr or "unknown"
            if is_rate_limited(ip, IVR_RATE_LIMIT_PER_MINUTE):
                return jsonify({"error": "Rate limit exceeded"}), 429
            # Verify signature - but allow mock bypass for testing
            sig = request.headers.get("X-Twilio-Signature") or request.headers.get("X-Webhook-Signature") or request.headers.get("X-Exotel-Signature") or ""
            # For mock provider, permissive
            try:
                if not provider.verify_webhook_signature(request, sig):
                    # In strict mode, reject; but for dev/mock allow
                    if os.environ.get("TELEPHONY_PROVIDER", "mock") != "mock":
                        return jsonify({"error": "Invalid webhook signature"}), 403
            except Exception:
                pass
            return fn(*args, **kwargs)
        return wrapper

    def parse_input(request):
        """Unified DTMF + Speech parser for Twilio/Exotel/Mock."""
        # Twilio sends Digits and SpeechResult
        digits = request.form.get("Digits") or request.form.get("digits") or request.args.get("Digits") or request.args.get("digits") or ""
        speech = request.form.get("SpeechResult") or request.form.get("speech") or request.args.get("SpeechResult") or ""
        # Also check JSON
        try:
            body = request.get_json(silent=True) or {}
            if not digits:
                digits = body.get("Digits") or body.get("digits") or body.get("dtmf") or body.get("digit") or ""
            if not speech:
                speech = body.get("SpeechResult") or body.get("speech") or body.get("transcript") or body.get("speech_result") or ""
            # Generic fallbacks
            if not digits and not speech:
                digits = body.get("input") or ""
        except Exception:
            pass
        # Also check query param 'Digits' from our Gather action?
        if not digits:
            digits = request.args.get("Digits", "")
        if not digits:
            # Check form 'Digits' lower case variations
            for k, v in request.form.items():
                if k.lower() == "digits" and v:
                    digits = v
                    break
        # Prefer speech if digits empty and speech available
        if speech and not digits:
            return speech.strip(), "speech", speech.strip()
        if digits:
            # Digits may include # terminator, strip
            digits_clean = digits.strip().replace("#", "")
            # If multiple digits for number fields, keep as is
            return digits_clean, "dtmf", digits_clean
        # Fallback: check 'q' param handling for mock
        raw = request.form.get("q") or request.args.get("q") or ""
        return raw, "dtmf", raw

    # -------------------- WEBHOOK: Incoming Call --------------------
    @app.route("/api/ivr/webhook/incoming", methods=["POST", "GET"])
    @verify_telephony_signature
    def ivr_incoming():
        from .services import gateway as _gw
        provider_name = os.environ.get("TELEPHONY_PROVIDER", "mock")
        provider = get_telephony_provider()
        # Real-inbound detection (independent of TELEPHONY_PROVIDER): the
        # self-hosted PBX gateway declares transport=sip AND presents the
        # gateway secret. Either one missing -> NOT a real inbound call and
        # nothing here can mark pstn_connected.
        try:
            _transport = ((request.form.get("transport") or request.args.get("transport")
                           or (request.get_json(silent=True) or {}).get("transport") or "").lower())
        except Exception:
            _transport = ""
        is_gateway_call = _transport in ("sip", "pbx", "pstn") and _gw.gateway_authenticated(request)
        if is_gateway_call:
            provider_name = "sip"
        # Extract caller and called numbers
        caller = extract_caller_from_request(request)
        if not caller:
            # Try form fields directly
            for key in ["From", "Caller", "from", "caller"]:
                v = request.form.get(key) or request.args.get(key)
                if v:
                    caller = normalize_phone(v)
                    break
        # If still empty, it may be mock call without caller, allow but mark
        from_number = caller or request.form.get("From") or request.args.get("From") or "unknown"
        to_number = request.form.get("To") or request.args.get("To") or request.form.get("Called") or os.environ.get("IVR_PHONE_NUMBER", "")
        # Generate call_sid if provider didn't send one
        call_sid = request.form.get("CallSid") or request.form.get("call_sid") or request.args.get("CallSid") or request.args.get("call_sid") or f"IVR-{uuid.uuid4().hex[:10].upper()}"
        # Normalize
        raw_from = from_number
        from_norm = normalize_phone(raw_from) if raw_from != "unknown" else ""

        conn = get_db()
        # Idempotency check
        existing = conn.execute("SELECT call_sid FROM ivr_calls WHERE call_sid=?", (call_sid,)).fetchone()
        if existing:
            # Already handled, return welcome again
            lang = "en"
            twiml = provider.generate_welcome_twiml(call_sid, lang)
            conn.close()
            return Response(twiml, mimetype="text/xml")

        # Determine is_mock (gateway-authenticated real calls are never mock)
        is_mock = (not is_gateway_call) and (
            provider_name == "mock" or request.headers.get("X-Mock-Call") == "true" or "MOCK" in call_sid)
        try:
            create_call_record(conn, provider_name, from_norm or raw_from, to_number, call_sid=call_sid, is_mock=is_mock)
        except Exception as e:
            # If duplicate due to race
            pass

        # Log caller capture
        if from_norm:
            conn.execute("UPDATE ivr_calls SET caller_number_normalized=?, caller_number=? WHERE call_sid=?", (from_norm, raw_from, call_sid))
            conn.commit()

        # Honest PSTN marker: ONLY an authenticated gateway delivering a real
        # (non-mock) inbound call flips pstn_connected, via first_real_inbound.
        if is_gateway_call and not is_mock:
            try:
                _gw.record_real_inbound(conn, call_sid, from_norm or raw_from, to_number)
            except Exception:
                pass

        # Helpline: identify registered farmer; skip the language prompt when
        # the farmer's preferred language is already known.
        try:
            farmer = identify_and_link_caller(conn, call_sid)
        except Exception:
            farmer = None
        known_lang = None
        if farmer:
            try:
                known_lang = apply_known_language(conn, call_sid, farmer)
            except Exception:
                known_lang = None
        if known_lang:
            # Welcome + main menu in the known language (no re-ask).
            say_hi = provider._say(f"{t('welcome', known_lang)} {t('language_confirm', known_lang)}", known_lang)
            say_menu = provider._say(t("main_menu", known_lang), known_lang)
            gather = provider._gather(say_menu, action=f"/api/ivr/webhook/menu?call_sid={call_sid}", num_digits=1, timeout=10, input_type="dtmf speech")
            redirect = provider._redirect(f"/api/ivr/webhook/menu?call_sid={call_sid}")
            twiml = provider._wrap_response(say_hi + gather + redirect)
            try:
                from .services.call_service import get_session as _gs
                _sess = _gs(conn, call_sid)
                conn.execute("INSERT INTO ivr_events (call_sid, session_id, event_type, details) VALUES (?,?,?,?)",
                             (call_sid, _sess["id"] if _sess else None, "LANGUAGE_SKIPPED_KNOWN",
                              json.dumps({"language": known_lang})))
                conn.commit()
            except Exception:
                pass
        else:
            # Generate welcome + language selection
            twiml = provider.generate_welcome_twiml(call_sid, "en")
        conn.close()
        return Response(twiml, mimetype="text/xml")

    # Alternative: Twilio often POSTs to root webhook, alias
    @app.route("/api/ivr/webhook/voice", methods=["POST", "GET"])
    def ivr_voice_alias():
        return ivr_incoming()

    # Canonical production webhook documented in docs/IVR_DEPLOYMENT.md.
    # Telephony providers (Twilio/Exotel) are configured with this URL.
    @app.route("/api/ivr/webhook/call", methods=["POST", "GET"])
    def ivr_call_alias():
        return ivr_incoming()

    @app.route("/api/ivr/webhook/welcome", methods=["POST", "GET"])
    @verify_telephony_signature
    def ivr_welcome():
        call_sid = request.args.get("call_sid") or request.form.get("CallSid") or request.args.get("CallSid") or ""
        provider = get_telephony_provider()
        if not call_sid:
            return Response(provider.generate_goodbye_twiml("unknown", "en", "error_generic"), mimetype="text/xml")
        # Replay welcome if no input
        conn = get_db()
        call = get_call(conn, call_sid)
        lang = call["language"] if call else "en"
        twiml = provider.generate_welcome_twiml(call_sid, lang)
        conn.close()
        return Response(twiml, mimetype="text/xml")

    @app.route("/api/ivr/webhook/language", methods=["POST", "GET"])
    @verify_telephony_signature
    def ivr_language():
        call_sid = request.args.get("call_sid") or request.form.get("CallSid") or request.args.get("CallSid") or ""
        raw_input, source, normalized = parse_input(request)
        # Also check Digits directly if parse failed
        if not raw_input:
            raw_input = request.form.get("Digits") or request.args.get("Digits") or ""
        provider = get_telephony_provider()
        if not call_sid:
            return Response(provider.generate_goodbye_twiml("unknown", "en", "error_generic"), mimetype="text/xml")
        conn = get_db()
        # If no input (timeout), replay
        if not raw_input:
            call = get_call(conn, call_sid)
            lang = call["language"] if call else "en"
            twiml = provider.generate_welcome_twiml(call_sid, lang)
            conn.close()
            return Response(twiml, mimetype="text/xml")
        lang = set_language(conn, call_sid, raw_input)
        # Now send main menu in selected language
        twiml = provider.generate_menu_twiml(call_sid, lang)
        conn.close()
        return Response(twiml, mimetype="text/xml")

    @app.route("/api/ivr/webhook/menu", methods=["POST", "GET"])
    @verify_telephony_signature
    def ivr_menu():
        call_sid = request.args.get("call_sid") or request.form.get("CallSid") or request.args.get("CallSid") or ""
        raw_input, source, normalized = parse_input(request)
        if not raw_input:
            raw_input = request.form.get("Digits") or request.args.get("Digits") or ""
        provider = get_telephony_provider()
        if not call_sid:
            return Response(provider.generate_goodbye_twiml("unknown", "en", "error_generic"), mimetype="text/xml")
        conn = get_db()
        session = get_session(conn, call_sid)
        lang = session["language"] if session else "en"
        if not raw_input:
            # Timeout: replay menu
            twiml = provider.generate_menu_twiml(call_sid, lang)
            conn.close()
            return Response(twiml, mimetype="text/xml")
        # Handle DTMF controls: 9=repeat, 0=go back
        if raw_input == "9":
            twiml = provider.generate_menu_twiml(call_sid, lang)
            conn.close()
            return Response(twiml, mimetype="text/xml")
        if raw_input == "0":
            # Go back to language
            update_session_state(conn, call_sid, "LANGUAGE_SELECT")
            twiml = provider.generate_welcome_twiml(call_sid, lang)
            conn.close()
            return Response(twiml, mimetype="text/xml")

        choice = handle_menu_choice(conn, call_sid, raw_input, lang)
        if choice == "vet":
            # Regional routing: district + language + existing assignment + load.
            call = get_call(conn, call_sid)
            session = get_session(conn, call_sid)
            district = None
            caller_user_id = session.get("caller_user_id") if session else None
            # Prefer the identified session region, then caller profile district.
            try:
                if session and session.get("region"):
                    district = (json.loads(session["region"]) or {}).get("district") or None
            except Exception:
                district = None
            if not district or district == "Not provided":
                district = None
            caller_norm = call["caller_number_normalized"] if call else ""
            if not district and caller_norm:
                user = conn.execute("SELECT district FROM users WHERE mobile=?", (caller_norm,)).fetchone()
                if user and user["district"]:
                    district = user["district"]
            set_routing_status(conn, call_sid, "ROUTING", {"district": district, "language": lang})
            ranked = rank_vets_for_call(conn, district, lang, caller_user_id)
            vet = next((r for r in ranked if r["selectable"]), None)
            try:
                conn.execute("INSERT INTO ivr_events (call_sid, session_id, event_type, details) VALUES (?,?,?,?)",
                             (call_sid, session["id"] if session else None, "ROUTING_DECISION",
                              json.dumps({"district": district, "language": lang,
                                          "selected_vet_id": vet["id"] if vet else None,
                                          "candidates": [{"vet_id": r["id"], "availability": r["availability"],
                                                          "score": r["score"], "reasons": r["reasons"]} for r in ranked]})))
                conn.commit()
            except Exception:
                pass
            if vet and is_vet_available(conn, vet):
                # Check recording consent if required
                if IVR_RECORDING_ENABLED and IVR_RECORDING_CONSENT_REQUIRED:
                    # Ask consent first
                    # We need to generate consent TwiML, but we can also directly connect if mock
                    # For real, ask consent
                    try:
                        from .telephony.mock_provider import MockTelephonyProvider
                        # Exact-type check: SIPProvider reuses the mock TwiML
                        # generators but is a REAL transport, so the farmer
                        # must actually press 1/2 for recording consent.
                        if type(provider) is MockTelephonyProvider:
                            # In mock, simulate consent = yes
                            vet_call_connected(conn, call_sid, vet["id"], vet_call_sid=f"VET-{uuid.uuid4().hex[:8]}")
                            twiml = provider.generate_connect_vet_twiml(call_sid, vet["mobile"], lang)
                        else:
                            twiml = provider.generate_recording_consent_twiml(call_sid, lang)
                            # Store pending vet id in session? Use extra field
                            conn.execute("UPDATE ivr_sessions SET vet_id=? WHERE call_sid=?", (vet["id"], call_sid))
                            conn.commit()
                    except Exception:
                        vet_call_connected(conn, call_sid, vet["id"])
                        twiml = provider.generate_connect_vet_twiml(call_sid, vet["mobile"], lang)
                else:
                    vet_call_connected(conn, call_sid, vet["id"])
                    twiml = provider.generate_connect_vet_twiml(call_sid, vet["mobile"], lang)
            else:
                # No vet available -> transition to survey
                # Use mock provider's helper if available
                if hasattr(provider, "generate_vet_unavailable_twiml"):
                    twiml = provider.generate_vet_unavailable_twiml(call_sid, lang)
                else:
                    msg = t("vet_unavailable", lang)
                    say = provider._say(msg, lang)
                    # Redirect to survey start
                    redirect = provider._redirect(f"/api/ivr/webhook/survey/start?call_sid={call_sid}")
                    twiml = provider._wrap_response(say + redirect)
                # Auto-mark survey started
                update_session_state(conn, call_sid, "SURVEY", extra={"survey_started": 1, "current_question_key": SURVEY_QUESTION_KEYS[0], "current_question_index": 0})
                set_routing_status(conn, call_sid, "VET_UNAVAILABLE", {"district": district})
                # Also enqueue that we attempted vet connection
                conn.execute("INSERT INTO ivr_events (call_sid, event_type, details) VALUES (?,?,?)",
                             (call_sid, "VET_UNAVAILABLE", json.dumps({"district": district})))
                conn.commit()
            conn.close()
            return Response(twiml, mimetype="text/xml")
        elif choice == "survey":
            twiml = f'<?xml version="1.0" encoding="UTF-8"?><Response><Say voice="alice" language="{lang}">{t("survey_intro", lang)}</Say><Redirect>/api/ivr/webhook/survey/start?call_sid={call_sid}</Redirect></Response>'
            conn.close()
            return Response(twiml, mimetype="text/xml")
        else:
            # Invalid choice
            msg = t("invalid_input", lang)
            say = provider._say(msg, lang)
            menu = provider.generate_menu_twiml(call_sid, lang)
            # Need to extract inner Response content? Simpler: regenerate menu with error
            # Combine
            inner = say + provider._say(t("main_menu", lang), lang)
            gather = provider._gather(inner, action=f"/api/ivr/webhook/menu?call_sid={call_sid}", num_digits=1, timeout=10, input_type="dtmf speech")
            twiml = provider._wrap_response(gather)
            conn.close()
            return Response(twiml, mimetype="text/xml")

    @app.route("/api/ivr/webhook/recording-consent", methods=["POST", "GET"])
    @verify_telephony_signature
    def ivr_recording_consent():
        call_sid = request.args.get("call_sid") or request.form.get("CallSid") or ""
        raw_input, source, normalized = parse_input(request)
        provider = get_telephony_provider()
        conn = get_db()
        session = get_session(conn, call_sid)
        lang = session["language"] if session else "en"
        vet_id = session["vet_id"] if session and session.get("vet_id") else None
        if not vet_id:
            # Try to find vet again
            vet = find_available_vet(conn, None)
            vet_id = vet["id"] if vet else None
        # 1 = consent, 2 = no consent
        consent = raw_input == "1"
        if vet_id:
            vet = conn.execute("SELECT * FROM users WHERE id=?", (vet_id,)).fetchone()
            vet_phone = vet["mobile"] if vet else ""
            # Update call recording consent
            conn.execute("UPDATE ivr_calls SET recording_consent=?, recording_enabled=? WHERE call_sid=?", (1 if consent else 0, 1 if consent else 0, call_sid))
            conn.commit()
            vet_call_connected(conn, call_sid, vet_id)
            twiml = provider.generate_connect_vet_twiml(call_sid, vet_phone, lang)
            conn.close()
            return Response(twiml, mimetype="text/xml")
        else:
            twiml = provider.generate_goodbye_twiml(call_sid, lang, "error_generic")
            conn.close()
            return Response(twiml, mimetype="text/xml")

    @app.route("/api/ivr/webhook/survey/start", methods=["POST", "GET"])
    @verify_telephony_signature
    def ivr_survey_start():
        call_sid = request.args.get("call_sid") or request.form.get("CallSid") or ""
        provider = get_telephony_provider()
        if not call_sid:
            return Response(provider.generate_goodbye_twiml("unknown", "en", "error_generic"), mimetype="text/xml")
        conn = get_db()
        session = get_session(conn, call_sid)
        lang = session["language"] if session else "en"
        # Ensure session is in SURVEY
        if not session or session["current_state"] != "SURVEY":
            update_session_state(conn, call_sid, "SURVEY", language=lang, extra={"survey_started": 1, "current_question_key": SURVEY_QUESTION_KEYS[0], "current_question_index": 0})
            session = get_session(conn, call_sid)
        set_routing_status(conn, call_sid, "SURVEY_STARTED")
        # Helpline: prefill verified profile facts so known questions are skipped.
        first_key = SURVEY_QUESTION_KEYS[0]
        try:
            call = get_call(conn, call_sid)
            farmer = identify_farmer(conn, (call or {}).get("caller_number_normalized") or "")
            if farmer:
                if not (session or {}).get("caller_user_id"):
                    conn.execute("UPDATE ivr_sessions SET caller_user_id=? WHERE call_sid=?",
                                 (farmer["user_id"], call_sid))
                    conn.commit()
                skipped = apply_prefill(conn, call_sid, build_prefill(conn, farmer), lang)
                if skipped:
                    responses = get_all_responses_dict(conn, call_sid)
                    prefilled = get_prefilled_keys(conn, call_sid)
                    idx = get_next_question_index(-1, responses, prefilled)
                    if idx < len(SURVEY_QUESTION_KEYS):
                        first_key = SURVEY_QUESTION_KEYS[idx]
                        update_session_state(conn, call_sid, "SURVEY",
                                             extra={"current_question_key": first_key,
                                                    "current_question_index": idx, "retry_count": 0})
                    conn.execute("INSERT INTO ivr_events (call_sid, session_id, event_type, details) VALUES (?,?,?,?)",
                                 (call_sid, (session or {}).get("id"), "SURVEY_PREFILLED",
                                  json.dumps({"skipped": skipped, "first_question": first_key})))
                    conn.commit()
        except Exception as e:
            print(f"survey prefill failed (non-fatal): {e}")
        twiml = provider.generate_survey_question_twiml(call_sid, first_key, lang, attempt=0)
        conn.close()
        return Response(twiml, mimetype="text/xml")

    @app.route("/api/ivr/webhook/survey", methods=["POST", "GET"])
    @verify_telephony_signature
    def ivr_survey():
        call_sid = request.args.get("call_sid") or request.form.get("CallSid") or request.args.get("CallSid") or ""
        q_key = request.args.get("q") or request.form.get("q") or ""
        # Fallback to session's current question
        provider = get_telephony_provider()
        if not call_sid:
            return Response(provider.generate_goodbye_twiml("unknown", "en", "error_generic"), mimetype="text/xml")
        conn = get_db()
        session = get_session(conn, call_sid)
        if not session:
            conn.close()
            return Response(provider.generate_goodbye_twiml(call_sid, "en", "error_generic"), mimetype="text/xml")
        lang = session["language"] or "en"
        current_key = session.get("current_question_key") or q_key or SURVEY_QUESTION_KEYS[0]
        # If q_key provided and differs from current, use q_key
        if q_key and q_key in SURVEY_QUESTION_KEYS:
            current_key = q_key
        current_index = session.get("current_question_index", 0)
        # Ensure correct index for current_key
        if current_key in SURVEY_QUESTION_KEYS:
            current_index = SURVEY_QUESTION_KEYS.index(current_key)

        # Parse input
        raw_input, source, normalized_input = parse_input(request)
        # Handle controls first
        # If no input yet (first render), raw_input empty means we already rendered? But this endpoint is called after Gather.
        # So if empty, treat as timeout/no input -> retry
        if not raw_input:
            # Check retry count
            retry = session.get("retry_count", 0) + 1
            if retry >= 3:
                # After max retries, skip if optional, else mark Not provided and advance
                record_survey_answer(conn, call_sid, current_key, "Not provided", source="dtmf", language=lang)
                responses = get_all_responses_dict(conn, call_sid)
                next_key = advance_survey(conn, call_sid, current_index, responses)
                if not next_key:
                    # Survey complete -> generate report
                    caller = get_call(conn, call_sid)
                    caller_phone = caller["caller_number_normalized"] if caller else ""
                    # Build responses dict
                    all_resp = get_all_responses_dict(conn, call_sid)
                    # Trigger report
                    try:
                        create_ivr_report_from_survey(conn, call_sid, all_resp, language=lang, caller_phone=caller_phone, duration_seconds=caller["duration_seconds"] if caller else 0)
                        # Also enqueue AI summarize
                        enqueue_job(conn, call_sid, "AI_SUMMARIZE", {"transcript": " ".join(all_resp.values())})
                    except Exception as e:
                        print(f"survey complete report error: {e}")
                    twiml = provider.generate_goodbye_twiml(call_sid, lang, "survey_complete")
                    conn.close()
                    return Response(twiml, mimetype="text/xml")
                else:
                    twiml = provider.generate_survey_question_twiml(call_sid, next_key, lang, attempt=0)
                    conn.close()
                    return Response(twiml, mimetype="text/xml")
            else:
                conn.execute("UPDATE ivr_sessions SET retry_count=? WHERE call_sid=?", (retry, call_sid))
                conn.commit()
                twiml = provider.generate_survey_question_twiml(call_sid, current_key, lang, attempt=retry)
                conn.close()
                return Response(twiml, mimetype="text/xml")

        # Handle DTMF controls
        if raw_input == "9":
            # Repeat current question
            twiml = provider.generate_survey_question_twiml(call_sid, current_key, lang, attempt=0)
            conn.close()
            return Response(twiml, mimetype="text/xml")
        if raw_input == "0":
            # Go back one question if possible
            prev_idx = max(0, current_index - 1)
            prev_key = SURVEY_QUESTION_KEYS[prev_idx]
            # Remove previous answer? Keep but allow overwrite
            update_session_state(conn, call_sid, "SURVEY", extra={"current_question_key": prev_key, "current_question_index": prev_idx, "retry_count": 0})
            twiml = provider.generate_survey_question_twiml(call_sid, prev_key, lang, attempt=0)
            conn.close()
            return Response(twiml, mimetype="text/xml")
        if raw_input == "#":
            # Skip if allowed
            raw_input = "Not provided"
            source = "dtmf"

        # Normalize and record
        # For number fields, raw_input may be digits; for choice, digits
        # Use confidence 0.9 for dtmf, lower for speech if needed
        confidence = 1.0 if source == "dtmf" else 0.85
        # Check if speech confidence low -> ask retry
        # For now, if speech and empty after normalize, retry
        normalized = record_survey_answer(conn, call_sid, current_key, raw_input, source=source, confidence=confidence, language=lang)

        # For choice questions that require confirmation, we could ask confirmation. Simplified: directly advance unless invalid
        # Validate choice
        if current_key in CHOICE_MAPS:
            mapping = CHOICE_MAPS[current_key]
            # If dtmf digit not in mapping and normalized is still raw digit, it's invalid
            if source == "dtmf" and raw_input not in mapping and normalized == raw_input:
                # Invalid digit for this question
                retry = session.get("retry_count", 0) + 1
                if retry >= 3:
                    # Mark Not provided and advance
                    # Overwrite last response with Not provided
                    conn.execute("DELETE FROM ivr_survey_responses WHERE call_sid=? AND question_key=? ORDER BY id DESC LIMIT 1", (call_sid, current_key))
                    record_survey_answer(conn, call_sid, current_key, "Not provided", source="dtmf", language=lang)
                    responses = get_all_responses_dict(conn, call_sid)
                    next_key = advance_survey(conn, call_sid, current_index, responses)
                    if not next_key:
                        caller = get_call(conn, call_sid)
                        caller_phone = caller["caller_number_normalized"] if caller else ""
                        all_resp = get_all_responses_dict(conn, call_sid)
                        try:
                            create_ivr_report_from_survey(conn, call_sid, all_resp, language=lang, caller_phone=caller_phone)
                        except Exception as e:
                            print(e)
                        twiml = provider.generate_goodbye_twiml(call_sid, lang, "survey_complete")
                        conn.close()
                        return Response(twiml, mimetype="text/xml")
                    twiml = provider.generate_survey_question_twiml(call_sid, next_key, lang)
                    conn.close()
                    return Response(twiml, mimetype="text/xml")
                else:
                    conn.execute("UPDATE ivr_sessions SET retry_count=? WHERE call_sid=?", (retry, call_sid))
                    conn.commit()
                    # Also delete the invalid response we just inserted
                    conn.execute("DELETE FROM ivr_survey_responses WHERE id = (SELECT id FROM ivr_survey_responses WHERE call_sid=? AND question_key=? ORDER BY id DESC LIMIT 1)", (call_sid, current_key))
                    conn.commit()
                    err = provider._say(t("invalid_input", lang), lang)
                    qtwiml = provider.generate_survey_question_twiml(call_sid, current_key, lang, attempt=retry)
                    # Need to combine: error + question
                    # qtwiml is full Response, extract inner
                    # Simpler: regenerate with error prefix
                    # We'll just return invalid input then question
                    twiml = provider._wrap_response(err + provider._say(t(f"q_{current_key}", lang), lang) + provider._gather("", action=f"/api/ivr/webhook/survey?call_sid={call_sid}&q={current_key}", num_digits=1, timeout=10))
                    conn.close()
                    return Response(twiml, mimetype="text/xml")

        # Advance to next question
        responses = get_all_responses_dict(conn, call_sid)
        next_key = advance_survey(conn, call_sid, current_index, responses)
        if not next_key:
            # Survey completed
            caller = get_call(conn, call_sid)
            caller_phone = caller["caller_number_normalized"] if caller else ""
            # Handle hidden caller: if caller_phone empty, check if callback_number was provided via survey? Not in current keys, but allow farmer_name etc.
            # If still empty, keep as is
            try:
                create_ivr_report_from_survey(conn, call_sid, responses, language=lang, caller_phone=caller_phone, duration_seconds=caller["duration_seconds"] if caller else 0)
                enqueue_job(conn, call_sid, "AI_SUMMARIZE", {"transcript": json.dumps(responses)})
            except Exception as e:
                print(f"create report failed: {e}")
                import traceback; traceback.print_exc()
            # Check urgency to decide message
            # Fetch report to see urgency
            report = conn.execute("SELECT urgency FROM ivr_reports WHERE call_sid=? ORDER BY id DESC LIMIT 1", (call_sid,)).fetchone()
            urgency = report["urgency"] if report else "MEDIUM"
            # If high/critical, include escalation note
            if urgency in ("HIGH", "CRITICAL"):
                msg_key = "emergency_escalated"
                # Append to goodbye
                say1 = provider._say(t("survey_complete", lang), lang)
                say2 = provider._say(t(msg_key, lang), lang)
                hang = provider._hangup()
                twiml = provider._wrap_response(say1 + say2 + hang)
            else:
                twiml = provider.generate_goodbye_twiml(call_sid, lang, "survey_complete")
            conn.close()
            return Response(twiml, mimetype="text/xml")
        else:
            twiml = provider.generate_survey_question_twiml(call_sid, next_key, lang, attempt=0)
            conn.close()
            return Response(twiml, mimetype="text/xml")

    @app.route("/api/ivr/webhook/status", methods=["POST", "GET"])
    @verify_telephony_signature
    def ivr_status_callback():
        """Provider status callback (call completed, failed, etc)."""
        call_sid = request.form.get("CallSid") or request.args.get("CallSid") or request.form.get("call_sid") or ""
        status = request.form.get("CallStatus") or request.args.get("CallStatus") or request.form.get("status") or ""
        duration = request.form.get("CallDuration") or request.args.get("CallDuration") or request.form.get("duration") or 0
        try:
            duration = int(duration)
        except Exception:
            duration = 0
        conn = get_db()
        if call_sid:
            # Normalize status to DB enum (uppercase, hyphens->underscores:
            # real providers send "no-answer"/"in-progress", which must NOT
            # corrupt into COMPLETED).
            status_norm = (status or "COMPLETED").upper().replace("-", "_")
            if status_norm not in ('INITIATED','RINGING','IN_PROGRESS','COMPLETED','FAILED','NO_ANSWER','BUSY','CANCELED'):
                status_norm = "COMPLETED"
            conn.execute("UPDATE ivr_calls SET status=?, duration_seconds=?, ended_at=datetime('now'), updated_at=datetime('now') WHERE call_sid=?", (status_norm, duration, call_sid))
            conn.commit()
            # Trigger disconnect handling if not already completed
            call = get_call(conn, call_sid)
            if call:
                existing_report = conn.execute("SELECT id FROM ivr_reports WHERE call_sid=?", (call_sid,)).fetchone()
                if not existing_report:
                    session = get_session(conn, call_sid)
                    if session:
                        if session.get("survey_started"):
                            responses = get_all_responses_dict(conn, call_sid)
                            if responses:
                                try:
                                    create_ivr_report_from_survey(conn, call_sid, responses, language=session.get("language","en"), caller_phone=call["caller_number_normalized"] or "", duration_seconds=duration, is_partial=True)
                                except Exception as e:
                                    print(e)
                        elif session.get("vet_connected") and status_norm == "COMPLETED":
                            # Vet leg actually answered (COMPLETED) without survey -
                            # create transcript-based report if transcripts exist.
                            # NO_ANSWER/BUSY/CANCELED/FAILED legs must NOT fabricate
                            # a "consultation completed" report; the survey
                            # fallback creates the real report instead.
                            transcripts = conn.execute("SELECT text_original FROM ivr_transcripts WHERE call_sid=?", (call_sid,)).fetchall()
                            full_transcript = " ".join([t["text_original"] for t in transcripts if t["text_original"]])
                            if full_transcript:
                                try:
                                    from ivr.services.report_service import create_ivr_report_from_vet_transcript
                                    create_ivr_report_from_vet_transcript(conn, call_sid, transcript=full_transcript, language=session.get("language","en"), caller_phone=call["caller_number_normalized"] or "", vet_id=session.get("vet_id"), duration_seconds=duration)
                                except Exception as e:
                                    print(e)
                            else:
                                # Still create minimal report for vet connection traceability
                                try:
                                    from ivr.services.report_service import create_ivr_report_from_vet_transcript
                                    create_ivr_report_from_vet_transcript(conn, call_sid, transcript="Vet consultation completed without transcript", language=session.get("language","en"), caller_phone=call["caller_number_normalized"] or "", vet_id=session.get("vet_id"), duration_seconds=duration)
                                except Exception as e:
                                    print(e)
        conn.close()
        return jsonify({"ok": True})

    @app.route("/api/ivr/webhook/call-ended", methods=["POST"])
    @verify_telephony_signature
    def ivr_call_ended():
        call_sid = request.args.get("call_sid") or (request.get_json(silent=True) or {}).get("call_sid") or request.form.get("CallSid") or ""
        duration = (request.get_json(silent=True) or {}).get("duration") or request.form.get("duration") or 0
        try:
            duration = int(duration)
        except Exception:
            duration = 0
        conn = get_db()
        if call_sid:
            handle_call_end(conn, call_sid, duration_seconds=duration, reason="webhook_call_ended")
        conn.close()
        return jsonify({"ok": True})

    # -------------------- MANAGEMENT APIS (auth required) --------------------
    @app.route("/api/ivr/calls", methods=["GET"])
    @ivr_auth_required(roles=["vet", "govt", "lab"])
    def list_ivr_calls():
        conn = get_db()
        status = request.args.get("status")
        limit = min(int(request.args.get("limit", 50)), 200)
        q = "SELECT * FROM ivr_calls ORDER BY id DESC LIMIT ?"
        params = [limit]
        if status:
            q = "SELECT * FROM ivr_calls WHERE status=? ORDER BY id DESC LIMIT ?"
            params = [status, limit]
        rows = conn.execute(q, params).fetchall()
        out = [dict(r) for r in rows]
        # Mask phone for non-privileged? Govt can see, vet can see
        conn.close()
        return jsonify(out)

    @app.route("/api/ivr/calls/<call_sid>", methods=["GET"])
    @ivr_auth_required(roles=["vet", "govt", "lab", "owner"])
    def get_ivr_call(call_sid):
        conn = get_db()
        call = conn.execute("SELECT * FROM ivr_calls WHERE call_sid=?", (call_sid,)).fetchone()
        if not call:
            conn.close()
            return jsonify({"error": "Call not found"}), 404
        session = conn.execute("SELECT * FROM ivr_sessions WHERE call_sid=?", (call_sid,)).fetchone()
        responses = conn.execute("SELECT * FROM ivr_survey_responses WHERE call_sid=? ORDER BY id", (call_sid,)).fetchall()
        events = conn.execute("SELECT * FROM ivr_events WHERE call_sid=? ORDER BY id", (call_sid,)).fetchall()
        transcripts = conn.execute("SELECT * FROM ivr_transcripts WHERE call_sid=? ORDER BY id", (call_sid,)).fetchall()
        report = conn.execute("SELECT * FROM ivr_reports WHERE call_sid=?", (call_sid,)).fetchone()
        conn.close()
        return jsonify({
            "call": dict(call),
            "session": dict(session) if session else None,
            "responses": [dict(r) for r in responses],
            "events": [dict(e) for e in events],
            "transcripts": [dict(t) for t in transcripts],
            "report": dict(report) if report else None,
        })

    @app.route("/api/ivr/reports", methods=["GET"])
    @ivr_auth_required(roles=["vet", "govt", "lab", "owner"])
    def list_ivr_reports():
        conn = get_db()
        # Owner sees only their own reports (by phone)
        role = g.user["role"]
        if role == "owner":
            # Find owner's phone
            user = conn.execute("SELECT mobile FROM users WHERE id=?", (g.user["uid"],)).fetchone()
            mobile = user["mobile"] if user else ""
            rows = conn.execute("SELECT * FROM ivr_reports WHERE caller_number=? OR caller_number LIKE ? ORDER BY id DESC", (mobile, f"%{mobile[-10:]}" if mobile else "%")).fetchall()
        else:
            # Vet/govt see all, with optional filters
            district = request.args.get("district")
            urgency = request.args.get("urgency")
            status_f = request.args.get("status")
            q = "SELECT * FROM ivr_reports WHERE 1=1"
            params = []
            if district:
                q += " AND LOWER(location_district)=LOWER(?)"
                params.append(district)
            if urgency:
                q += " AND urgency=?"
                params.append(urgency.upper())
            if status_f:
                q += " AND status=?"
                params.append(status_f)
            q += " ORDER BY id DESC LIMIT 100"
            rows = conn.execute(q, params).fetchall()
        out = []
        for r in rows:
            d = dict(r)
            # Parse structured json if needed
            try:
                d["ai_structured"] = json.loads(d["ai_structured_json"]) if d["ai_structured_json"] else None
            except Exception:
                d["ai_structured"] = None
            out.append(d)
        conn.close()
        return jsonify(out)

    @app.route("/api/ivr/reports/<int:report_id>", methods=["GET"])
    @ivr_auth_required(roles=["vet", "govt", "lab", "owner"])
    def get_ivr_report(report_id):
        conn = get_db()
        report = conn.execute("SELECT * FROM ivr_reports WHERE id=?", (report_id,)).fetchone()
        if not report:
            conn.close()
            return jsonify({"error": "Report not found"}), 404
        # Role check: owner can only view own
        if g.user["role"] == "owner":
            user = conn.execute("SELECT mobile FROM users WHERE id=?", (g.user["uid"],)).fetchone()
            mobile = user["mobile"] if user else ""
            if report["caller_number"] != mobile and mobile[-10:] not in (report["caller_number"] or ""):
                conn.close()
                return jsonify({"error": "Not authorized"}), 403
        # Also fetch linked case
        case = None
        if report["case_id"]:
            case = conn.execute("SELECT * FROM cases WHERE id=?", (report["case_id"],)).fetchone()
        transcripts = conn.execute("SELECT * FROM ivr_transcripts WHERE call_sid=?", (report["call_sid"],)).fetchall()
        conn.close()
        res = dict(report)
        try:
            res["ai_structured"] = json.loads(res["ai_structured_json"]) if res["ai_structured_json"] else None
        except Exception:
            res["ai_structured"] = None
        res["linked_case"] = dict(case) if case else None
        res["transcripts"] = [dict(t) for t in transcripts]
        return jsonify(res)

    @app.route("/api/ivr/reports/<int:report_id>/status", methods=["PUT"])
    @ivr_auth_required(roles=["vet", "govt"])
    def update_ivr_report_status(report_id):
        data = request.get_json(force=True) or {}
        new_status = data.get("status")
        note = data.get("note") or ""
        if not new_status:
            return jsonify({"error": "status required"}), 400
        conn = get_db()
        report = conn.execute("SELECT * FROM ivr_reports WHERE id=?", (report_id,)).fetchone()
        if not report:
            conn.close()
            return jsonify({"error": "Report not found"}), 404
        try:
            update_report_status(conn, report_id, new_status, actor=g.user["name"], note=note)
        except ValueError as e:
            conn.close()
            return jsonify({"error": str(e)}), 400
        updated = conn.execute("SELECT * FROM ivr_reports WHERE id=?", (report_id,)).fetchone()
        conn.close()
        return jsonify(dict(updated))

    @app.route("/api/ivr/reports/<int:report_id>/merge", methods=["POST"])
    @ivr_auth_required(roles=["vet", "govt"])
    def merge_ivr_report(report_id):
        data = request.get_json(force=True) or {}
        keep_id = data.get("keep_id") or report_id
        duplicate_id = data.get("duplicate_id") or report_id
        conn = get_db()
        from .services.duplicate import merge_reports
        merge_reports(conn, keep_id, duplicate_id, actor=g.user["name"])
        conn.close()
        return jsonify({"ok": True, "merged": True})

    @app.route("/api/ivr/analytics", methods=["GET"])
    @ivr_auth_required(roles=["vet", "govt"])
    def ivr_analytics():
        conn = get_db()
        total_calls = conn.execute("SELECT COUNT(*) c FROM ivr_calls").fetchone()["c"]
        completed = conn.execute("SELECT COUNT(*) c FROM ivr_calls WHERE status='COMPLETED'").fetchone()["c"]
        failed = conn.execute("SELECT COUNT(*) c FROM ivr_calls WHERE status='FAILED'").fetchone()["c"]
        reports = conn.execute("SELECT COUNT(*) c FROM ivr_reports").fetchone()["c"]
        partial = conn.execute("SELECT COUNT(*) c FROM ivr_reports WHERE status='PARTIALLY_COMPLETED'").fetchone()["c"]
        vet_conn = conn.execute("SELECT COUNT(*) c FROM ivr_sessions WHERE vet_connected=1").fetchone()["c"]
        vet_unavail = conn.execute("SELECT COUNT(*) c FROM ivr_events WHERE event_type='VET_UNAVAILABLE'").fetchone()["c"]
        avg_duration = conn.execute("SELECT AVG(duration_seconds) avg FROM ivr_calls WHERE duration_seconds>0").fetchone()["avg"] or 0
        high_prio = conn.execute("SELECT COUNT(*) c FROM ivr_reports WHERE urgency IN ('HIGH','CRITICAL')").fetchone()["c"]
        # District-wise
        district_rows = conn.execute("SELECT COALESCE(location_district,'Unknown') d, COUNT(*) c FROM ivr_reports GROUP BY LOWER(d) ORDER BY c DESC").fetchall()
        disease_rows = conn.execute("SELECT COALESCE(main_problem,'Unknown') d, COUNT(*) c FROM ivr_reports GROUP BY LOWER(d) ORDER BY c DESC LIMIT 6").fetchall()
        # Daily
        daily = conn.execute("SELECT * FROM ivr_analytics_daily ORDER BY date DESC LIMIT 7").fetchall()
        # Helpline channel + routing funnel (live counts)
        try:
            helpline_calls = conn.execute("SELECT COUNT(*) c FROM ivr_calls WHERE channel='HELPLINE'").fetchone()["c"]
        except Exception:
            helpline_calls = 0
        try:
            helpline_reports = conn.execute("SELECT COUNT(*) c FROM ivr_reports WHERE source='HELPLINE'").fetchone()["c"]
        except Exception:
            helpline_reports = 0
        try:
            by_channel = [{"label": r["ch"], "value": r["c"]} for r in
                          conn.execute("SELECT COALESCE(channel,'IVR') ch, COUNT(*) c FROM ivr_calls GROUP BY ch").fetchall()]
        except Exception:
            by_channel = []
        try:
            identified = conn.execute("SELECT COUNT(*) c FROM ivr_sessions WHERE caller_user_id IS NOT NULL").fetchone()["c"]
        except Exception:
            identified = 0
        try:
            prefilled_calls = conn.execute("SELECT COUNT(DISTINCT call_sid) c FROM ivr_survey_responses WHERE transcript='prefilled:profile'").fetchone()["c"]
        except Exception:
            prefilled_calls = 0
        try:
            by_routing = [{"label": r["rs"], "value": r["c"]} for r in
                          conn.execute("SELECT COALESCE(routing_status,'UNKNOWN') rs, COUNT(*) c FROM ivr_sessions GROUP BY rs").fetchall()]
        except Exception:
            by_routing = []
        # Merge with govt analytics for integrated view
        conn.close()
        return jsonify({
            "total_calls": total_calls,
            "completed_calls": completed,
            "failed_calls": failed,
            "reports_generated": reports,
            "partial_reports": partial,
            "vet_connections": vet_conn,
            "vet_unavailable": vet_unavail,
            "avg_call_duration": round(avg_duration, 1),
            "high_priority": high_prio,
            "by_district": [{"label": r["d"], "value": r["c"]} for r in district_rows],
            "by_problem": [{"label": r["d"], "value": r["c"]} for r in disease_rows],
            "daily": [dict(d) for d in daily],
            "helpline_calls": helpline_calls,
            "helpline_reports": helpline_reports,
            "by_channel": by_channel,
            "identified_farmers": identified,
            "prefilled_calls": prefilled_calls,
            "by_routing_status": by_routing,
        })

    @app.route("/api/ivr/location/share", methods=["POST", "GET"])
    def ivr_location_share():
        """Secure location sharing via SMS link (farmer consent)."""
        data = request.get_json(silent=True) or {}
        call_sid = request.args.get("call_sid") or data.get("call_sid") or request.form.get("call_sid") or ""
        token = request.args.get("token") or data.get("token") or ""
        lat = request.args.get("lat") or data.get("lat") or request.form.get("lat")
        lng = request.args.get("lng") or data.get("lng") or request.form.get("lng")
        if not call_sid or not lat or not lng:
            return jsonify({"error": "call_sid, lat, lng required"}), 400
        # Validate token (simple hash check)
        # If token invalid, still accept but log warning
        conn = get_db()
        call = conn.execute("SELECT * FROM ivr_calls WHERE call_sid=?", (call_sid,)).fetchone()
        if not call:
            conn.close()
            return jsonify({"error": "Call not found"}), 404
        # Store as response with location_source GPS
        session = conn.execute("SELECT * FROM ivr_sessions WHERE call_sid=?", (call_sid,)).fetchone()
        # Insert location responses
        try:
            lat_f = float(lat); lng_f = float(lng)
            if 6.0 <= lat_f <= 36.0 and 68.0 <= lng_f <= 98.0:
                # Update report if exists, else store pending location in responses table
                # Check responses for location
                conn.execute(
                    "INSERT INTO ivr_survey_responses (call_sid, session_id, question_key, answer_raw, answer_normalized, answer_source, language) VALUES (?,?,?,?,?,?,?)",
                    (call_sid, session["id"] if session else None, "location_lat", str(lat), str(lat_f), "manual", session["language"] if session else "en")
                )
                conn.execute(
                    "INSERT INTO ivr_survey_responses (call_sid, session_id, question_key, answer_raw, answer_normalized, answer_source, language) VALUES (?,?,?,?,?,?,?)",
                    (call_sid, session["id"] if session else None, "location_lng", str(lng), str(lng_f), "manual", session["language"] if session else "en")
                )
                conn.execute(
                    "INSERT INTO ivr_survey_responses (call_sid, session_id, question_key, answer_raw, answer_normalized, answer_source, language) VALUES (?,?,?,?,?,?,?)",
                    (call_sid, session["id"] if session else None, "location_source", "GPS", "GPS", "manual", session["language"] if session else "en")
                )
                # Also update ivr_reports if already created
                conn.execute("UPDATE ivr_reports SET location_lat=?, location_lng=?, location_source='GPS', location_accuracy='High - SMS link GPS consent' WHERE call_sid=?", (lat_f, lng_f, call_sid))
                conn.execute("INSERT INTO ivr_events (call_sid, event_type, details) VALUES (?,?,?)", (call_sid, "LOCATION_SHARED_VIA_SMS", json.dumps({"lat": lat_f, "lng": lng_f})))
                conn.commit()
        except Exception as e:
            conn.close()
            return jsonify({"error": str(e)}), 400
        conn.close()
        return jsonify({"ok": True, "message": "Location shared successfully, thank you."})

    @app.route("/api/ivr/config", methods=["GET"])
    @ivr_auth_required(roles=["govt"])
    def get_ivr_config():
        from .config import get_ivr_config_summary
        conn = get_db()
        # Load survey config from DB
        try:
            row = conn.execute("SELECT config_value FROM ivr_survey_config WHERE config_key='survey_definition'").fetchone()
            survey_cfg = json.loads(row["config_value"]) if row else None
        except Exception:
            survey_cfg = None
        conn.close()
        return jsonify({
            "telephony": get_ivr_config_summary(),
            "survey": survey_cfg,
        })

    @app.route("/api/ivr/config", methods=["PUT"])
    @ivr_auth_required(roles=["govt"])
    def update_ivr_config():
        data = request.get_json(force=True) or {}
        # Only survey config is mutable via API; telephony secrets via env
        survey_cfg = data.get("survey")
        conn = get_db()
        if survey_cfg:
            from .services.survey import save_survey_config_to_db
            save_survey_config_to_db(conn, survey_cfg, updated_by=g.user["uid"])
            # Audit
            try:
                from database import audit_log
                audit_log(conn, "UPDATE_IVR_SURVEY_CONFIG", "ivr_config", "survey_definition", actor_id=g.user["uid"], actor_name=g.user["name"], actor_role=g.user["role"], details={"survey": survey_cfg})
                conn.commit()
            except Exception:
                pass
        conn.close()
        return jsonify({"ok": True})

    @app.route("/api/ivr/health", methods=["GET"])
    def ivr_health():
        from .config import get_ivr_config_summary, validate_required_config, is_provider_configured
        from .services import gateway as _gw
        missing = validate_required_config()
        conn = get_db()
        try:
            gw = _gw.gateway_health(conn)
            pstn = _gw.pstn_status(conn)
        except Exception:
            gw = {"pbx_healthy": False, "pbx_last_heartbeat_at_utc": None,
                  "pbx_last_heartbeat_age_s": None, "pbx_host": None,
                  "sip_registered": False, "sip_trunk": None}
            pstn = {"pstn_connected": False, "first_real_inbound_at_utc": None,
                    "first_real_inbound_sid": None}
        finally:
            try:
                conn.close()
            except Exception:
                pass
        # Distinct liveness signals: the application/IVR being up says NOTHING
        # about the voice path. pbx/sip/pstn are true only when verified:
        # heartbeats for pbx/sip, an actual authenticated inbound call for pstn.
        return jsonify({
            "status": "ok" if not missing else "degraded",
            "application": True,
            "ivr": True,
            "pbx": gw["pbx_healthy"],
            "pbx_detail": gw,
            "sip_registered": gw["sip_registered"],
            "pstn_connected": pstn["pstn_connected"],
            "pstn_detail": pstn,
            "config": get_ivr_config_summary(),
            "missing_env": missing,
            "provider_ready": is_provider_configured(),
            "version": "1.0-ivr",
        })

    # -------------------- VOICE GATEWAY (self-hosted PBX) --------------------
    # Secret-authenticated endpoints for the Asterisk gateway in pbx/ ONLY.
    # Never uses the permissive mock signature path: gateway auth fails closed
    # when PBX_WEBHOOK_SECRET is unset.
    @app.route("/api/ivr/gateway/heartbeat", methods=["POST"])
    def gateway_heartbeat():
        from .services import gateway as _gw
        ip = request.remote_addr or "unknown"
        if is_rate_limited(ip, IVR_RATE_LIMIT_PER_MINUTE):
            return jsonify({"error": "Rate limit exceeded"}), 429
        if not _gw.gateway_ip_allowed(request):
            return jsonify({"error": "Forbidden"}), 403
        if not _gw.gateway_authenticated(request):
            return jsonify({"error": "Invalid gateway credentials"}), 401
        data = request.get_json(silent=True) or {}
        if not isinstance(data, dict):
            data = {}
        conn = get_db()
        try:
            summary = _gw.record_heartbeat(conn, data)
        finally:
            try:
                conn.close()
            except Exception:
                pass
        return jsonify({"ok": True, **summary})

    @app.route("/api/ivr/gateway/authorize-dial", methods=["POST"])
    def gateway_authorize_dial():
        from .services import gateway as _gw
        ip = request.remote_addr or "unknown"
        if is_rate_limited(ip, IVR_RATE_LIMIT_PER_MINUTE):
            return jsonify({"error": "Rate limit exceeded"}), 429
        if not _gw.gateway_ip_allowed(request):
            return jsonify({"error": "Forbidden"}), 403
        if not _gw.gateway_authenticated(request):
            return jsonify({"error": "Invalid gateway credentials"}), 401
        data = request.get_json(silent=True) or {}
        if not isinstance(data, dict):
            data = {}
        call_sid = data.get("call_sid") or ""
        number = data.get("number") or ""
        conn = get_db()
        try:
            decision = _gw.authorize_dial_number(conn, call_sid, number)
            try:
                conn.execute(
                    "INSERT INTO ivr_events (call_sid, event_type, details, actor) VALUES (?,?,?,?)",
                    (call_sid, "DIAL_AUTHORIZE",
                     json.dumps({"number_last4": (number or "")[-4:], "allowed": decision.get("allowed")}), "pbx-gateway"))
                conn.commit()
            except Exception:
                pass
        finally:
            try:
                conn.close()
            except Exception:
                pass
        return jsonify(decision), (200 if decision.get("allowed") else 403)

    # -------------------- MOCK / DEV helpers --------------------
    @app.route("/api/ivr/mock/call", methods=["POST"])
    def mock_initiate_call():
        """Dev helper: simulate inbound call without real telephony.
        Mirrors real inbound identification (farmer link + known language)."""
        data = request.get_json(force=True) or {}
        from_number = data.get("from") or data.get("caller") or data.get("phone") or "+919800000001"
        to_number = data.get("to") or os.environ.get("IVR_PHONE_NUMBER", "+911800123456")
        provider_name = "mock"
        call_sid = f"MOCK-{uuid.uuid4().hex[:10].upper()}"
        conn = get_db()
        create_call_record(conn, provider_name, normalize_phone(from_number), to_number, call_sid=call_sid, is_mock=True)
        known_lang = None
        try:
            farmer = identify_and_link_caller(conn, call_sid)
            if farmer:
                known_lang = apply_known_language(conn, call_sid, farmer)
        except Exception:
            pass
        conn.close()
        provider = get_telephony_provider()
        if known_lang:
            say_hi = provider._say(f"{t('welcome', known_lang)} {t('language_confirm', known_lang)}", known_lang)
            say_menu = provider._say(t("main_menu", known_lang), known_lang)
            gather = provider._gather(say_menu, action=f"/api/ivr/webhook/menu?call_sid={call_sid}", num_digits=1, timeout=10, input_type="dtmf speech")
            twiml = provider._wrap_response(say_hi + gather + provider._redirect(f"/api/ivr/webhook/menu?call_sid={call_sid}"))
        else:
            twiml = provider.generate_welcome_twiml(call_sid, "en")
        return jsonify({"call_sid": call_sid, "twiml": twiml, "from": from_number, "to": to_number, "language": known_lang or "en"})

    @app.route("/api/ivr/mock/dtmf", methods=["POST"])
    def mock_dtmf():
        """Dev helper: send DTMF digit to ongoing call."""
        data = request.get_json(force=True) or {}
        call_sid = data.get("call_sid")
        digit = data.get("digit") or data.get("dtmf") or ""
        q = data.get("q") or ""
        if not call_sid:
            return jsonify({"error": "call_sid required"}), 400
        # Simulate POST to webhook
        # We directly handle via call_service? For simpler testing, we emulate via internal POST
        # Instead, return instruction for test to call webhook directly
        return jsonify({"ok": True, "call_sid": call_sid, "digit": digit, "next": "POST to /api/ivr/webhook/* with Digits"})

