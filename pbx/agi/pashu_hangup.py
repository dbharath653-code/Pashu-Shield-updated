#!/usr/bin/env python3
"""
Hangup backstop for the Pashu-Shield voice gateway.

Runs from the dialplan `h` extension AFTER pashu_ivr.py exits, guaranteeing the
backend always learns the call ended (even if the main AGI was killed).
Stdlib only. Best-effort: never fails the hangup path.

  extensions.conf:  exten => h,1,AGI(pashu_hangup.py,${PASHU_CALL_SID})

Env: PASHU_BACKEND_URL, PBX_WEBHOOK_SECRET (same file as the main AGI).
"""
import json
import os
import sys
import urllib.request


def main(argv):
    call_sid = (argv[1] if len(argv) > 1 else "").strip()
    # Drain AGI env (required by the protocol) to find the channel uniqueid.
    unique_id = ""
    try:
        for line in sys.stdin:
            line = line.strip()
            if line == "":
                break
            if line.startswith("agi_uniqueid:"):
                unique_id = line.split(":", 1)[1].strip()
    except Exception:
        pass
    if not call_sid and unique_id:
        call_sid = "SIP-%s" % unique_id
    base = os.environ.get("PASHU_BACKEND_URL", "").rstrip("/")
    secret = os.environ.get("PBX_WEBHOOK_SECRET", "")
    if not call_sid or not base or not secret:
        return 0
    body = json.dumps({"call_sid": call_sid, "duration": 0,
                       "reason": "pbx_hangup_backstop"}).encode()
    req = urllib.request.Request(
        base + "/api/ivr/webhook/call-ended", data=body,
        headers={"Content-Type": "application/json", "X-PBX-Secret": secret},
        method="POST")
    try:
        with urllib.request.urlopen(req, timeout=10):
            pass
    except Exception as e:
        print("pashu_hangup: best-effort post failed: %s" % e, file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
