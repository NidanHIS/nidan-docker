#!/usr/bin/env python3
"""Ship Keycloak's login and admin events out of its database (backlog KC-23-05, KC-28-07).

  KC_AUTOMATION_CLIENT_SECRET=... scripts/keycloak-event-shipper.py OUT_DIR --client nidan-incident [--url ...]
  KC_TOKEN_FILE=/secure/token     scripts/keycloak-event-shipper.py OUT_DIR [--url ...]

Every run appends the events newer than the last run to OUT_DIR/login-events-YYYY-MM.jsonl and
OUT_DIR/admin-events-YYYY-MM.jsonl (one JSON object per line, oldest first), so deleting events in Keycloak (or
restoring an older Keycloak database) does not delete this copy. OUT_DIR is created 0700, files 0600; keep it on
other storage than the Keycloak volume and include it in the off-host backup. Run it every few minutes from cron
(docs/keycloak-events.cron). scripts/keycloak-reapply-after-restore.py reads the admin events after a restore.
"""
import hashlib
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

KINDS = {"login": "/events", "admin": "/admin-events"}
PAGE = 200


def token_from(base, client):
    if client:
        secret = os.environ.get("KC_AUTOMATION_CLIENT_SECRET", "")
        if not secret:
            raise SystemExit("set KC_AUTOMATION_CLIENT_SECRET (from .env.local)")
        req = urllib.request.Request(base.rstrip("/") + "/realms/master/protocol/openid-connect/token", data=urllib.parse.urlencode(
            {"grant_type": "client_credentials", "client_id": client, "client_secret": secret}).encode())
        with urllib.request.urlopen(req, timeout=15) as resp:
            return json.loads(resp.read().decode())["access_token"]
    tf = os.environ.get("KC_TOKEN_FILE", "")
    if not tf or not Path(tf).is_file():
        raise SystemExit("set KC_TOKEN_FILE, or use --client")
    return Path(tf).read_text().strip()


def fetch_newer(get, path, since_ms, seen):
    """Events with time >= since_ms not already shipped (Keycloak lists newest first), returned oldest first."""
    out, first = [], 0
    while True:
        page = get(f"{path}?first={first}&max={PAGE}")
        for e in page:
            if e.get("time", 0) < since_ms:
                return list(reversed(out))
            key = hashlib.sha256(json.dumps(e, sort_keys=True).encode()).hexdigest()
            if key not in seen:
                out.append(e)
        if len(page) < PAGE:
            return list(reversed(out))
        first += PAGE


def ship(get, out_dir, now_ms=None):
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    os.chmod(out_dir, 0o700)
    cursor_file = out_dir / ".cursor.json"
    cursor = json.loads(cursor_file.read_text()) if cursor_file.exists() else {}
    shipped = {}
    for kind, path in KINDS.items():
        c = cursor.get(kind, {"time": 0, "seen": []})
        events = fetch_newer(get, path, c["time"], set(c["seen"]))
        for e in events:
            month = time.strftime("%Y-%m", time.gmtime(e.get("time", 0) / 1000))
            f = out_dir / f"{kind}-events-{month}.jsonl"
            fd = os.open(f, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o600)
            with os.fdopen(fd, "a", encoding="utf-8") as fh:
                fh.write(json.dumps(e, sort_keys=True) + "\n")
        if events:
            last = max(e.get("time", 0) for e in events)
            same = [hashlib.sha256(json.dumps(e, sort_keys=True).encode()).hexdigest() for e in events if e.get("time") == last]
            c = {"time": last, "seen": same + (c["seen"] if last == c["time"] else [])}
        cursor[kind] = c
        shipped[kind] = len(events)
    tmp = cursor_file.with_suffix(".tmp")
    tmp.write_text(json.dumps(cursor))
    os.chmod(tmp, 0o600)
    tmp.replace(cursor_file)
    return shipped


def main(argv):
    args, base, client = list(argv[1:]), "http://127.0.0.1:8180/auth", None
    for flag in ("--url", "--client"):
        if flag in args:
            i = args.index(flag)
            val = args[i + 1] if i + 1 < len(args) else ""
            del args[i:i + 2]
            base, client = (val, client) if flag == "--url" else (base, val)
    if len(args) != 1 or not base or client == "":
        print(__doc__)
        return 2
    token = token_from(base, client)

    def get(path):
        req = urllib.request.Request(base.rstrip("/") + "/admin/realms/nidan" + path,
                                     headers={"Authorization": f"Bearer {token}", "Accept": "application/json"})
        with urllib.request.urlopen(req, timeout=30) as resp:
            return json.loads(resp.read().decode() or "[]")

    try:
        shipped = ship(get, args[0])
    except urllib.error.HTTPError as e:
        print(f"Keycloak answered {e.code}", file=sys.stderr)
        return 1
    print(f"shipped {shipped['login']} login and {shipped['admin']} admin event(s) to {args[0]}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
