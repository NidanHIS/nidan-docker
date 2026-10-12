#!/usr/bin/env python3
"""Incident response for central login (backlog KC-50). Dry run by default: prints every step; --execute acts.

  scripts/incident.py revoke-all            [--execute]   everyone must sign in again within ~2 minutes
  scripts/incident.py rotate-realm-keys     [--execute] [--retire-old]
  scripts/incident.py rotate-client-secret CLIENT [--execute]

revoke-all            new oauth2-proxy cookie secret (old gateway cookies can no longer be decrypted), realm
                      not-before = now + push, log out every Keycloak session (Odoo also ends its sessions via
                      back-channel logout), restart oauth2-proxy.
rotate-realm-keys     add a new RS256 key with a higher priority (new tokens use it at once). Later, once the old
                      tokens have expired (>= 12 h), run again with --retire-old to disable every older RSA key:
                      tokens signed by them are then refused everywhere.
rotate-client-secret  new secret for one Keycloak client, written to Keycloak and .env.local, consumers restarted.

Keycloak is driven with kcadm.sh inside the keycloak container, as the automation client
KC_AUTOMATION_CLIENT_ID / KC_AUTOMATION_CLIENT_SECRET (master realm, client credentials: an operator with OTP
cannot be scripted). Secrets reach the container as environment variables (`docker compose exec -e NAME`),
never on a command line. .env.local is backed up (0600) before any change.
"""
import os
import secrets
import shutil
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
# every command acts on the central-login stack (unless the site already chose its files in COMPOSE_FILE)
os.environ.setdefault("COMPOSE_FILE", "docker-compose.yml:keycloak.docker-compose.yml")
ENV_FILE = ROOT / ".env.local"
KCADM = "/opt/keycloak/bin/kcadm.sh"
KCCFG = "--config /tmp/kcadm-incident.config"
LOGIN = (f"{KCADM} config credentials {KCCFG} --server http://localhost:8080/auth --realm master "
         f'--client "$KC_AUTOMATION_CLIENT_ID" --secret "$KC_AUTOMATION_CLIENT_SECRET" >/dev/null')
# Keycloak client -> (.env.local key, compose services to restart)
CLIENTS = {
    "nidan-gateway": ("KC_GATEWAY_CLIENT_SECRET", ["oauth2-proxy"]),
    "openmrs": ("KC_OPENMRS_CLIENT_SECRET", ["openmrs-backend"]),
    "odoo": ("KC_ODOO_CLIENT_SECRET", ["odoo"]),
    "openelis": ("KC_OPENELIS_CLIENT_SECRET", ["openelis"]),
    "superset": ("KC_SUPERSET_CLIENT_SECRET", ["superset"]),
    "dhis2": ("KC_DHIS2_CLIENT_SECRET", ["nidan-dhis2"]),
    "cis": ("KC_CIS_CLIENT_SECRET", ["nidan-cis"]),
    "odoo-sync": ("KC_ODOO_SYNC_CLIENT_SECRET", ["odoo"]),
    "portal-sync": ("KC_PORTAL_SYNC_CLIENT_SECRET", []),  # the portal stack runs elsewhere: restart it by hand
    "role-sync": ("KC_ROLE_SYNC_CLIENT_SECRET", ["keycloak-role-sync"]),
}


def new_secret(length=40):
    alphabet = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789"
    return "".join(secrets.choice(alphabet) for _ in range(length))


def kc(script, env_names=()):
    """docker compose exec of a kcadm script; secrets only as named env vars taken from our environment."""
    argv = ["docker", "compose", "exec", "-T"]
    for name in ("KC_AUTOMATION_CLIENT_ID", "KC_AUTOMATION_CLIENT_SECRET", *env_names):
        argv += ["-e", name]
    return argv + ["keycloak", "sh", "-c", f"{LOGIN} && {script}"]


def set_env_key(key, value, env_file=ENV_FILE):
    """Replace KEY=... in .env.local (append if missing) after a timestamped 0600 backup."""
    text = env_file.read_text(encoding="utf-8") if env_file.exists() else ""
    backup = env_file.with_name(f"{env_file.name}.bak-{time.strftime('%Y%m%d-%H%M%S')}")
    if env_file.exists():
        shutil.copy2(env_file, backup)
        os.chmod(backup, 0o600)
    lines, done = text.splitlines(), False
    for i, line in enumerate(lines):
        if line.startswith(key + "="):
            lines[i], done = f"{key}={value}", True
    if not done:
        lines.append(f"{key}={value}")
    env_file.write_text("\n".join(lines) + "\n", encoding="utf-8")
    os.chmod(env_file, 0o600)
    return backup


def plan(command, args):
    """[(description, argv or callable, extra env)] — the exact steps, so a dry run shows what would happen."""
    steps = []
    if command == "revoke-all":
        cookie = new_secret(32)
        steps.append(("new oauth2-proxy cookie secret in .env.local", ("setenv", "OAUTH2_PROXY_COOKIE_SECRET", cookie), {}))
        steps.append(("realm not-before = now, pushed to clients",
                      kc(f'{KCADM} update realms/nidan {KCCFG} -s notBefore=$(date +%s) && '
                         f'{KCADM} create realms/nidan/push-revocation {KCCFG}'), {}))
        steps.append(("log out every Keycloak session", kc(f"{KCADM} create realms/nidan/logout-all {KCCFG}"), {}))
        steps.append(("restart oauth2-proxy with the new cookie secret",
                      ["docker", "compose", "up", "-d", "--force-recreate", "oauth2-proxy"], {}))
    elif command == "rotate-realm-keys":
        if "--retire-old" in args:
            # Only signing keys: Keycloak's default "rsa-generated" and ours "rsa-<14 digits>". Never the
            # encryption key "rsa-enc-generated" or the HMAC key. Keep the newest rotated key.
            steps.append(("disable every older RSA signing key (old tokens are refused from now on)",
                          kc(f"list=$({KCADM} get components -r nidan {KCCFG} -q type=org.keycloak.keys.KeyProvider "
                             "--fields id,name --format csv --noquotes | grep -E ',rsa-(generated|[0-9]{14})$'); "
                             "newest=$(echo \"$list\" | grep -E ',rsa-[0-9]{14}$' | sort -t, -k2 | tail -n 1 | cut -d, -f1); "
                             "[ -n \"$newest\" ] || { echo 'no rotated key yet: run rotate-realm-keys first' >&2; exit 1; }; "
                             "for id in $(echo \"$list\" | cut -d, -f1); do [ \"$id\" = \"$newest\" ] || "
                             f"{KCADM} update components/$id -r nidan {KCCFG} -s 'config.enabled=[\"false\"]' || exit 1; done"), {}))
        else:
            name = "rsa-" + time.strftime("%Y%m%d%H%M%S")
            steps.append((f"add RS256 key {name} with priority {int(time.time())} (newest wins)",
                          kc(f"rid=$({KCADM} get realms/nidan {KCCFG} --fields id --format csv --noquotes); "
                             f"{KCADM} create components -r nidan {KCCFG} -s name={name} -s providerId=rsa-generated "
                             "-s providerType=org.keycloak.keys.KeyProvider -s parentId=$rid "
                             f"-s 'config.priority=[\"{int(time.time())}\"]' -s 'config.keySize=[\"2048\"]' "
                             "-s 'config.algorithm=[\"RS256\"]'"), {}))
    elif command == "rotate-client-secret":
        client = args[0] if args else ""
        if client not in CLIENTS:
            raise SystemExit(f"unknown client {client!r}; one of: {', '.join(CLIENTS)}")
        key, services = CLIENTS[client]
        value = new_secret()
        steps.append((f"set a new secret on Keycloak client {client}",
                      kc(f"cid=$({KCADM} get clients -r nidan {KCCFG} -q clientId={client} --fields id --format csv "
                         f'--noquotes); {KCADM} update clients/$cid -r nidan {KCCFG} -s "secret=$NEW_CLIENT_SECRET"',
                         env_names=("NEW_CLIENT_SECRET",)), {"NEW_CLIENT_SECRET": value}))
        steps.append((f"write {key} to .env.local", ("setenv", key, value), {}))
        if services:
            steps.append((f"restart {', '.join(services)}", ["docker", "compose", "up", "-d", "--force-recreate", *services], {}))
        else:
            steps.append((f"restart the portal stack by hand with the new {key}", None, {}))
    else:
        raise SystemExit(__doc__)
    return steps


def main(argv):
    os.environ.setdefault("KC_AUTOMATION_CLIENT_ID", "nidan-incident")  # keycloak/realm/master/automation.yaml
    if len(argv) < 2:
        print(__doc__)
        return 2
    command, rest = argv[1], [a for a in argv[2:] if a != "--execute"]
    execute = "--execute" in argv
    steps = plan(command, rest)
    if execute and not (os.environ.get("KC_AUTOMATION_CLIENT_ID") and os.environ.get("KC_AUTOMATION_CLIENT_SECRET")):
        print("set KC_AUTOMATION_CLIENT_ID and KC_AUTOMATION_CLIENT_SECRET (master-realm automation client)", file=sys.stderr)
        return 2
    for n, (what, step, extra) in enumerate(steps, 1):
        print(f"{n}. {what}")
        if not execute or step is None:
            if isinstance(step, list):
                print("     " + " ".join(step[:-1] if step[-2:-1] == ["-c"] else step))
                if step[-2:-1] == ["-c"]:
                    print("       " + step[-1])  # the kcadm script: only $VAR references, no secret values
            continue
        if isinstance(step, tuple) and step[0] == "setenv":
            print(f"     backup: {set_env_key(step[1], step[2])}")
            continue
        res = subprocess.run(step, cwd=ROOT, env=dict(os.environ, **extra), capture_output=True, text=True)
        if res.returncode != 0:
            print(f"     FAILED: {(res.stderr or res.stdout).strip()[:400]}", file=sys.stderr)
            return 1
    if not execute:
        print("dry run: nothing changed. Re-run with --execute.")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
