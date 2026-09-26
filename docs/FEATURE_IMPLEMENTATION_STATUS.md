# Pashu-Shield feature implementation status

This change preserves the existing Flask + SQLite + static frontend + IVR/Asterisk architecture. The database is the source of truth. External operations are reported as `NOT_CONFIGURED` or `FAILED` until a provider returns success.

| Feature | Status | Backend | Frontend | Integration | Tests |
|---|---|---|---|---|---|
| Laboratory sample processing | Implemented | Existing sample, custody, receiving, testing, result and verification APIs preserved | Existing laboratory processing queue preserved; no separate lab-only tracking dashboard added | SQLite + audit events | Existing workflow tests pass |
| Veterinary laboratory report tracking | Implemented | `/api/veterinarian/lab-reports`, detail, status, timeline and download APIs with RBAC | Veterinary “Laboratory Reports / Lab Tracking” view, filters and detail timeline | Sample/report lifecycle events | `test_new_workflows` + existing lab tests |
| Realtime report/visit events | Implemented with SSE | `realtime_events`, `/api/realtime/events`, authenticated `/api/realtime/stream`, idempotency | EventSource reconnects and refreshes active case/lab screens | Database-backed push; no fake timers | Event persistence/replay covered |
| Farmer veterinary visit tracking | Implemented | Case-based visit, time-limited tracking sessions, GPS observations, stop rules | Farmer case card/map and status; GPS unavailable is shown honestly | Browser/device geolocation only | RBAC and lifecycle test |
| Government authorized visibility | Implemented | Government RBAC for cases, events, lab alerts and locations | Existing government portal plus realtime event source | No unscoped farmer GPS access | Existing RBAC/IVR tests |
| Helpline app GPS handoff | Implemented | Signed IVR location link plus authenticated `/api/telephony/location`; case propagation | Uses explicit farmer app/device permission when available | PSTN GPS is not claimed; village/district fallback remains textual | Existing no-fake-GPS tests |
| SMS | Implemented as configurable provider | `backend/notifications.py`, `/api/notifications`, delivery status/retry/config health | Delivery errors are returned to UI/API | Generic HTTP SMS adapter; credentials from env | Missing-credentials test |
| WhatsApp/push | Configuration-aware, adapter not enabled | Health state is explicit; no fake success | Not presented as delivered | Requires deployment provider adapter | Not complete until provider is configured |
| Web portal languages | Partial | IVR dictionaries remain centralized for English, Hindi, Marathi, Telugu | English/Marathi existing labels plus Hindi/Telugu core labels and English key fallback | IVR and portal use same four language codes | Existing IVR language tests |
| IVR/Asterisk/SIP | Preserved | Existing signed webhooks, provider abstraction, PBX authorization unchanged | Existing IVR reporting screens | Real PSTN still requires carrier/SIP trunk and PBX secrets | Existing IVR/PBX suite |
| Offline sync | Preserved | Existing idempotent `offline_sync_log` queue | Existing local queue and reconnect sync | New GPS writes are not fabricated offline; retry with idempotency is required | Existing offline tests |

## APIs added or extended

- `POST /api/cases/:id/location`, `GET /api/cases/:id/location`
- `POST /api/visits`, `PATCH /api/visits/:id/status`
- `POST /api/visits/:id/start-tracking`, `POST /api/visits/:id/stop-tracking`
- `POST /api/visits/:id/location`, `GET /api/visits/:id/location`
- `POST /api/telephony/location`
- `GET /api/veterinarian/lab-reports`, `GET /api/veterinarian/lab-reports/:id`
- `PATCH /api/lab-reports/:id/status`, `GET /api/veterinarian/lab-reports/:id/download`
- `POST /api/notifications`
- `GET /api/realtime/token`, `GET /api/realtime/events`, `GET /api/realtime/stream` (the browser uses a short-lived stream token)
- `GET /health`, `GET /health/integrations` (the existing `/api/health` is extended)

## Data and privacy rules

- `case_locations` records source and nullable coordinates. `NOT_AVAILABLE`, profile and verbal village/district data never become a coordinate.
- `visit_tracking_sessions` are tied to one case, veterinarian, expiry time and auditable stop reason.
- `visit_locations` accepts only authenticated assigned-veterinarian observations, validates coordinate ranges, and deduplicates by idempotency key.
- A farmer can see only their case; a veterinarian can see assigned cases and unassigned cases only within the veterinarian's registered district; government sees only its registered jurisdiction (or all jurisdictions when no district is configured).
- `realtime_events` payloads are filtered using the same case authorization before replay or stream delivery.

## Deployment checklist

1. Copy the root `.env.example` or `backend/.env.example`; generate a strong `SIH_SECRET_KEY`.
2. Configure `SIH_DB_PATH` on persistent storage. Run database initialization once on deploy.
3. Configure `SMS_PROVIDER`, `SMS_BASE_URL`, `SMS_API_KEY`, `SMS_SENDER_ID` and optional retry/rate settings for actual SMS. Until then, SMS is `NOT_CONFIGURED`.
4. Configure a real map provider if production map tiles/routing are required. OpenStreetMap tiles are a development/default tile source; no route/ETA is fabricated.
5. For helpline PSTN, provision the carrier/SIP trunk, Asterisk/PBX, `PBX_WEBHOOK_SECRET`, and `PBX_ALLOWED_IPS`. The app cannot receive a cellular call without that external infrastructure.
6. Require HTTPS for the web app, SSE stream, signed location links and telephony gateway.

## Verification

From the repository root:

```bash
.venv/bin/python -m unittest discover -s backend -p 'test_*.py' -v
node --check frontend/app.js
.venv/bin/python -m py_compile backend/app.py backend/database.py backend/notifications.py
```

The test suite covers the preserved workflows plus GPS authorization, real-location-only visit updates, laboratory tracking event replay and the missing-SMS-credentials state. A live SMS provider, map provider, carrier/SIP trunk and production deployment cannot be verified inside a repository-only test run.
