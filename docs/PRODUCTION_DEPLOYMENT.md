# Pashu-Shield — Production Deployment Guide

This is the production runbook for the **existing** Pashu-Shield app. Nothing was
redesigned: the same Flask backend, static frontend, FastAPI ML service, SQLite
database, and IVR channel — made deployable and verified.

## 1. Architecture (as deployed)

```
USERS (browser / phone)
  |
  v
https://<backend-host>                  Render web service `pashu-shield-backend`
  |-- /                                 static frontend (index.html, app.js, GIS, Whisper)
  |-- /api/*                            Flask REST API + auth + GIS + notifications
  |-- /api/ivr/webhook/call             canonical IVR webhook (Twilio/Exotel POST here)
  |-- /api/ivr/webhook/status           telephony status callback
  |-- /api/health                       health check  -> {"status":"ok",...}
  |
  |--- server-to-server (SIH_ML_BACKEND) ---> https://<ml-host>
                                              Render web service `pashu-shield-ml`
                                              /health, /api/predict, /api/outbreak-detection,
                                              /api/forecast, /api/cluster, /api/model-performance
```

* Single-origin: the frontend calls relative `/api`, so **no CORS is needed**
  by default. (Optional split-hosting CORS is supported via `CORS_ORIGINS`.)
* SQLite lives on a **persistent disk** (`SIH_DB_PATH`), so restarts and
  redeploys keep data. Schema + seeds are created automatically and idempotently
  at startup (safe under 2 gunicorn workers via an init file-lock).

## 2. One-time production deploy (Render Blueprint)

Prerequisites: the repo pushed to GitHub; a Render account (free tier works).

1. Render Dashboard -> **New -> Blueprint** -> select this repo.
   `render.yaml` creates both services and wires `SIH_ML_BACKEND` automatically.
2. In the Blueprint deploy screen (or Service -> Environment afterwards), set:
   | Variable | Value |
   |---|---|
   | `SIH_SECRET_KEY` | auto-generated (`generateValue`) — keep it |
   | `IVR_PHONE_NUMBER` | your public PSTN number, E.164 (e.g. `+919000000000`) |
   | `TELEPHONY_PROVIDER` | `mock` until a real provider is configured |
   | `APP_BASE_URL` | `https://<backend-host>` (for IVR SMS location links) |
   | Real telephony only | `TELEPHONY_ACCOUNT_ID`, `TELEPHONY_AUTH_TOKEN`, `TELEPHONY_PHONE_NUMBER`, `TELEPHONY_WEBHOOK_SECRET` |
   | Optional | `OPENAI_API_KEY` (else rule-based IVR summaries are used) |
3. Deploy. Render runs health checks on `/api/health` (backend) and `/health` (ML).
4. Verify (replace hosts):
   ```bash
   curl https://<backend-host>/api/health
   curl https://<ml-host>/health
   # Auth + ML proxy through backend (seed govt login):
   GOVT_TOKEN=$(curl -s -X POST https://<backend-host>/api/auth/login \
     -H 'Content-Type: application/json' \
     -d '{"identifier":"govt@example.com","password":"password123"}' | python3 -c "import sys,json;print(json.load(sys.stdin)['token'])")
   curl "https://<backend-host>/api/govt/ai/predict?district=Pune&disease=FMD" \
     -H "Authorization: Bearer $GOVT_TOKEN"
   ```
   Or run the full sweep from this repo:
   ```bash
   python3 verify_prod.py https://<backend-host> https://<ml-host>
   python3 verify_ivr_e2e.py https://<backend-host>
   ```

Seed logins (change/remove after first production login):
`rajesh@example.com` (owner), `vet1@example.com` (vet),
`govt@example.com` (govt), `lab@example.com` (lab) — password `password123`.

## 3. Helpline 7382210251 (click-to-call + regional routing)

The official Pashu-Shield helpline is **7382210251** (displayed as
**+91 73822 10251**). It is the default of `IVR_PHONE_NUMBER` (backend config
+ `render.yaml`), served to the frontend via the public `/api/ivr/info`
endpoint (no secrets), and rendered as a native-dialer link:

```html
<a href="tel:+917382210251">CALL NOW</a>
```

Farmer journey: landing page / owner dashboard shows the number -> tap
CALL NOW -> the phone's native dialer opens with +917382210251 -> farmer
calls -> (once PSTN is terminated, §4) the application identifies the farmer
by caller ID, reuses the saved language, resolves the region from the
verified profile, routes to an available same-region veterinarian, and falls
back to the multilingual survey (skipping already-known questions) that
auto-creates a `HELPLINE` report + case, notifies the vet, and surfaces in
the government portal, GIS, and analytics.

**Telephony reality (no fabrication):** a website cannot by itself receive a
PSTN/cellular call. The `tel:` link only opens the farmer's native dialer;
the browser never places or receives the cellular call. PSTN termination of
7382210251 on the application's voice webhooks requires a carrier/SIP-trunk
service pointed at `https://<backend-host>/api/ivr/webhook/call`. Until that
carrier dependency is provisioned, the full application-side workflow (mock
transport) is implemented and tested, but live cellular calls to 7382210251
cannot reach the system — and `GET /api/ivr/info` honestly reports
`"pstn_connected": false`. No Twilio/Exotel SDKs or credentials are used.

## 4. IVR production wiring (Twilio / Exotel)

1. In the backend service environment, set `TELEPHONY_PROVIDER=twilio` (or
   `exotel`) plus `TELEPHONY_ACCOUNT_ID`, `TELEPHONY_AUTH_TOKEN`,
   `TELEPHONY_PHONE_NUMBER`, `TELEPHONY_WEBHOOK_SECRET`, `IVR_PHONE_NUMBER`.
2. In the provider console, point the number's **voice webhook** (POST) to:
   `https://<backend-host>/api/ivr/webhook/call`
   and the **status callback** to:
   `https://<backend-host>/api/ivr/webhook/status`
   (Legacy aliases `/api/ivr/webhook/incoming` and `/voice` also work.)
3. Place a test call: language -> main menu -> vet option or report survey ->
   confirm the report appears in govt/vet `IVR reports` and a case is created.
4. Webhook signature verification is enforced whenever the provider is not
   `mock`; rate limiting is 60 req/min per IP.

Without provider credentials the IVR APIs still run in `mock` mode for
testing (`POST /api/ivr/mock/call`), but no real phone calls can arrive.

## 5. Local / staging run (mirrors production)

```bash
python3 -m venv .venv && .venv/bin/pip install -r backend/requirements.txt -r ml-backend/requirements.txt
# terminal 1 (ML):
cd ml-backend && ../.venv/bin/python -m uvicorn main:app --host 0.0.0.0 --port 8000
# terminal 2 (backend + frontend):
cd backend && SIH_ML_BACKEND=http://127.0.0.1:8000 ../.venv/bin/gunicorn app:app \
  --bind 0.0.0.0:5001 --workers 2 --timeout 120
# open http://localhost:5001  |  health: /api/health  |  ML: http://localhost:8000/health
```

## 6. Production notes

* `SIH_DB_PATH` overrides the SQLite location (Render disk). Locally the
  default `backend/animal_health.db` is used and auto-created.
* `FLASK_DEBUG` defaults ON only for local `python app.py` on port 5001;
  gunicorn/Render never runs with debug.
* Error responses are generic (no stack traces); role checks return 401/403.
* Frontend uses hash routing (`#/...`), so no server-side SPA fallback is needed.
* Large static assets (Whisper ONNX ~44 MB, ONNX wasm ~37 MB, geojson 1.4 MB)
  are served by Flask directly — no CDN required.
