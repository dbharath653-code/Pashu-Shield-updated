#!/usr/bin/env python3
"""
PBX heartbeat: proves the voice gateway is alive and the SIP trunk is up.

Runs every minute from cron on the PBX host. Checks:
  1. Asterisk responds at all  (`asterisk -rx 'core show version'`)
  2. SIP trunk state (`asterisk -rx 'pjsip show registrations'` contains
     "Registered", OR static IP-auth trunk configured -> see below)

and POSTs the result to the backend's secret-authenticated heartbeat endpoint.
The backend derives pbx_healthy / sip_registered from these heartbeats.

Static IP-auth trunks (carrier sends calls to our IP, no registration):
  set PBX_TRUNK_STATIC=true and the script verifies the endpoint exists
  (`pjsip show endpoint <trunk>`) instead of a registration.

Env: PASHU_BACKEND_URL, PBX_WEBHOOK_SECRET, PBX_TRUNK_ENDPOINT,
     PBX_HEARTBEAT_TRUNK (label for health output), PBX_TRUNK_STATIC.
Stdlib only.
"""
import json
import os
import socket
import subprocess
import sys
import urllib.request


def _rx(cmd):
    try:
        proc = subprocess.run(["asterisk", "-rx", cmd], timeout=15,
                              stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                              text=True)
        return proc.returncode, proc.stdout or ""
    except Exception as e:
        return 1, "error: %s" % e


def main():
    base = os.environ.get("PASHU_BACKEND_URL", "").rstrip("/")
    secret = os.environ.get("PBX_WEBHOOK_SECRET", "")
    trunk = os.environ.get("PBX_TRUNK_ENDPOINT", "carrier-trunk")
    trunk_label = os.environ.get("PBX_HEARTBEAT_TRUNK", trunk)
    static_trunk = os.environ.get("PBX_TRUNK_STATIC", "false").lower() in ("1", "true", "yes")
    if not base or not secret:
        print("pbx_heartbeat: PASHU_BACKEND_URL and PBX_WEBHOOK_SECRET required",
              file=sys.stderr)
        return 2
    rc, version_out = _rx("core show version")
    asterisk_version = ""
    if rc == 0:
        for line in version_out.splitlines():
            if "Asterisk" in line:
                asterisk_version = line.strip()[:64]
                break
    sip_registered = False
    if static_trunk:
        rc2, ep_out = _rx("pjsip show endpoint %s" % trunk)
        # Endpoint exists and is not shot down -> trunk path usable.
        sip_registered = (rc2 == 0 and "Not found" not in ep_out
                          and ("Context" in ep_out or "Endpoint" in ep_out))
    else:
        _rc2, reg_out = _rx("pjsip show registrations")
        sip_registered = "Registered" in reg_out
    payload = {"pbx_host": socket.gethostname()[:128],
               "sip_registered": bool(sip_registered and rc == 0),
               "trunk": trunk_label[:128],
               "asterisk_version": asterisk_version}
    req = urllib.request.Request(
        base + "/api/ivr/gateway/heartbeat", data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json", "X-PBX-Secret": secret},
        method="POST")
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            print("pbx_heartbeat: http=%s registered=%s" % (resp.status, payload["sip_registered"]))
            return 0
    except Exception as e:
        print("pbx_heartbeat: FAILED: %s" % e, file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv))
