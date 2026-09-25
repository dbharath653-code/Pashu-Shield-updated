# Real PSTN/cellular termination for helpline 7382210251 (self-hosted SIP PBX)

Status of this document: **implementation complete, carrier termination pending**.
The application, the voice gateway and all tests are done; no real call has
reached the system yet, so the backend honestly reports `pstn_connected=false`.
See "Final honesty note" at the end.

No Twilio. No Exotel. No telephony SaaS of any kind.

## 1. PSTN architecture

```
farmer's mobile (any Indian network)
  |  ordinary cellular call to 7382210251
  v
Indian PSTN / carrier voice network
  |  carrier routes the DID to the enterprise SIP trunk
  v
carrier SBC / SIP trunk  (UDP 5060, RTP 10000-10100)
  |
  v
Pashu-Shield voice gateway: Asterisk 20/22 on a public-IP VM  (pbx/)
  |  AGI transport: POSTs existing IVR webhooks over HTTPS,
  |  executes returned TwiML (Say/Gather/Dial/Redirect/Hangup)
  v
Pashu-Shield backend (Render, existing app, TELEPHONY_PROVIDER=sip)
  |  existing IVR: identify -> language -> region -> vet routing
  |  -> bridge (Dial TwiML) or survey -> report -> case -> govt/GIS
  v
vet's mobile (vet leg, carrier trunk)   [only after per-call backend approval]
```

Why this shape (§29): Render web services cannot host reliable SIP/RTP (no
stable UDP ports, no static media IP), so the PBX runs on a small public VM
while the web app stays on Render. The two are joined by HTTPS + a shared
secret. Nothing in the existing application workflow changed: the PBX drives
the *same* `/api/ivr/webhook/*` endpoints the mock transport uses.

Why Asterisk (not FreeSWITCH): simplest fit — the app already speaks TwiML, and
a ~700-line stdlib-only Python AGI (`pbx/agi/pashu_ivr.py`) interprets exactly
the five verbs the backend emits. No extra daemon, no ARI/AMI/websocket
surface, no new language runtime on the PBX host. FreeSWITCH would add a
second SIP stack to operate for zero application benefit.

Why no IVR logic in the PBX (§9): farmer data, vet routing, survey, reports,
AI summary and notifications stay in the backend where they are tested
(96 tests). The PBX only converts SIP<->HTTPS and audio<->DTMF.

## 2. SIP architecture

- **Trunk**: one PJSIP endpoint (`carrier-trunk`, `pbx/asterisk/pjsip.conf`).
  Inbound: carrier delivers the helpline DID to the PBX IP (static IP-auth is
  the norm for Indian DID termination; registration mode is a one-line
  `PBX_SIP_REGISTER=true` toggle if the carrier requires it).
- **Codecs**: ulaw/alaw (universal on Indian trunks); optional G.722.
- **DTMF**: RFC 4733 (RFC 2833). Asterisk also accepts in-band/SIP-INFO per
  channel negotiation; the AGI collects whatever the channel delivers.
- **Caller ID**: passed through from SIP `From` / `P-Asserted-Identity`
  (`trust_id_inbound=yes`). Never invented: withheld CLI arrives empty and the
  backend's existing unknown-caller flow handles it.
- **Vet legs**: dialled by the AGI as `PJSIP/<vet>@carrier-trunk` inside the
  answered inbound call. There is **no outbound dialplan context** — dialling
  out is unreachable except through this one code path, which requires backend
  approval of the exact number (see §9).
- **TLS/SRTP**: template-ready (`PBX_TLS_CERT/KEY`, `PBX_SRTP`) but OFF by
  default — most Indian carrier trunks are UDP/RTP-only. Enable only if the
  carrier supports it; the gateway HTTP leg to the backend is always TLS.

## 3. PBX setup

Operator runbook (Ubuntu 22.04/24.04 VM with a public IP, 1 vCPU / 1 GB is
plenty for helpline concurrency):

```bash
git clone <repo-url> && cd <repo>
sudo mkdir -p /etc/pashu-pbx
sudo cp pbx/pbx.env.example /etc/pashu-pbx/pbx.env
sudo chmod 600 /etc/pashu-pbx/pbx.env
sudo nano /etc/pashu-pbx/pbx.env     # §6 values
sudo bash pbx/install.sh
```

`install.sh` (idempotent) installs Asterisk + offline TTS + fail2ban, renders
configs from `pbx.env`, installs the AGIs, firewall, fail2ban, heartbeat cron
and recording-retention cron, then reloads Asterisk. Details: `pbx/README.md`.

Backend (Render dashboard env):

```
TELEPHONY_PROVIDER=sip
PBX_WEBHOOK_SECRET=<same 32+ byte hex as the PBX>
PBX_ALLOWED_IPS=<PBX egress IP>     # recommended hardening
```

## 4. Required firewall ports (PBX host)

| Port | Proto | From | Purpose |
|---|---|---|---|
| 22 | TCP | operator IPs (rate-limited) | SSH admin |
| 5060 | UDP+TCP | carrier SBC IPs ONLY | SIP signalling |
| 5061 | TCP | carrier SBC IPs ONLY | SIP/TLS (only if enabled) |
| 10000-10100 | UDP | carrier SBC IPs ONLY | RTP media (~4 ports/call) |
| 443 | TCP | outbound | HTTPS to backend (Render) |

Everything else: deny incoming. Enforced by `pbx/security/ufw-rules.sh`
(sourced from `CARRIER_SIP_IPS`, `RTP_PORT_START/END`). AMI (5038), Asterisk
HTTP (8088) and ARI are **disabled**; legacy chan_sip is unloaded — there is
nothing else to scan.

Cloud security groups must mirror the same rules if ufw is bypassed.

## 5. Required DNS

- **Backend**: the PBX needs only the existing public HTTPS hostname of the
  Render backend (`PASHU_BACKEND_URL`, e.g. `https://<app>.onrender.com`).
  No new DNS is required for the application.
- **PBX host**: no DNS is strictly required (the carrier routes by IP), but a
  stable A record (e.g. `pbx.pashu-shield.example`) is recommended so TLS
  certificates and firewall references survive IP changes.
- **Carrier**: no DNS changes needed on our side; the carrier maps the
  helpline DID to the PBX IP in their own routing.

## 6. Environment variables

Backend (`backend/.env.example`; secret values only in Render dashboard):

| Var | Required | Purpose |
|---|---|---|
| `TELEPHONY_PROVIDER` | yes (`sip`) | selects `SIPProvider` (mock stays for dev/tests) |
| `PBX_WEBHOOK_SECRET` | yes (32+ hex chars) | shared gateway secret; auth fails closed when unset |
| `PBX_ALLOWED_IPS` | recommended | PBX egress IPs allowed on `/api/ivr/gateway/*` |
| `PBX_HEARTBEAT_STALE_SECONDS` | no (300) | freshness window for `pbx_healthy` |
| `IVR_PHONE_NUMBER` | no (7382210251) | unchanged helpline |

PBX host (`/etc/pashu-pbx/pbx.env`, chmod 600; template: `pbx/pbx.env.example`):

`PASHU_BACKEND_URL`, `PBX_WEBHOOK_SECRET` (same secret),
`SIP_TRUNK_HOST/PORT`, `SIP_USERNAME`, `SIP_PASSWORD`, `CARRIER_SIP_IPS`
(carrier-provided), `PBX_DID_LAST10=7382210251`, `PBX_SIP_REGISTER`,
`PBX_TRUNK_STATIC`, `RTP_PORT_START/END`, TTS/voice overrides, recording
switch + retention. No variable here is committed to Git, ever.

## 7. Carrier configuration (what to ask for)

Hand this checklist to the carrier / enterprise-voice account team:

1. Terminate DID **7382210251** to our SIP trunk (destination: PBX public IP,
   port 5060/UDP; or credentials if registration-based).
2. Provide: trunk host/IP, port, username/password (if any), the **signalling
   SBC IPs** (for `CARRIER_SIP_IPS` firewalling), codec list, DTMF mode
   (need RFC 4733), and whether CLI is passed through.
3. Confirm the vet-leg policy: outbound calls from the trunk to Indian mobile
   numbers (only ever to vet numbers from our directory), expected number
   format (10-digit national vs +91), and which CLI is presented.
4. Confirm TLS/SRTP support (likely no; then UDP/RTP stands, documented).
5. Provide a test window + a trunk-side call trace contact for the real-mobile
   test (§10).

The carrier is a regulated telecom operator, not a "telephone-directory API":
using a carrier SIP trunk complies with the no-SaaS constraint.

## 8. Number termination configuration

Number-type honesty: from the repository/sandbox side the current nature of
**7382210251** (personal SIM vs business/postpaid vs virtual/DID) **cannot be
verified** — only the number owner / their operator can confirm it. What is
technically certain:

- An ordinary personal mobile SIM **cannot** be "connected to a web server".
  There is no legal or supported mechanism to intercept a SIM's calls into
  SIP (no SIM-bank hacks, no SS7 games — all out of scope and disallowed).
- The legitimate mechanisms are, in order of preference:
  1. Carrier converts/routes the number to an **enterprise SIP trunk** (same
     number, now trunk-delivered). Nothing in the app changes.
  2. Carrier **ports** the number to an enterprise-voice product that offers
     SIP termination. Nothing in the app changes.
  3. Only as a documented interim: carrier-level **call forwarding** from
     7382210251 to a trunk DID — with the caveat that CLI/diversion headers
     vary by carrier (test §10.3 explicitly). The user-facing helpline stays
     7382210251 (§32); any interim forwarding number is documented in
     `pbx.env` (`PBX_DID_LAST10` stays `7382210251`).

Owner action: confirm with the operator which of (1)-(3) applies to 7382210251
and complete §7. Until then the system is PARTIAL by §34 (ready, unconnected).

## 9. Security configuration

- **SIP**: firewall to carrier IPs (§4); fail2ban bans scanners after 3 bad
  auths (`pbx/security/`); strong trunk password (carrier-issued); no SIP
  phones/extensions exist to brute-force (trunk endpoint only).
- **Gateway HTTP**: shared 32-byte secret on every call, TLS-only backend URL
  (installer refuses plain http), optional IP allowlist, existing per-IP rate
  limiting, secret+HMAC verification that fails closed.
- **No open relay**: no outbound dialplan; `SIPProvider.initiate_outbound_call`
  returns `unsupported` (inbound-only in code); the AGI dials vet legs only
  after `POST /api/ivr/gateway/authorize-dial` approves the number against the
  call's *selected vet* (last-10 match on a `role='vet'` row). Toll fraud is
  impossible by construction. Every decision is audit-logged (`DIAL_AUTHORIZE`
  events, last-4 digits only).
- **SSRF guard**: the AGI follows TwiML action/Redirect URLs only on the
  backend host.
- **Recording**: master switch `PBX_RECORDING_ENABLED` AND per-call farmer
  consent (press 1 at the consent prompt). Without consent the PBX never
  records. Files stay on the PBX (`PBX_RECORDING_DIR`, 30-day retention cron);
  the backend never fabricates transcripts from them — the AI summary keeps
  working from the structured survey/consultation data exactly as with mock
  calls (§19/§20: no logic changed).
- **Failure safety** (§27): backend unreachable -> generic failure prompt in
  the caller's language + `call-ended` metadata preserved; vet no-answer ->
  status recorded, automatic fallback to the survey; per-call state machine
  unchanged.

## 10. Testing procedure (real mobile, §22-§26)

Prerequisites: §§3+7+8 done; `/api/ivr/health` shows `pbx:true,
sip_registered:true` (heartbeat flowing) and `pstn_connected:false` (honest).

1. **Call**: from a real mobile (record the network, e.g. Jio/Airtel/Vi),
   dial **7382210251**. Expect ring -> answer -> language menu.
2. **CLI**: check `ivr_calls.caller_number_normalized` equals the test mobile
   (or empty if the carrier withholds CLI — then verify the unknown-caller
   flow still completes).
3. **DTMF**: press 2 (Telugu) / 1 (vet) etc.; watch `pashu_ivr.log` for
   `gather collected digits=`.
4. **Vet bridge**: with a test vet AVAILABLE + consent press 1, verify the vet
   mobile rings, both legs hear each other, hangup from either side ends cleanly.
5. **Survey fallback**: vet OFFLINE (or no-answer) -> full survey via keypad ->
   report + case + vet notification + govt/GIS rows.
6. **Second network**: repeat 1-5 from a different operator; record both.
7. **Metrics** (record real values, do not pre-fill): PSTN->answer seconds,
   menu->routing-decision seconds, routing->vet-ring seconds, MOS/quality notes.
8. **Flip check**: after step 1, `/api/ivr/health` must show
   `pstn_connected:true` with `first_real_inbound_*` set — automatically, with
   no manual flag. Only then may the project status move PARTIAL -> PASS.

DTMF/audio/latency/hangup/session/report/case/notification/govt/GIS checks map
1:1 to §22 items 1-17; record each PASS/FAIL in the test log.

## 11. Troubleshooting

| Symptom | Check |
|---|---|
| `pbx:false` in health | heartbeat cron running? `tail /var/log/asterisk/pbx-heartbeat.log`; backend URL/secret match? |
| `sip_registered:false` (reg trunk) | `asterisk -rx 'pjsip show registrations'`; wrong `SIP_*` creds; carrier SBC IPs changed (update firewall + identify) |
| `sip_registered:false` (static trunk) | `pjsip show endpoint carrier-trunk`; set `PBX_TRUNK_STATIC=true` |
| Calls don't arrive | carrier routing not active; firewall (`ufw status`); `pjsip set logger on` to see INVITEs; DID format mismatch (see dialplan normalize) |
| No audio one-way | NAT: set `PBX_PUBLIC_IP`/`PBX_LOCALNETS`; RTP ports blocked; `strictrtp` vs carrier SBC (capture `rtp set debug on`) |
| DTMF ignored | carrier not sending RFC 4733 (`dtmfmode`); check `pashu_ivr.log` gather lines |
| Robotic/missing prompts | `espeak-ng` installed? `PBX_TTS_COMMAND` valid? cache dir writable? Upgrade path: pre-recorded prompts (below) |
| Vet leg fails | `authorize-dial` denied? (log shows reason); trunk outbound barred; wrong `PBX_VET_DIAL_FORMAT` (try e164); vet number not in DB |
| `pstn_connected` stuck false | no real call yet (correct!), or gateway secret mismatch (auth fails closed) |

**Prompt-quality upgrade path** (honest limitation): offline TTS is robotic,
especially for te/hi/mr. The supported upgrade is pre-recorded studio prompts:
drop `<sha>.wav` files into `$PBX_SOUNDS_DIR/<lang>/` (name = sha1 of
`"<lang>|<voice>|<text>"`, see `render_prompt`) — the gateway prefers cached
audio and never calls TTS for them. Swapping the TTS engine later means
changing only `PBX_TTS_COMMAND`.

## 12. Rollback procedure

1. `sudo systemctl stop asterisk` (PBX offline; web app + mock IVR unaffected).
2. Backend Render env: `TELEPHONY_PROVIDER=mock` (removes the `PBX_WEBHOOK_SECRET`
   requirement; gateway endpoints keep failing closed).
3. Carrier: suspend DID routing to the PBX IP (calls then follow the operator's
   normal no-route treatment — confirm with the carrier what the farmer hears).
4. Prior Asterisk configs are preserved in `/etc/asterisk/pashu.bak.*` by the
   installer; restore + `systemctl restart asterisk` to revert the host.
5. `pstn_connected` remains latched at its honest value (a real call did arrive
   once); heartbeats age out so `pbx/sip_registered` return to false.

## Final honesty note

Implemented + tested in this repo: SIP provider, gateway auth, heartbeat +
health signals, authorize-dial, AGI TwiML gateway, Asterisk configs, firewall,
fail2ban, installer, docs, 28 new tests (96 total green). **Not** done (cannot
be done from a repo): carrier contract for 7382210251, PBX VM provisioning,
and the real-mobile test of §10. The backend will report `pstn_connected:true`
by itself the moment the first authenticated real call arrives — and not a
moment sooner.
