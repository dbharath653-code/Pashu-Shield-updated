# PashuMitra IVR Reporting Channel — Deployment & Operations

This document covers **only the new IVR channel**. The existing web application
deployment is unchanged.

## 1. What was added

```
PHONE CALL
    ↓  (licensed telephony provider: Twilio / Exotel / Plivo / custom)
POST /ivr/voice            (signature-verified, idempotent webhook)
    ↓
IVRService (state machine, DB-backed sessions)
    ↓  language select → main menu
┌──────────────────────────────┐
│ 1 — Talk to a veterinarian   │  → real <Dial> bridge to an on-duty vet
│ 2 — Report a problem         │  → multilingual automated survey (DTMF + speech)
└──────────────────────────────┘
    ↓  call ends (provider status webhook / normal completion)
ivr_jobs queue (durable, retried)
    ↓
TRANSCRIBE → SUMMARISE (structured, evidence-grounded) →
create existing user/animal/case rows → notify vet + govt (existing
notifications table) → IVR report visible in the web app
```

## 2. Environment variables

No secret, phone number or provider is hardcoded. Configure in the deployment
(Render dashboard / systemd / docker env).

### Telephony (required to answer real calls)

| Variable | Meaning |
|---|---|
| `IVR_PROVIDER` | `twilio` \| `exotel` \| `plivo` \| `custom` (empty = unconfigured; the IVR answers "service not configured" and does **not** fake anything) |
| `IVR_PHONE_NUMBER` | the IVR number farmers call (used for display + caller ID) |
| `TELEPHONY_ACCOUNT_ID` | Twilio Account SID / Plivo Auth ID |
| `TELEPHONY_AUTH_TOKEN` | Twilio/Plivo auth token (also verifies webhooks) |
| `TELEPHONY_PHONE_NUMBER` | provider number that receives the calls |
| `TELEPHONY_API_KEY` / `TELEPHONY_API_SECRET` | Exotel API credentials |
| `TELEPHONY_SID` / `TELEPHONY_SUBDOMAIN` | Exotel account SID / subdomain |
| `TELEPHONY_BASE_URL` / `TELEPHONY_MARKUP` | custom provider REST base + markup dialect (`twilio_xml`, `plivo_xml`, `exotel_xml`) |
| `TELEPHONY_WEBHOOK_SECRET` | shared secret for providers that sign with HMAC/token (Exotel/custom) |
| `IVR_PUBLIC_BASE_URL` | public HTTPS base URL of the backend (signature reconstruction + GPS consent links) |

### Security

| Variable | Meaning |
|---|---|
| `IVR_REQUIRE_WEBHOOK_SIGNATURE` | `true` (default): unverifiable webhooks are rejected with 403 |
| `IVR_PHONE_ENCRYPTION_KEY` | Fernet key (from `cryptography.fernet.Fernet.generate_token()`) — when set, caller numbers are encrypted at rest; when unset, only an HMAC lookup hash + masked form is stored (never the plaintext) |
| `IVR_PHONE_HASH_SECRET` | HMAC secret for the duplicate-detection hash |
| `IVR_LOCATION_TOKEN_SECRET` | signs the browser GPS consent links |
| `IVR_WEBHOOK_IP_ALLOWLIST` | optional comma list (Exotel/custom fallback control) |
| `IVR_RATE_LIMIT_PER_MINUTE` | per-IP webhook rate limit (default 120) |

### Languages & behaviour

| Variable | Meaning | Default |
|---|---|---|
| `IVR_SUPPORTED_LANGUAGES` | comma list of enabled languages (en, hi, te, mr) | `en,te,hi,mr` |
| `IVR_DEFAULT_LANGUAGE` | fallback language | `en` |
| `IVR_RECORDING_ENABLED` | allow call recording (vet bridge) | `false` |
| `IVR_RECORDING_CONSENT_REQUIRED` | always ask consent before recording | `true` |
| `IVR_RECORDING_ROLES` | who may download recordings | `govt,vet` |
| `IVR_AUTO_ASSIGN_VET` | auto-assign a district vet on new reports | `true` |
| `IVR_NOTIFY_GOV_USERS` | notify government portal users | `true` |
| `IVR_DUPLICATE_WINDOW_MINUTES` | duplicate detection window | `1440` |
| `IVR_VET_AVAILABLE_FROM` / `IVR_VET_AVAILABLE_TO` | default on-duty window | `08:00` / `20:00` |
| `IVR_WORKER_ENABLED` | in-process worker (disable when the external worker service runs) | `true` |
| `IVR_DEV_MODE` | enables `POST /api/ivr/dev/simulate` (test-only, no telephony) | `false` |

### Speech-to-text (optional)

| Variable | Meaning |
|---|---|
| `IVR_STT_PROVIDER` | `none` \| `provider_native` (Twilio `<Record transcribe>`) \| `whisper_http` \| `openai_compatible` |
| `IVR_STT_WHISPER_URL` | any Whisper/faster-whisper/whisper.cpp HTTP endpoint (reuse of existing Whisper infrastructure) |
| `IVR_STT_BASE_URL` / `IVR_STT_API_KEY` / `IVR_STT_MODEL` | OpenAI-compatible `/audio/transcriptions` |
| `IVR_STT_MIN_CONFIDENCE` | below this the IVR asks the farmer to repeat (never guesses) |

With **no STT configured** the IVR still works: DTMF answers are used,
transcripts are marked unavailable, and the pipeline reports exactly that.

### AI summarisation (optional)

| Variable | Meaning |
|---|---|
| `IVR_AI_PROVIDER` | `none` \| `openai_compatible` |
| `IVR_AI_BASE_URL` / `IVR_AI_API_KEY` / `IVR_AI_MODEL` | chat-completions endpoint |

With **no LLM configured** an evidence-based deterministic extractor builds the
structured report from what was actually said; the report is labelled
"System-generated … — veterinarian verification required." Both outputs pass
the same schema validation + grounding check that removes anything not
supported by the transcript.

## 3. Provider webhook setup

Point the provider's IVR/voice app at:

| Provider setting | URL |
|---|---|
| Voice URL (answer + collect) | `https://<backend>/ivr/voice` (POST) |
| Status callback | `https://<backend>/ivr/status` (POST) |
| Recording ready | `https://<backend>/ivr/recording` (POST) |
| Transcription ready (provider-native STT) | `https://<backend>/ivr/transcription` (POST) |

Enable recordings on the voice app **only if** `IVR_RECORDING_ENABLED=true`;
the IVR itself asks the farmer for consent before a vet bridge is recorded.
For Twilio: `<Dial record="record-from-answer-dual">` is emitted by the IVR
only after consent, and `<Record transcribe="true">` can be added to the voice
app for `IVR_STT_PROVIDER=provider_native`.

## 4. Services (Render)

* `pashu-shield-backend` — existing web API + IVR webhooks/APIs (gunicorn, 2 workers)
* `pashu-shield-ml` — existing ML service (unchanged)
* `pashu-shield-ivr-worker` — **new** worker (`python -m ivr.worker`) that runs
  the durable post-call job queue. Set `IVR_DISABLE_INLINE_WORKER=1` on the
  backend so jobs are processed once.

SQLite note: the current platform stores its database in a file
(`animal_health.db`, WAL mode). For production durability the database should
live on persistent storage (Render Disk) or be migrated to Postgres — this
affects the existing app and the IVR channel equally; the IVR layer uses the
same connection API so a driver swap is contained in `database.get_db()`.

## 5. Role-based access (web app)

| Route | Roles |
|---|---|
| `/api/ivr/reports`, `/api/ivr/calls`, `/api/ivr/audit` | `govt`, `vet` (owners see only their own reports) |
| `/api/ivr/reports/<id>/recording` | `govt`, assigned `vet` only — and only when consent was granted |
| `/api/ivr/config`, `/api/ivr/survey`, `/api/ivr/vets/*`, `/api/ivr/jobs*` | `govt` (admin) |
| `/api/ivr/reports/<id>/status` | `govt`, `vet` (kept in sync with the existing case workflow) |
| `/api/ivr/reports/<id>/verify` | `vet` (marks the AI summary as veterinarian-verified) |
| `/api/ivr/dev/simulate` | only when `IVR_DEV_MODE=true` |

## 6. Location strategy (no fake GPS)

| Source | How it is obtained |
|---|---|
| `FARMER_PROVIDED` | village/district/state asked in the survey (always attempted) |
| `REGISTRY` | caller number matched an existing registered farmer/animal profile |
| `NETWORK` | only when the provider's authorised API returns carrier location metadata |
| `GPS` | farmer opens the signed, expiring consent link (`/ivr/location/<token>`) and the **browser** geolocation API returns coordinates after explicit consent; refusal is recorded |
| `NOT_AVAILABLE` | nothing else applied — stored explicitly, never invented |

The source is stored per report and surfaced in the web app and analytics.

## 7. Health & observability

* `GET /api/ivr/health` (authenticated) — provider readiness, missing
  credentials (exact variable names), STT/AI status, job queue stats
* `GET /api/ivr/jobs` + `POST /api/ivr/jobs/run` (admin) — queue state and
  manual drain
* `GET /api/ivr/audit` — IVR event trail; the platform audit table
  (`/api/audit-logs`) also receives every IVR action
* Every webhook event is recorded with its signature verification outcome;
  duplicate deliveries are logged as `WEBHOOK_DUPLICATE_IGNORED`

## 8. Tests

```
cd backend
python -m unittest test_all_features test_regression test_ivr
```

29 IVR tests cover the 16 acceptance scenarios (call answered, language
selection incl. speech, vet bridge, vet-unavailable fallback, full survey →
automatic report, caller capture, legitimate location only, non-hallucinating
summary, vet + govt notifications, partial-disconnect preservation,
speech-failure → DTMF fallback, webhook replay → no duplicate, RBAC, existing
web reporting and no regression in animal/case/lab/GIS/ML endpoints).
