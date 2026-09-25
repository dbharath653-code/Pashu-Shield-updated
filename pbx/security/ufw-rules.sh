#!/usr/bin/env bash
# Pashu-Shield voice gateway firewall (ufw). Called by install.sh; reads the
# same pbx.env (PBX_ENV_FILE). Inbound SIP/RTP allowed ONLY from the carrier's
# signalling IPs. Re-run after changing CARRIER_SIP_IPS / RTP range.
set -euo pipefail
ENV_FILE="${PBX_ENV_FILE:-/etc/pashu-pbx/pbx.env}"
set -a; . "$ENV_FILE"; set +a
: "${SIP_TRUNK_PORT:=5060}"
: "${RTP_PORT_START:=10000}"
: "${RTP_PORT_END:=10100}"

if ! command -v ufw >/dev/null; then echo "ufw not installed" >&2; exit 1; fi

ufw --force reset >/dev/null
ufw default deny incoming
ufw default allow outgoing
# SSH: rate-limited (brute-force hardening). Change 22 if you use another port.
ufw limit 22/tcp comment "ssh rate-limited"

# Carrier signalling IPs (comma-separated in pbx.env, e.g. 203.0.113.10,198.51.100.0/24)
echo "$CARRIER_SIP_IPS" | tr ',' '\n' | sed 's/^ *//;s/ *$//' | grep -v '^$' | while read -r ip; do
  ufw allow from "$ip" to any port "$SIP_TRUNK_PORT" proto udp comment "pashu carrier sip"
  ufw allow from "$ip" to any port "$SIP_TRUNK_PORT" proto tcp comment "pashu carrier sip-tcp"
  if [ -n "${PBX_TLS_CERT:-}" ]; then
    ufw allow from "$ip" to any port 5061 proto tcp comment "pashu carrier sip-tls"
  fi
  ufw allow from "$ip" to any port "${RTP_PORT_START}:${RTP_PORT_END}" proto udp comment "pashu carrier rtp"
done

ufw --force enable
ufw status numbered
echo "firewall active: SIP/RTP reachable only from CARRIER_SIP_IPS; all else denied."
