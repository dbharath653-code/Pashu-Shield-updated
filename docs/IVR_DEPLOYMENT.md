# PashuMitra IVR Reporting Channel — Deployment Guide

This document covers production deployment of the IVR extension (not a rewrite) added to the existing PashuMitra app.

## 1. What was added (extension only)

- `backend/ivr/` — telephony abstraction, multilingual IVR flow, survey engine, AI summarizer, report/case integration, jobs, analytics, config, security
- `backend/ivr/locales/` — `en/te/hi` prompts (DTMF + speech)
- `frontend/app.js` — ~300 lines appended: injects IVR cards into existing `govtDashboard/vetDashboard/ownerDashboard` without redesign; adds routes `/#/owner|vet|govt/ivr-reports`, `/#/vet|govt/ivr-calls`, `/#/govt/ivr-analytics|ivr-config`
- Existing UI/APIs/roles unchanged; manual reporting continues to work. Verified by 38-test suite.

## 2. Environment Variables (single source of truth: IVR_PHONE_NUMBER)

Set in Render Dashboard → Service → Environment (or `.env` locally):

```bash
IVR_PHONE_NUMBER=+919000000000        # Required: public PSTN number shown to farmers
TELEPHONY_PROVIDER=twilio              # mock | twilio | exotel | plivo
TELEPHONY_ACCOUNT_ID=ACxxxx            # Provider SID / API key
TELEPHONY_AUTH_TOKEN=...               # Provider auth token (never commit)
TELEPHONY_WEBHOOK_SECRET=...           # HMAC secret for X-Twilio-Signature / X-Exotel-Signature verification
TELEPHONY_PHONE_NUMBER=+919000000000   # Optional override (defaults to IVR_PHONE_NUMBER)
OPENAI_API_KEY=sk-...                  # Optional; if absent, rule-based summarizer used (no hallucination)
ANTHROPIC_API_KEY=                     # Optional alternative
IVR_RECORDING_ENABLED=true
IVR_RECORDING_CONSENT_REQUIRED=true
IVR_LOCATION_SMS_ENABLED=false         # Enable only if SMS gateway configured
IVR_RATE_LIMIT_PER_MINUTE=60
SIH_SECRET_KEY=<generateValue>
```

**Provider abstraction:** `backend/ivr/telephony/factory.py` selects `MockProvider` | `TwilioProvider` | `ExotelProvider` based on `TELEPHONY_PROVIDER`. Only credentials change — no code change required. Add a new provider by implementing `TelephonyProvider` (`make_call`, `verify_signature`, `generate_twiml`, `get_recording`).

## 3. Webhook URL (HTTPS)

Configure your telephony provider's **Voice Webhook** to:

```
https://<your-backend-host>/api/ivr/webhook/call
https://<your-backend-host>/api/ivr/webhook/status   (status callback)
```

(Legacy aliases `/api/ivr/webhook/incoming` and `/api/ivr/webhook/voice` serve the same handler.)

- Must be HTTPS with valid certificate (provider requirement).
- Example (Twilio): Console → Phone Numbers → Active Numbers → Select `IVR_PHONE_NUMBER` → Voice Configuration → Webhook `https://.../api/ivr/webhook/call` (POST), Status Callback `https://.../api/ivr/webhook/status`.
- Example (Exotel): Exotel Dashboard → App Bazaar → IVR App → Connect → URL `https://.../api/ivr/webhook/call`.
- Health check: `GET /api/ivr/health` returns `{ status: "degraded" | "healthy", missing_env: [...], provider_ready }`.

## 4. HTTPS & Reverse Proxy

Render provides TLS termination. No app change needed. If self-hosting (nginx):

```nginx
server {
  listen 443 ssl;
  ssl_certificate /etc/ssl/cert.pem;
  ssl_certificate_key /etc/ssl/key.pem;
  location / { proxy_pass http://127.0.0.1:5001; proxy_set_header Host $host; }
}
```

## 5. Database Migration

IVR tables are created automatically via `init_db()` on startup (idempotent `CREATE TABLE IF NOT EXISTS`):

```
ivr_calls, ivr_sessions, ivr_survey_responses, ivr_transcripts,
ivr_recordings, ivr_reports, ivr_events, ivr_jobs, ivr_analytics_daily
```

Existing data is never dropped. For existing deployments, restart the backend after adding IVR env vars — migration runs in-place. SQLite WAL mode enabled; `busy_timeout=5000` avoids `database is locked` under concurrent webhooks.

## 6. Background Workers (Async)

`IVR_ASYNC_ENABLED=true` (default) spawns daemon threads for:

- Transcription (Whisper/local STT)
- AI structured JSON summarization (validated schema, no hallucination)
- Report + Case + Notification generation

Fallback: if async disabled, jobs run inline in webhook. In production with `gunicorn --workers 2`, threads are per-worker. For horizontal scaling beyond Render, replace `ivr/jobs.py` with Celery/RQ (queue name `ivr_jobs`) — interface is `enqueue_job(conn, call_sid, job_type, payload)`.

## 7. Deployment Checklist

- [ ] Set `IVR_PHONE_NUMBER` and `TELEPHONY_*` (never leave `NOT_CONFIGURED` in prod)
- [ ] Set `TELEPHONY_WEBHOOK_SECRET` and verify `X-Twilio-Signature`/`X-Exotel-Signature` in logs (`verify_signature` in `ivr/services/security.py`)
- [ ] Configure provider webhook URL to HTTPS `/api/ivr/webhook/call` and status callback
- [ ] Run `python -m unittest test_all_features test_regression test_ivr` (38 OK) before deploy
- [ ] Smoke test with mock: `POST /api/ivr/mock/call {"from": "+91..."} → language 1 → menu 2 → answer 21 questions → GET /api/ivr/reports?token=`
- [ ] Enable real provider (`TELEPHONY_PROVIDER=twilio`) and place test call to `IVR_PHONE_NUMBER`
- [ ] Verify govt GIS links cases: `GET /api/cases` shows IVR-linked cases (`source=IVR`)
- [ ] Set `OPENAI_API_KEY` only if external LLM desired; otherwise rule-based summarizer handles all reports safely

## 8. Verification (E2E)

```bash
# Health
curl https://<host>/api/ivr/health

# Mock E2E (no real telephony)
curl -X POST https://<host>/api/ivr/mock/call -H 'Content-Type: application/json' -d '{"from":"+919800000001"}'
# Use returned call_sid to drive language/menu/survey webhooks, then:
curl https://<host>/api/ivr/reports -H 'Authorization: Bearer <govt_token>'
```

Expected: `report.district`, `report.village`, `report.location_source` in {FARMER_PROVIDED, GPS, NETWORK, NOT_AVAILABLE}, never fake GPS; `report.ai_summary` always prefixed with `AI-generated summary — veterinarian verification required.`

## 9. Security

- Webhook signature verification (`HMAC-SHA256`), rate limiting (60/min prod, 1000/min for mock test client), RBAC (govt/vet/owner), encryption for PII (sanitization), audit logging in `notifications`/`ivr_events`.
- Recording consent preamble played before `<Dial record>`; no storage without consent.

## 10. Rollback

Remove `IVR_PHONE_NUMBER` → IVR routes return `degraded` but existing app unchanged. No database rollback needed (new tables only).
