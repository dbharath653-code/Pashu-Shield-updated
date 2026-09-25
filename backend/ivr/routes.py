"""
IVR HTTP surface:

  Provider webhooks (signature verified, idempotent, rate limited)
      POST /ivr/voice           incoming call / keypad / speech / dial result
      POST /ivr/status          call status callback
      POST /ivr/recording       recording available callback
      POST /ivr/transcription   provider native transcription callback
      GET  /ivr/location/<tok>  browser GPS consent page
      POST /ivr/location/<tok>  browser GPS submission

  Application APIs (JWT, role based)
      /api/ivr/health, /config, /survey, /calls, /reports, /analytics,
      /jobs, /audit, /vets/availability
"""
import json
import os
from datetime import datetime, timedelta

from flask import Blueprint, jsonify, request, Response

from . import jobs, location as location_mod, report_service, survey as survey_mod
from . import security as security_mod
from . import stt as stt_mod
from .config import settings
from .locales import available_locales, supported_language_codes, t
from .providers import get_provider, provider_status
from .security import (mask_phone, payload_fingerprint, rate_limit,
                       verify_webhook)
from .service import IVRService
from .survey import validate_definition

NOT_PROVIDED = "Not provided"

# Roles
def _roles_admin():
    return settings.ADMIN_ROLES or ["govt"]


def _roles_view():
    return settings.VIEW_ROLES or ["govt", "vet"]


def create_ivr_blueprint(deps):
    """Build the blueprint with the host application's helpers injected."""
    auth_required = deps["auth_required"]
    get_db = deps["get_db"]
    audit_log = deps["audit_log"]
    notify = deps["notify"]
    hash_password = deps["hash_password"]

    context = {
        "notify": notify,
        "audit_log": audit_log,
        "hash_password": hash_password,
        "get_db": get_db,
    }

    bp = Blueprint("ivr", __name__)

    # ------------------------------------------------------------- helpers --
    def provider():
        return get_provider()

    def service():
        return IVRService(provider(), context=context)

    def xml(body, status=200):
        return Response(body, status=status, mimetype="application/xml")

    def empty_response():
        return xml('<?xml version="1.0" encoding="UTF-8"?><Response></Response>')

    def _values():
        values = {}
        values.update(request.args.to_dict())
        if request.form:
            values.update(request.form.to_dict())
        if request.is_json:
            try:
                values.update(request.get_json(silent=True) or {})
            except Exception:
                pass
        return values

    def _raw_body():
        try:
            return request.get_data(as_text=True) or ""
        except Exception:
            return ""

    def _call_id_field(values):
        for key in ("CallSid", "CallUUID", "CallSid", "call_sid", "CallId"):
            if values.get(key):
                return values.get(key)
        return None

    def _has_input(values):
        return any(values.get(k) not in (None, "") for k in
                   ("Digits", "SpeechResult", "Speech", "DtmfDigits", "dtmf", "TranscriptionText"))

    def _guarded_webhook(kind):
        """Signature + rate-limit + idempotency gate. Returns (values, conn, err_response)."""
        conn = get_db()
        values = _values()
        raw = _raw_body()
        prov = provider()

        # rate limit (per IP and per provider call id)
        ip = request.headers.get("X-Forwarded-For", "").split(",")[0].strip() or (request.remote_addr or "unknown")
        if not rate_limit(conn, f"ivr:ip:{ip}:{kind}"):
            conn.close()
            return values, None, (jsonify({"error": "rate limit exceeded"}), 429)

        result = verify_webhook(request, prov.name, values, raw)
        if not result.valid:
            from .security import claim_webhook_event
            cid = _call_id_field(values)
            if cid:
                claim_webhook_event(conn, prov.name, f"{cid}:rejected:{payload_fingerprint(values, raw)[:16]}",
                                    kind, values, raw, signature_valid=0)
            conn.close()
            if result.reason == "not_configured" and not settings.REQUIRE_WEBHOOK_SIGNATURE:
                return values, get_db(), None
            return values, None, (jsonify({"error": "webhook signature verification failed",
                                           "reason": result.reason,
                                           "detail": result.detail}), 403)

        # idempotency: identical provider callback with input is processed once
        if _has_input(values):
            from .security import claim_webhook_event, mark_webhook_processed
            cid = _call_id_field(values) or "unknown"
            event_id = f"{cid}:{kind}:{payload_fingerprint(values, raw)[:16]}"
            is_new, _row = claim_webhook_event(conn, prov.name, event_id, kind, values, raw,
                                               signature_valid=1)
            if not is_new:
                conn.execute(
                    "INSERT INTO ivr_events (event_type, actor, actor_type, details) "
                    "VALUES ('WEBHOOK_DUPLICATE_IGNORED',?,'PROVIDER',?)",
                    (prov.name, json.dumps({"event_id": event_id})),
                )
                conn.commit()
                conn.close()
                return values, None, ("DUPLICATE", None)
            mark_webhook_processed(conn, prov.name, event_id)
        return values, conn, None

    # ============================================================= WEBHOOKS ==
    @bp.post("/ivr/voice")
    def ivr_voice():
        values, conn, err = _guarded_webhook("voice")
        if err:
            if err == ("DUPLICATE", None):
                return empty_response()
            return err
        try:
            prov = provider()
            svc = IVRService(prov, context=context)
            if not prov.is_configured():
                # Nothing is faked: the caller is told the service is unavailable.
                conn.execute(
                    "INSERT INTO ivr_events (event_type, actor, actor_type, details) "
                    "VALUES ('PROVIDER_NOT_CONFIGURED',?,'PROVIDER',?)",
                    (prov.name, json.dumps({"errors": prov.configuration_errors()})),
                )
                conn.commit()
                builder = prov.response()
                builder.say("The IVR reporting service is not configured. Please contact the animal husbandry department.")
                builder.hangup()
                ctype, body = prov.render(builder)
                return Response(body, status=503, mimetype=ctype)

            incoming = prov.parse_incoming(values)
            gather = prov.parse_gather(values)
            first_request = not values.get("Digits") and not values.get("SpeechResult") and \
                not _has_input(values) and not svc._dial_status(values)

            if first_request and not conn.execute(
                "SELECT id FROM ivr_calls WHERE provider=? AND provider_call_id=?",
                (prov.name, incoming.provider_call_id),
            ).fetchone():
                ctype, body = svc.handle_incoming(conn, incoming, values)
            else:
                ctype, body = svc.handle_input(conn, gather, values)
            return Response(body, mimetype=ctype)
        finally:
            try:
                conn.close()
            except Exception:
                pass

    @bp.post("/ivr/status")
    def ivr_status():
        values, conn, err = _guarded_webhook("status")
        if err:
            if err == ("DUPLICATE", None):
                return jsonify({"ok": True, "duplicate": True})
            return err
        try:
            prov = provider()
            svc = IVRService(prov, context=context)
            event = prov.parse_status(values)
            svc.handle_status(conn, event, values)
            return jsonify({"ok": True})
        finally:
            conn.close()

    @bp.post("/ivr/recording")
    def ivr_recording():
        values, conn, err = _guarded_webhook("recording")
        if err:
            if err == ("DUPLICATE", None):
                return jsonify({"ok": True, "duplicate": True})
            return err
        try:
            prov = provider()
            svc = IVRService(prov, context=context)
            event = prov.parse_recording(values)
            recording_id = svc.handle_recording(conn, event, values)
            return jsonify({"ok": True, "recording_id": recording_id})
        finally:
            conn.close()

    @bp.post("/ivr/transcription")
    def ivr_transcription():
        values, conn, err = _guarded_webhook("transcription")
        if err:
            if err == ("DUPLICATE", None):
                return jsonify({"ok": True, "duplicate": True})
            return err
        try:
            prov = provider()
            svc = IVRService(prov, context=context)
            data = prov.parse_transcription(values)
            svc.handle_transcription(conn, data, values)
            return jsonify({"ok": True})
        finally:
            conn.close()

    # ------------------------------------------------------- GPS consent ----
    LOCATION_PAGE = """<!doctype html><html lang="en"><head><meta charset="utf-8"/>
<meta name="viewport" content="width=device-width,initial-scale=1"/>
<title>Share farm location — PashuMitra IVR</title>
<style>
body{font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,Arial,sans-serif;background:#eef0f6;
     color:#222639;margin:0;padding:24px;display:flex;justify-content:center}
.card{background:#fff;border-radius:18px;box-shadow:0 6px 20px rgba(45,55,140,.08);padding:24px;max-width:420px;width:100%}
h1{font-size:19px;color:#2c3690;margin:0 0 8px}
p{font-size:14px;color:#7a7f95;line-height:1.5}
button{width:100%;border:none;border-radius:14px;padding:14px;font-weight:700;font-size:15px;margin-top:10px;cursor:pointer}
.primary{background:#3d4db8;color:#fff}.ghost{background:#e7effe;color:#2c3690}
.status{margin-top:14px;font-size:13px;color:#1fa971;font-weight:700}
</style></head><body><div class="card">
<h1>Share your farm location</h1>
<p>PashuMitra is using this link because the phone call does not provide GPS coordinates.
Your location is used only to send a veterinarian to the right village. You can refuse.</p>
<button class="primary" id="allow">Share my location</button>
<button class="ghost" id="deny">Continue without sharing</button>
<div class="status" id="status"></div>
<script>
const status=document.getElementById('status');
document.getElementById('allow').onclick=()=>{
  if(!navigator.geolocation){status.textContent='This device/browser does not provide location.';return;}
  status.textContent='Requesting location…';
  navigator.geolocation.getCurrentPosition(async(pos)=>{
    const res=await fetch(location.pathname,{method:'POST',headers:{'Content-Type':'application/json'},
      body:JSON.stringify({lat:pos.coords.latitude,lng:pos.coords.longitude,accuracy:pos.coords.accuracy,consent:true})});
    const data=await res.json().catch(()=>({}));
    status.textContent=res.ok?'Thank you. Location recorded.':'Could not save location: '+(data.error||'unknown error');
  },(err)=>{status.textContent='Location permission denied or unavailable.';},{enableHighAccuracy:true,timeout:20000});
};
document.getElementById('deny').onclick=async()=>{
  await fetch(location.pathname,{method:'POST',headers:{'Content-Type':'application/json'},
    body:JSON.stringify({consent:false})});
  status.textContent='No location shared. Your report will use the village you told us.';
};
</script></div></body></html>"""

    @bp.get("/ivr/location/<token>")
    def ivr_location_page(token):
        conn = get_db()
        try:
            from .security import verify_token
            payload = verify_token(token)
            if not payload:
                return Response("This location link is invalid or has expired.", status=410,
                                mimetype="text/html")
            return Response(LOCATION_PAGE, mimetype="text/html")
        finally:
            conn.close()

    @bp.post("/ivr/location/<token>")
    def ivr_location_capture(token):
        conn = get_db()
        try:
            if not rate_limit(conn, f"ivr:location:{token[:16]}"):
                return jsonify({"error": "rate limit exceeded"}), 429
            data = request.get_json(silent=True) or {}
            call_id, error = location_mod.resolve_gps_submission(
                conn, token, data.get("lat"), data.get("lng"),
                accuracy=data.get("accuracy"), consent=bool(data.get("consent")))
            if error:
                return jsonify({"error": error}), 400
            # keep any already-created report in sync
            report = conn.execute("SELECT * FROM ivr_reports WHERE call_id=?", (call_id,)).fetchone()
            if report and report["structured_json"]:
                structured = json.loads(report["structured_json"])
                loc = location_mod.resolve(conn, call_id)
                structured["location"]["lat"] = loc.get("lat")
                structured["location"]["lng"] = loc.get("lng")
                structured["location"]["source"] = loc.get("source")
                structured["location"]["accuracy"] = loc.get("accuracy")
                conn.execute(
                    "UPDATE ivr_reports SET lat=?, lng=?, location_source=?, location_accuracy=?, "
                    "structured_json=?, updated_at=datetime('now') WHERE id=?",
                    (loc.get("lat"), loc.get("lng"), loc.get("source"), loc.get("accuracy"),
                     json.dumps(structured, ensure_ascii=False), report["id"]),
                )
                conn.commit()
            return jsonify({"ok": True, "call_id": call_id})
        finally:
            conn.close()

    # ================================================================= APIs ==
    @bp.get("/api/ivr/health")
    @auth_required()
    def ivr_health():
        conn = get_db()
        try:
            status = provider_status()
            status["languages"] = supported_language_codes()
            status["available_locales"] = available_locales()
            status["dev_mode"] = settings.DEV_MODE
            status["encryption_mode"] = security_mod.encryption_mode()
            status["stt"] = {
                "provider": settings.STT_PROVIDER,
                "configured": stt_mod.is_configured(),
                "reason": stt_mod.unavailability_reason(),
            }
            status["ai"] = {
                "provider": settings.AI_PROVIDER,
                "model": settings.AI_MODEL,
                "configured": (settings.AI_PROVIDER or "none").lower() == "openai_compatible" and bool(settings.AI_API_KEY),
            }
            status["jobs"] = jobs.queue_stats(conn)
            status["worker_enabled"] = settings.WORKER_ENABLED
            return jsonify(status)
        finally:
            conn.close()

    @bp.get("/api/ivr/config")
    @auth_required()
    def ivr_get_config():
        from flask import g
        if getattr(g, "user", {}).get("role") not in _roles_admin():
            return jsonify({"error": "Forbidden for this role"}), 403
        conn = get_db()
        try:
            rows = conn.execute("SELECT key, value, updated_by, updated_at FROM ivr_config ORDER BY key").fetchall()
            overrides = {}
            for r in rows:
                try:
                    overrides[r["key"]] = json.loads(r["value"])
                except Exception:
                    overrides[r["key"]] = r["value"]
            return jsonify({"settings": settings.masked(), "overrides": overrides,
                            "supported_languages": supported_language_codes(),
                            "available_locales": available_locales()})
        finally:
            conn.close()

    @bp.put("/api/ivr/config")
    @auth_required()
    def ivr_put_config():
        from flask import g
        role = getattr(g, "user", {}).get("role")
        if role not in _roles_admin():
            return jsonify({"error": "Forbidden for this role"}), 403
        data = request.get_json(silent=True) or {}
        overrides = data.get("overrides") or data
        if not isinstance(overrides, dict):
            return jsonify({"error": "overrides must be an object"}), 400
        conn = get_db()
        try:
            from .config import Settings, SECRET_KEYS
            attr_keys = {k for k in vars(Settings) if k.isupper()}
            saved, rejected = {}, []
            for key, value in overrides.items():
                if key in SECRET_KEYS:
                    rejected.append(key)
                    continue
                attr = key if key in attr_keys else (
                    key[len("IVR_"):] if key.startswith("IVR_") and key[len("IVR_"):].upper() in attr_keys else None)
                if attr is None:
                    rejected.append(key)
                    continue
                conn.execute(
                    "INSERT INTO ivr_config (key, value, updated_by, updated_at) VALUES (?,?,?,datetime('now')) "
                    "ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated_by=excluded.updated_by, "
                    "updated_at=datetime('now')",
                    (key, json.dumps(value), getattr(g, "user", {}).get("name", "admin")),
                )
                saved[key] = value
            conn.commit()
            audit_log(conn, "IVR_CONFIG_UPDATED", "ivr_config", "settings",
                      actor_id=getattr(g, "user", {}).get("uid"),
                      actor_name=getattr(g, "user", {}).get("name"), actor_role=role,
                      details={"saved": sorted(saved.keys()), "rejected": rejected})
            conn.commit()
            from .config import load_db_overrides
            load_db_overrides(get_db)
            return jsonify({"saved": saved, "rejected": rejected})
        finally:
            conn.close()

    # ------------------------------------------------------------- survey --
    @bp.get("/api/ivr/survey")
    @auth_required()
    def ivr_get_survey():
        from flask import g
        if getattr(g, "user", {}).get("role") not in _roles_admin():
            return jsonify({"error": "Forbidden for this role"}), 403
        conn = get_db()
        try:
            row, definition = survey_mod.get_active_survey(conn)
            return jsonify({"id": row["id"], "code": row["code"], "version": row["version"],
                            "definition": definition})
        finally:
            conn.close()

    @bp.put("/api/ivr/survey")
    @auth_required()
    def ivr_put_survey():
        from flask import g
        role = getattr(g, "user", {}).get("role")
        if role not in _roles_admin():
            return jsonify({"error": "Forbidden for this role"}), 403
        data = request.get_json(silent=True) or {}
        definition = data.get("definition") or data
        conn = get_db()
        try:
            survey_id = survey_mod.save_survey(conn, definition, updated_by=getattr(g, "user", {}).get("name", "admin"))
            audit_log(conn, "IVR_SURVEY_UPDATED", "ivr_survey", survey_id,
                      actor_id=getattr(g, "user", {}).get("uid"),
                      actor_name=getattr(g, "user", {}).get("name"), actor_role=role,
                      details={"questions": len(definition.get("questions", []))})
            conn.commit()
            return jsonify({"ok": True, "survey_id": survey_id})
        except ValueError as exc:
            return jsonify({"error": str(exc)}), 400
        finally:
            conn.close()

    # -------------------------------------------------------------- calls ---
    @bp.get("/api/ivr/calls")
    @auth_required()
    def ivr_calls():
        from flask import g
        role = getattr(g, "user", {}).get("role")
        if role not in _roles_view() and role not in _roles_admin():
            return jsonify({"error": "Forbidden for this role"}), 403
        conn = get_db()
        try:
            limit = min(int(request.args.get("limit", 50)), 200)
            status = request.args.get("status")
            query = "SELECT * FROM ivr_calls WHERE 1=1"
            params = []
            if status:
                query += " AND status=?"
                params.append(status)
            query += " ORDER BY id DESC LIMIT ?"
            params.append(limit)
            rows = conn.execute(query, params).fetchall()
            out = []
            for r in rows:
                d = dict(r)
                d["caller_number_encrypted"] = None  # never return ciphertext
                out.append(d)
            return jsonify(out)
        finally:
            conn.close()

    @bp.get("/api/ivr/calls/<int:call_id>")
    @auth_required()
    def ivr_call_detail(call_id):
        from flask import g
        role = getattr(g, "user", {}).get("role")
        if role not in _roles_view() and role not in _roles_admin():
            return jsonify({"error": "Forbidden for this role"}), 403
        conn = get_db()
        try:
            call = conn.execute("SELECT * FROM ivr_calls WHERE id=?", (call_id,)).fetchone()
            if not call:
                return jsonify({"error": "call not found"}), 404
            d = dict(call)
            d["caller_number_encrypted"] = None
            d["responses"] = [dict(r) for r in conn.execute(
                "SELECT * FROM ivr_responses WHERE call_id=? ORDER BY id", (call_id,)).fetchall()]
            d["transcripts"] = [dict(r) for r in conn.execute(
                "SELECT * FROM ivr_transcripts WHERE call_id=? ORDER BY id", (call_id,)).fetchall()]
            d["events"] = [dict(r) for r in conn.execute(
                "SELECT * FROM ivr_events WHERE call_id=? ORDER BY id", (call_id,)).fetchall()]
            d["locations"] = [dict(r) for r in conn.execute(
                "SELECT * FROM ivr_location_captures WHERE call_id=? ORDER BY id", (call_id,)).fetchall()]
            return jsonify(d)
        finally:
            conn.close()

    # ------------------------------------------------------------ reports ---
    @bp.get("/api/ivr/reports")
    @auth_required()
    def ivr_reports():
        from flask import g
        role = getattr(g, "user", {}).get("role")
        uid = getattr(g, "user", {}).get("uid")
        if role not in _roles_view() and role not in _roles_admin() and role != "owner":
            return jsonify({"error": "Forbidden for this role"}), 403
        conn = get_db()
        try:
            limit = min(int(request.args.get("limit", 50)), 200)
            query = ("SELECT r.*, c.caller_number_masked, c.provider_call_id, c.provider "
                     "FROM ivr_reports r LEFT JOIN ivr_calls c ON c.id=r.call_id WHERE 1=1")
            params = []
            if role == "owner":
                query += " AND r.owner_user_id=?"
                params.append(uid)
            for arg in ("status", "urgency", "district"):
                if request.args.get(arg):
                    query += f" AND r.{arg}=?"
                    params.append(request.args.get(arg))
            if request.args.get("duplicates") == "1":
                query += " AND r.is_duplicate=1"
            query += " ORDER BY r.id DESC LIMIT ?"
            params.append(limit)
            rows = conn.execute(query, params).fetchall()
            out = []
            for r in rows:
                d = dict(r)
                structured = json.loads(d.pop("structured_json") or "{}")
                d["animal"] = structured.get("animal", {})
                d["symptoms"] = structured.get("symptoms", [])
                d["location"] = structured.get("location", {})
                d["problem"] = structured.get("problem")
                d["provenance"] = structured.get("provenance")
                if role == "owner":
                    d["caller_number_masked"] = mask_phone(d.get("caller_number_masked") or "")
                out.append(d)
            return jsonify(out)
        finally:
            conn.close()

    @bp.get("/api/ivr/reports/<int:report_id>")
    @auth_required()
    def ivr_report_detail(report_id):
        from flask import g
        role = getattr(g, "user", {}).get("role")
        uid = getattr(g, "user", {}).get("uid")
        conn = get_db()
        try:
            report = conn.execute("SELECT * FROM ivr_reports WHERE id=?", (report_id,)).fetchone()
            if not report:
                return jsonify({"error": "report not found"}), 404
            report = dict(report)
            if role not in _roles_view() and role not in _roles_admin():
                if not (role == "owner" and report.get("owner_user_id") == uid):
                    return jsonify({"error": "Forbidden for this role"}), 403
            call = conn.execute("SELECT * FROM ivr_calls WHERE id=?", (report["call_id"],)).fetchone()
            call_d = dict(call) if call else {}
            if call_d:
                call_d.pop("caller_number_encrypted", None)
            return jsonify({
                "report": report,
                "call": call_d,
                "structured": json.loads(report["structured_json"] or "{}"),
                "responses": [dict(r) for r in conn.execute(
                    "SELECT * FROM ivr_responses WHERE call_id=? ORDER BY id", (report["call_id"],)).fetchall()],
                "transcripts": [dict(r) for r in conn.execute(
                    "SELECT * FROM ivr_transcripts WHERE call_id=? ORDER BY id", (report["call_id"],)).fetchall()],
                "recordings": [dict(r) for r in conn.execute(
                    "SELECT id, call_id, duration_seconds, status, storage_backend, created_at, consent_obtained "
                    "FROM ivr_recordings WHERE call_id=? ORDER BY id", (report["call_id"],)).fetchall()],
                "locations": [dict(r) for r in conn.execute(
                    "SELECT * FROM ivr_location_captures WHERE call_id=? ORDER BY id", (report["call_id"],)).fetchall()],
                "events": [dict(r) for r in conn.execute(
                    "SELECT * FROM ivr_events WHERE call_id=? OR report_id=? ORDER BY id",
                    (report["call_id"], report_id)).fetchall()],
            })
        finally:
            conn.close()

    @bp.post("/api/ivr/reports/<int:report_id>/status")
    @auth_required()
    def ivr_report_status(report_id):
        from flask import g
        role = getattr(g, "user", {}).get("role")
        if role not in _roles_view() and role not in _roles_admin():
            return jsonify({"error": "Forbidden for this role"}), 403
        data = request.get_json(silent=True) or {}
        conn = get_db()
        try:
            report, error = report_service.update_status(
                conn, report_id, data.get("status"), context=context,
                actor_name=getattr(g, "user", {}).get("name"), actor_role=role,
                note=data.get("note"))
            if error:
                return jsonify({"error": error}), 400
            return jsonify(dict(report))
        finally:
            conn.close()

    @bp.post("/api/ivr/reports/<int:report_id>/verify")
    @auth_required()
    def ivr_report_verify(report_id):
        """Veterinarian confirms/corrects the AI summary (human verification)."""
        from flask import g
        role = getattr(g, "user", {}).get("role")
        if role != "vet":
            return jsonify({"error": "Only a veterinarian can verify a report"}), 403
        data = request.get_json(silent=True) or {}
        conn = get_db()
        try:
            report = conn.execute("SELECT * FROM ivr_reports WHERE id=?", (report_id,)).fetchone()
            if not report:
                return jsonify({"error": "report not found"}), 404
            conn.execute(
                "UPDATE ivr_reports SET ai_verified_by_vet=1, updated_at=datetime('now') WHERE id=?", (report_id,)
            )
            if data.get("status"):
                report_service.update_status(conn, report_id, data.get("status"), context=context,
                                             actor_name=getattr(g, "user", {}).get("name"),
                                             actor_role="VET", note=data.get("note") or "Verified by veterinarian")
            conn.execute(
                "INSERT INTO ivr_events (call_id, report_id, event_type, actor, actor_type, details) "
                "VALUES (?,?,'REPORT_VERIFIED_BY_VET',?,'VET',?)",
                (report["call_id"], report_id, getattr(g, "user", {}).get("name"),
                 json.dumps({"note": data.get("note")})),
            )
            conn.commit()
            return jsonify({"ok": True})
        finally:
            conn.close()

    @bp.post("/api/ivr/reports/<int:report_id>/merge")
    @auth_required()
    def ivr_report_merge(report_id):
        from flask import g
        role = getattr(g, "user", {}).get("role")
        if role not in (settings.MERGE_ROLES or ["govt", "vet"]):
            return jsonify({"error": "Forbidden for this role"}), 403
        data = request.get_json(silent=True) or {}
        target = data.get("target_report_id")
        conn = get_db()
        try:
            report, error = report_service.merge_reports(
                conn, report_id, target, context=context,
                actor_name=getattr(g, "user", {}).get("name"), actor_role=role.upper())
            if error:
                return jsonify({"error": error}), 400
            return jsonify(dict(report))
        finally:
            conn.close()

    @bp.get("/api/ivr/reports/<int:report_id>/recording")
    @auth_required()
    def ivr_recording_download(report_id):
        """Role-restricted access to a call recording (never public)."""
        from flask import g
        role = getattr(g, "user", {}).get("role")
        uid = getattr(g, "user", {}).get("uid")
        if role not in (settings.RECORDING_ROLES or ["govt", "vet"]):
            return jsonify({"error": "Forbidden for this role"}), 403
        conn = get_db()
        try:
            report = conn.execute("SELECT * FROM ivr_reports WHERE id=?", (report_id,)).fetchone()
            if not report:
                return jsonify({"error": "report not found"}), 404
            if role == "vet" and report["assigned_vet_id"] != uid:
                return jsonify({"error": "Recording is restricted to the assigned veterinarian"}), 403
            recording = conn.execute(
                "SELECT * FROM ivr_recordings WHERE call_id=? ORDER BY id DESC LIMIT 1",
                (report["call_id"],)).fetchone()
            if not recording:
                return jsonify({"error": "no recording for this report"}), 404
            if not recording["consent_obtained"]:
                return jsonify({"error": "recording consent was not granted by the caller"}), 403
            if recording["storage_backend"] == "local" and recording["storage_uri"]:
                from flask import send_file
                return send_file(recording["storage_uri"], mimetype=recording["mime_type"] or "audio/mpeg")
            prov = provider()
            try:
                data, ctype = prov.fetch_recording(recording["provider_recording_id"], url=recording["storage_uri"])
            except Exception as exc:
                return jsonify({"error": f"recording download failed: {exc}"}), 502
            audit_log(conn, "IVR_RECORDING_ACCESSED", "ivr_report", report_id,
                      actor_id=uid, actor_name=getattr(g, "user", {}).get("name"), actor_role=role)
            conn.commit()
            return Response(data, mimetype=ctype)
        finally:
            conn.close()

    @bp.get("/api/ivr/case/<int:case_id>")
    @auth_required()
    def ivr_case_panel(case_id):
        """IVR details for an existing case (used by the case-detail screen)."""
        from flask import g
        role = getattr(g, "user", {}).get("role")
        uid = getattr(g, "user", {}).get("uid")
        conn = get_db()
        try:
            report = conn.execute("SELECT * FROM ivr_reports WHERE case_id=?", (case_id,)).fetchone()
            if not report:
                return jsonify({"report": None})
            # Same access rules as the existing case endpoint
            if role == "owner" and report["owner_user_id"] != uid:
                return jsonify({"report": None})
            if role not in _roles_view() and role not in _roles_admin() and role != "owner":
                return jsonify({"report": None})
            d = dict(report)
            structured = json.loads(d.pop("structured_json") or "{}")
            call = conn.execute("SELECT * FROM ivr_calls WHERE id=?", (report["call_id"],)).fetchone()
            return jsonify({
                "report": d,
                "structured": structured,
                "call": {k: v for k, v in (dict(call).items() if call else {}) if k != "caller_number_encrypted"},
                "transcript": (dict(call) or {}).get("id") and [
                    dict(r) for r in conn.execute(
                        "SELECT participant_role, language, text, created_at FROM ivr_transcripts WHERE call_id=?",
                        (report["call_id"],)).fetchall()],
                "role": role,
            })
        finally:
            conn.close()

    # ---------------------------------------------------------- analytics ---
    @bp.get("/api/ivr/analytics")
    @auth_required()
    def ivr_analytics():
        from flask import g
        role = getattr(g, "user", {}).get("role")
        if role not in _roles_view() and role not in _roles_admin():
            return jsonify({"error": "Forbidden for this role"}), 403
        conn = get_db()
        try:
            total_calls = conn.execute("SELECT COUNT(*) c FROM ivr_calls").fetchone()["c"]
            completed = conn.execute("SELECT COUNT(*) c FROM ivr_calls WHERE status='COMPLETED'").fetchone()["c"]
            partial = conn.execute("SELECT COUNT(*) c FROM ivr_calls WHERE status='PARTIALLY_COMPLETED'").fetchone()["c"]
            abandoned = conn.execute(
                "SELECT COUNT(*) c FROM ivr_calls WHERE status IN ('FAILED','NO_ANSWER','BUSY')").fetchone()["c"]
            vet_connected = conn.execute("SELECT COUNT(*) c FROM ivr_calls WHERE flow='VET_CONNECT'").fetchone()["c"]
            vet_unavailable = conn.execute(
                "SELECT COUNT(*) c FROM ivr_events WHERE event_type='VET_UNAVAILABLE'").fetchone()["c"]
            avg_duration = conn.execute(
                "SELECT AVG(duration_seconds) a FROM ivr_calls WHERE duration_seconds IS NOT NULL").fetchone()["a"]
            reports = conn.execute("SELECT COUNT(*) c FROM ivr_reports").fetchone()["c"]
            duplicates = conn.execute("SELECT COUNT(*) c FROM ivr_reports WHERE is_duplicate=1").fetchone()["c"]
            high = conn.execute(
                "SELECT COUNT(*) c FROM ivr_reports WHERE urgency IN ('HIGH','CRITICAL')").fetchone()["c"]
            resolved = conn.execute(
                "SELECT COUNT(*) c FROM ivr_reports WHERE status IN ('RESOLVED','CLOSED')").fetchone()["c"]

            by_district = conn.execute(
                "SELECT COALESCE(NULLIF(TRIM(district),''),'Unknown') label, COUNT(*) value "
                "FROM ivr_reports GROUP BY LOWER(label) ORDER BY value DESC").fetchall()
            by_urgency = conn.execute(
                "SELECT urgency label, COUNT(*) value FROM ivr_reports GROUP BY urgency").fetchall()
            by_status = conn.execute(
                "SELECT status label, COUNT(*) value FROM ivr_reports GROUP BY status").fetchall()
            by_language = conn.execute(
                "SELECT language label, COUNT(*) value FROM ivr_calls GROUP BY language").fetchall()
            by_location_source = conn.execute(
                "SELECT location_source label, COUNT(*) value FROM ivr_reports GROUP BY location_source").fetchall()

            problems = conn.execute(
                "SELECT structured_json FROM ivr_reports").fetchall()
            problem_counts = {}
            for row in problems:
                try:
                    structured = json.loads(row["structured_json"] or "{}")
                except ValueError:
                    continue
                key = structured.get("problem") or "Not provided"
                if key in (None, "", NOT_PROVIDED):
                    key = "Not provided"
                problem_counts[key] = problem_counts.get(key, 0) + 1

            first_response = conn.execute(
                "SELECT AVG((julianday(notified_vet_at) - julianday(created_at)) * 86400) a "
                "FROM ivr_reports WHERE notified_vet_at IS NOT NULL").fetchone()["a"]

            return jsonify({
                "totals": {
                    "calls": total_calls,
                    "completed_calls": completed,
                    "partially_completed": partial,
                    "abandoned_calls": abandoned,
                    "vet_connections": vet_connected,
                    "vet_unavailable": vet_unavailable,
                    "reports": reports,
                    "duplicates": duplicates,
                    "high_priority": high,
                    "resolved": resolved,
                    "avg_call_duration_seconds": round(avg_duration or 0, 1),
                    "resolution_rate": round((resolved / reports * 100) if reports else 0, 1),
                    "avg_vet_notification_seconds": round(first_response or 0, 1),
                },
                "reports_by_district": [{"label": r["label"], "value": r["value"]} for r in by_district],
                "reports_by_urgency": [{"label": r["label"], "value": r["value"]} for r in by_urgency],
                "reports_by_status": [{"label": r["label"], "value": r["value"]} for r in by_status],
                "calls_by_language": [{"label": r["label"], "value": r["value"]} for r in by_language],
                "location_sources": [{"label": r["label"], "value": r["value"]} for r in by_location_source],
                "problem_categories": [{"label": k, "value": v} for k, v in
                                       sorted(problem_counts.items(), key=lambda x: -x[1])],
            })
        finally:
            conn.close()

    # ------------------------------------------------------------ vet duty --
    @bp.get("/api/ivr/vets/availability")
    @auth_required()
    def ivr_vets_availability():
        from flask import g
        if getattr(g, "user", {}).get("role") not in _roles_admin():
            return jsonify({"error": "Forbidden for this role"}), 403
        conn = get_db()
        try:
            from .rules import available_vets, ensure_vet_availability
            ensure_vet_availability(conn)
            rows = conn.execute(
                "SELECT a.*, u.full_name, u.mobile, u.district FROM ivr_vet_availability a "
                "JOIN users u ON u.id=a.vet_id ORDER BY u.full_name").fetchall()
            on_duty = available_vets(conn)
            return jsonify({"vets": [dict(r) for r in rows],
                            "on_duty": [{"id": o["vet"]["id"], "name": o["vet"]["full_name"],
                                         "workload": o["workload"]} for o in on_duty]})
        finally:
            conn.close()

    @bp.put("/api/ivr/vets/<int:vet_id>/availability")
    @auth_required()
    def ivr_set_vet_availability(vet_id):
        from flask import g
        role = getattr(g, "user", {}).get("role")
        if role not in _roles_admin():
            return jsonify({"error": "Forbidden for this role"}), 403
        data = request.get_json(silent=True) or {}
        conn = get_db()
        try:
            from .rules import ensure_vet_availability
            ensure_vet_availability(conn)
            conn.execute(
                "UPDATE ivr_vet_availability SET is_available=?, available_from=COALESCE(?, available_from), "
                "available_to=COALESCE(?, available_to), max_open_cases=COALESCE(?, max_open_cases), "
                "notes=COALESCE(?, notes), updated_by=?, updated_at=datetime('now') WHERE vet_id=?",
                (int(bool(data.get("is_available", 1))), data.get("available_from"), data.get("available_to"),
                 data.get("max_open_cases"), data.get("notes"), getattr(g, "user", {}).get("uid"), vet_id),
            )
            audit_log(conn, "IVR_VET_AVAILABILITY_UPDATED", "user", vet_id,
                      actor_id=getattr(g, "user", {}).get("uid"),
                      actor_name=getattr(g, "user", {}).get("name"), actor_role=role,
                      details={"is_available": int(bool(data.get("is_available", 1)))})
            conn.commit()
            return jsonify({"ok": True, "vet_id": vet_id})
        finally:
            conn.close()

    # ---------------------------------------------------------------- jobs --
    @bp.get("/api/ivr/jobs")
    @auth_required()
    def ivr_jobs_list():
        from flask import g
        if getattr(g, "user", {}).get("role") not in _roles_admin():
            return jsonify({"error": "Forbidden for this role"}), 403
        conn = get_db()
        try:
            rows = conn.execute(
                "SELECT * FROM ivr_jobs ORDER BY id DESC LIMIT 100").fetchall()
            return jsonify({"stats": jobs.queue_stats(conn), "jobs": [dict(r) for r in rows]})
        finally:
            conn.close()

    @bp.post("/api/ivr/jobs/run")
    @auth_required()
    def ivr_jobs_run():
        """Process due jobs inline (operations escape hatch / used by tests)."""
        from flask import g
        if getattr(g, "user", {}).get("role") not in _roles_admin():
            return jsonify({"error": "Forbidden for this role"}), 403
        conn = get_db()
        try:
            processed = jobs.run_pending(conn, limit=int(request.args.get("limit", 20)),
                                         context={**context, "provider": provider()})
            return jsonify({"processed": processed, "stats": jobs.queue_stats(conn)})
        finally:
            conn.close()

    # --------------------------------------------------------------- audit --
    @bp.get("/api/ivr/audit")
    @auth_required()
    def ivr_audit():
        from flask import g
        role = getattr(g, "user", {}).get("role")
        if role not in _roles_view() and role not in _roles_admin():
            return jsonify({"error": "Forbidden for this role"}), 403
        conn = get_db()
        try:
            limit = min(int(request.args.get("limit", 100)), 500)
            query = "SELECT * FROM ivr_events WHERE 1=1"
            params = []
            for arg in ("call_id", "report_id", "event_type"):
                if request.args.get(arg):
                    query += f" AND {arg}=?"
                    params.append(request.args.get(arg))
            query += " ORDER BY id DESC LIMIT ?"
            params.append(limit)
            rows = conn.execute(query, params).fetchall()
            return jsonify([dict(r) for r in rows])
        finally:
            conn.close()

    # ----------------------------------------------------- development only --
    @bp.post("/api/ivr/dev/simulate")
    def ivr_dev_simulate():
        """DEVELOPMENT/TEST ONLY - drives the state machine without a provider.

        Disabled unless IVR_DEV_MODE=true. It never places or answers a real
        phone call; it renders the same provider markup the webhook would.
        """
        if not settings.DEV_MODE:
            return jsonify({"error": "IVR_DEV_MODE is disabled; simulation is not available"}), 403
        data = request.get_json(silent=True) or {}
        conn = get_db()
        try:
            from .providers import MarkupBuilder, TelephonyProvider

            class DevProvider(TelephonyProvider):
                name = "dev"
                serializer_flavor = "twilio_xml"

                def configuration_errors(self):
                    return []  # dev only: no credentials needed

            svc = IVRService(DevProvider(), context=context)
            values = {
                "CallSid": data.get("call_sid") or f"DEV{os.urandom(4).hex()}",
                "From": data.get("caller") or "+919800000001",
                "To": settings.IVR_PHONE_NUMBER or "+910000000000",
                "CallStatus": "ringing",
            }
            steps = []
            ctype, body = svc.handle_incoming(conn, svc.provider.parse_incoming(values), values)
            steps.append({"step": "incoming", "body": body})
            for entry in data.get("inputs", []):
                gather = svc.provider.parse_gather({**values, **entry})
                ctype, body = svc.handle_input(conn, gather, {**values, **entry})
                steps.append({"step": entry, "body": body})
            if data.get("finish"):
                svc.handle_status(conn, svc.provider.parse_status({**values, "CallStatus": "completed",
                                                                   "CallDuration": data.get("duration", 60)}), values)
            jobs.run_pending(conn, limit=50, context={**context, "provider": svc.provider})
            call = conn.execute(
                "SELECT * FROM ivr_calls WHERE provider='dev' AND provider_call_id=?",
                (values["CallSid"],)).fetchone()
            report = conn.execute("SELECT * FROM ivr_reports WHERE call_id=?", (call["id"],)).fetchone() if call else None
            return jsonify({
                "dev_mode": True,
                "call": dict(call) if call else None,
                "report": dict(report) if report else None,
                "steps": steps,
                "note": "Development simulation only - no telephony provider was contacted.",
            })
        finally:
            conn.close()

    return bp
