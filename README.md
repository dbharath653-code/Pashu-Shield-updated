# Pashu-Shield / PashuMitra

Pashu-Shield is a livestock health reporting and surveillance platform for
farmers, veterinarians, laboratories, and government officers. The repository
contains the complete application:

- **Flask backend**: authentication, role-based portals, animal/ herd records,
  cases, labs, prescriptions, vaccinations, GIS, notifications, weather, and
  IVR reporting APIs.
- **Static frontend**: served by Flask from `frontend/`; it calls the backend
  with same-origin relative `/api` URLs.
- **FastAPI ML service**: risk prediction, outbreak detection, forecasting, and
  clustering in `ml-backend/`.
- **IVR and helpline**: multilingual application-side IVR flow, mock transport,
  and an optional self-hosted Asterisk/SIP gateway in `pbx/`.
- **SQLite persistence**: the schema and demo records are created idempotently
  on first startup. Set `SIH_DB_PATH` to a persistent disk in production.

> **Important:** the web application cannot receive a cellular call by itself.
> The `tel:` button opens the farmer's dialer. Live calls to helpline
> **7382210251** require a carrier/SIP trunk terminating on the optional PBX.
> Until that is provisioned, use the mock IVR flow and expect
> `pstn_connected: false`.

## Fastest production deployment: Render Blueprint

This is the recommended path. `render.yaml` creates the backend and ML web
services and wires the backend to the ML service automatically.

### Prerequisites

1. Push or fork this repository on GitHub.
2. Create a Render account.
3. Use a Render plan that supports the persistent disk declared in
   `render.yaml` if production data must survive deploys/restarts. SQLite on an
   ephemeral filesystem is suitable only for a temporary demo.

### Deploy

1. In Render, choose **New → Blueprint** and select this repository.
2. Confirm that Render detects the root `render.yaml` and creates:
   - `pashu-shield-backend` — Flask app plus frontend and API.
   - `pashu-shield-ml` — FastAPI model service.
3. Let Render generate `SIH_SECRET_KEY`; never replace it with the example
   value.
4. In the backend service environment, set or confirm:

   | Variable | Value |
   |---|---|
   | `SIH_DB_PATH` | `/var/data/pashu-shield/animal_health.db` (already in the Blueprint) |
   | `SIH_ML_BACKEND` | populated from the ML service by the Blueprint |
   | `IVR_PHONE_NUMBER` | `7382210251` |
   | `TELEPHONY_PROVIDER` | `mock` until the optional PBX is live |
   | `APP_BASE_URL` | the public backend URL, for example `https://pashu-shield-backend.onrender.com` |
   | `OPENAI_API_KEY` | optional; rule-based IVR summaries work without it |

5. Deploy both services. Wait until the two health checks are green.
6. Verify the deployment, replacing the URLs with the actual Render URLs:

   ```bash
   curl https://<backend-host>/api/health
   curl https://<ml-host>/health

   # Full application/API sweep (run from this repository):
   python3 verify_prod.py https://<backend-host> https://<ml-host>
   python3 verify_ivr_e2e.py https://<backend-host>
   ```

The backend serves the user interface at `https://<backend-host>/`; no separate
frontend deployment or CORS configuration is needed for the default setup.

### First production login

The database seeds demo accounts so that the first deployment can be checked:

| Role | Email | Initial password |
|---|---|---|
| Owner | `rajesh@example.com` | `password123` |
| Veterinarian | `vet1@example.com` | `password123` |
| Government | `govt@example.com` | `password123` |
| Laboratory | `lab@example.com` | `password123` |

Log in once to validate each portal, then change the passwords or remove the
demo accounts before real use. Do not publish these credentials.

For the full production runbooks and the honest feature-by-feature implementation matrix, see:

- [`docs/FEATURE_IMPLEMENTATION_STATUS.md`](docs/FEATURE_IMPLEMENTATION_STATUS.md)
- [`docs/PRODUCTION_DEPLOYMENT.md`](docs/PRODUCTION_DEPLOYMENT.md)
- [`docs/IVR_DEPLOYMENT.md`](docs/IVR_DEPLOYMENT.md)
- [`docs/PSTN_SIP_PBX.md`](docs/PSTN_SIP_PBX.md)
- [`pbx/README.md`](pbx/README.md)

## Run locally

### One command

On macOS/Linux:

```bash
./run-local.sh
```

On Windows, double-click `run-local.bat` or run it from Command Prompt. The
scripts create `.venv`, install both requirement files, start the ML service on
port `8000`, start the backend/frontend on port `5001`, and open the app.

### Manual startup

```bash
python3 -m venv .venv
.venv/bin/pip install -r backend/requirements.txt -r ml-backend/requirements.txt

# Terminal 1: ML service
cd ml-backend
../.venv/bin/python -m uvicorn main:app --host 0.0.0.0 --port 8000

# Terminal 2: backend + frontend
cd backend
SIH_SECRET_KEY='local-only-secret' \
SIH_ML_BACKEND='http://127.0.0.1:8000' \
../.venv/bin/gunicorn app:app --bind 0.0.0.0:5001 --workers 2 --timeout 120
```

Open <http://localhost:5001>. Useful local endpoints:

- Backend health: <http://localhost:5001/api/health>
- ML health: <http://localhost:8000/health>
- ML Swagger UI: <http://localhost:8000/docs>
- Mock IVR call: `POST http://localhost:5001/api/ivr/mock/call`

For local environment overrides, export the variables in your shell (the
application intentionally does not require `python-dotenv`):

```bash
export SIH_SECRET_KEY='local-only-secret'
export SIH_ML_BACKEND='http://127.0.0.1:8000'
```

`backend/.env.example` is a reference for these variables and for Render; if
you keep values in a local `.env` file, load it into the process explicitly
(for example, `set -a; . backend/.env; set +a`). Never commit secrets.

## Test and verify

From the repository root:

```bash
# Unit/integration suite (100 tests at the time of writing)
.venv/bin/python -m unittest discover -s backend -p 'test_*.py' -v

# Start both services first, then run the live checks:
.venv/bin/python verify_prod.py http://127.0.0.1:5001 http://127.0.0.1:8000
.venv/bin/python verify_ivr_e2e.py http://127.0.0.1:5001
```

The tests use the ignored local SQLite file `backend/animal_health.db`. For a
clean run, point `SIH_DB_PATH` at a temporary path before starting Python.

## Optional live helpline / PSTN setup

The application-side IVR is ready to test with the mock provider. A real
cellular call requires all of the following, outside the Git repository:

1. A public Linux VM for Asterisk with reachable SIP/RTP ports.
2. A carrier SIP trunk that legally terminates or forwards `7382210251` to
   that VM.
3. The same strong `PBX_WEBHOOK_SECRET` in the Render backend and PBX host.
4. `TELEPHONY_PROVIDER=sip` and, preferably, `PBX_ALLOWED_IPS` set to the PBX
   egress IP in Render.
5. A real mobile test call verifying DTMF, audio, vet routing, survey fallback,
   case creation, and notifications.

Follow [`docs/PSTN_SIP_PBX.md`](docs/PSTN_SIP_PBX.md) rather than exposing an
ordinary personal SIM or opening an unauthenticated SIP gateway. The API only
reports `pstn_connected: true` after an authenticated real inbound call is
observed.

## Repository layout

```text
backend/       Flask API, SQLite layer, IVR modules, tests
frontend/      Static PashuMitra application and local Whisper assets
ml-backend/    FastAPI ML API and committed model artifacts
pbx/           Optional Asterisk/SIP transport and installer
docs/          Production, IVR, and PBX runbooks
render.yaml    Two-service Render Blueprint
run-local.sh   One-command macOS/Linux development startup
run-local.bat  One-command Windows development startup
```
