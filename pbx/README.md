# Pashu-Shield voice gateway (self-hosted Asterisk PBX)

Real-PSTN termination layer for helpline **7382210251** — no Twilio/Exotel/SaaS.
Full architecture, carrier checklist, testing and troubleshooting:
**[docs/PSTN_SIP_PBX.md](../docs/PSTN_SIP_PBX.md)** (read it before installing).

```
farmer mobile -> Indian PSTN -> carrier SIP trunk -> this PBX (public VM)
   -> HTTPS -> Pashu-Shield backend (Render) -> existing IVR webhooks
   -> TwiML -> this PBX plays/collects/bridges -> farmer hears IVR, vet bridged
```

The PBX is a **pure transport**: it holds no IVR logic, no farmer data, no vet
data. It drives the *same* `/api/ivr/webhook/*` endpoints the mock transport
uses and executes the returned TwiML (`Say/Gather/Dial/Redirect/Hangup`).

## Layout

| Path | Purpose |
|---|---|
| `agi/pashu_ivr.py` | Main AGI: inbound call -> backend webhooks -> TwiML execution (stdlib only) |
| `agi/pashu_hangup.py` | Hangup backstop (`h` extension): always reports call end |
| `agi/pbx_heartbeat.py` | Cron heartbeat: Asterisk + trunk state -> backend |
| `asterisk/*.template` | pjsip/extensions/rtp/modules/manager/http/logger templates |
| `render.py` | `{{VAR}}` + `{{#IF}}` template renderer (stdlib only) |
| `pbx.env.example` | PBX-host config template (**never commit filled values**) |
| `install.sh` | Ubuntu 22.04/24.04 installer (idempotent, run as root) |
| `security/` | ufw firewall rules + fail2ban jail/filter |

## Install (operator machine with a public IP)

```bash
git clone <repo> && cd <repo>
sudo mkdir -p /etc/pashu-pbx
sudo cp pbx/pbx.env.example /etc/pashu-pbx/pbx.env
sudo chmod 600 /etc/pashu-pbx/pbx.env
sudo nano /etc/pashu-pbx/pbx.env     # backend URL, gateway secret, carrier trunk
sudo bash pbx/install.sh
```

Then on the **backend** (Render dashboard env):

```
TELEPHONY_PROVIDER=sip
PBX_WEBHOOK_SECRET=<same 32+ byte hex secret>
PBX_ALLOWED_IPS=<PBX egress IP>        # recommended
```

Then ask the **carrier** to route the helpline DID to the PBX IP (checklist in
docs/PSTN_SIP_PBX.md § Carrier). Until a real call arrives, the backend honestly
reports `pstn_connected=false`.

## Verify (after the carrier routes the DID)

1. From a real mobile, call **7382210251**.
2. On the PBX: `asterisk -rvvv` (watch the AGI), and
   `tail -f /var/log/asterisk/pashu_ivr.log`.
3. Backend: `GET https://<backend>/api/ivr/health` must show
   `pbx:true, sip_registered:true, pstn_connected:true`.
4. Exercise: language menu DTMF, farmer identification, vet bridge (two-way
   audio both legs), survey fallback, hangup; confirm report + case + govt/GIS.

## Security model (summary)

- Inbound SIP/RTP firewalled to carrier IPs; SSH rate-limited; fail2ban bans
  scanners; AMI/HTTP/ARI surfaces disabled; minimal Asterisk modules.
- Every gateway HTTP call carries the shared secret over TLS; gateway
  endpoints fail closed when the secret is unset.
- **Inbound only**: no outbound dialplan exists. Vet legs are dialled only
  after the backend authorizes the exact selected vet number for that call —
  toll fraud through this box is impossible by construction.
- Vet-leg recording requires the farmer's in-call consent (press 1) AND the
  master switch; recordings stay on the PBX with 30-day retention.

## Rollback

```bash
sudo systemctl stop asterisk            # PBX offline; web app unaffected
# backend Render env: TELEPHONY_PROVIDER=mock   # back to pre-PSTN state
```

`install.sh` backs up prior Asterisk configs to `/etc/asterisk/pashu.bak.*`.
Full rollback + troubleshooting: docs/PSTN_SIP_PBX.md.
