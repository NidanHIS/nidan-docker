#!/usr/bin/env python3
"""Refuse a production start on demo or weak secrets (backlog KC-05-03).

Run with the same env files compose will use, before `up`:
    COMPOSE_ENV_FILES=.env,.env.local NIDAN_DEPLOYMENT=production scripts/preflight.py && \
    COMPOSE_ENV_FILES=.env,.env.local docker compose up -d

Reads the effective config (`docker compose config --format json`, all profiles; no daemon needed)
and checks every service environment variable whose name says it is a secret. In production
(NIDAN_DEPLOYMENT=production in the shell or the env files) any of these fails the run:
  * the value equals the value in the tracked demo .env or env.template
  * a placeholder or well-known default (CHANGEME..., change-me, sample, admin, ...)
  * shorter than 16 characters
  * Superset or the DHIS2 app reads the clinical databases with an app's own account instead of the
    read-only nidan_reporting login (KC-48)
  * a DB app password equals that DB's root password, or a non-DB service holds a DB superuser password
    of another role (KC-48-02); an app that IS its own DB's superuser is reported only
  * Orthanc accepts DICOM stores from any calling system, or no modality is registered (KC-31)
  * any service but keycloak / keycloak-db holds Keycloak's database password (KC-07-10)
  * (sso profile) KC_USERNAME_PATTERN accepts a reserved username (KC-55-04: regenerate it with
    scripts/reserved-usernames.py --site-file <workbook>/reserved-usernames.txt)
Outside production it only reports. Exit 0 = clean (or not production), 1 = refuse to start.
Values are never printed.
"""
import json
import os
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SECRET_NAME = re.compile(r"(PASSWORD|SECRET|_KEY|TOKEN|_PW)$|^DEFAULT_PW$")
NOT_SECRET = re.compile(r"BYPASS|_PATH$|_FILE$|_ENABLED$|_USER(NAME)?$")
KNOWN_WEAK = {"change-me", "changeme", "sample", "admin", "admin123", "password", "secret", "nidan",
              "nidan-secret-password", "your-secret-key-here", "pass", "openmrs", "clinlims", "orthanc"}
MIN_LEN = 16
# Shared service secrets: the services refuse to start in production below this (KC-51-04)
SHARED_MIN_LEN = 32
SHARED_SECRETS = {"NIDAN_OIS_SECRET", "NIDAN_ODOO_SECRET", "OIS_WEBHOOK_SECRET", "OPENELIS_CATALOG_WEBHOOK_SECRET",
                  "ORTHANC_WEBHOOK_SECRET", "NIDAN_VISIT_SYNC_ADMIN_KEY", "DEAD_LETTER_ADMIN_PASSWORD",
                  "NIDANCORE_ODOO_PAYMENT_SECRET", "ORG_OPENELISGLOBAL_NIDAN_PAYWALL_ODOO_SECRET"}
# Optional secrets: empty means "feature off", which the consuming service handles fail-closed.
OPTIONAL_EMPTY = {"SUPERSET_GUEST_TOKEN_SECRET", "PROVINCIAL_DHIS2_PASSWORD", "DEBEZIUM_DB_PASSWORD",
                  "NIDAN_SYNC_PASSWORD", "OPENELIS_CATALOG_WEBHOOK_SECRET", "ORTHANC_WEBHOOK_SECRET",
                  "NIDAN_SMTP_PASSWORD", "NIDAN_SMS_TOKEN"}   # email / SMS sign-in codes: off when unset


def tracked_values():
    vals = set()
    for name in (".env", "env.template"):
        f = ROOT / name
        if f.is_file():
            for line in f.read_text(encoding="utf-8").splitlines():
                if "=" in line and not line.lstrip().startswith("#"):
                    v = line.split("=", 1)[1].strip()
                    if v:
                        vals.add(v)
    return vals


def env_items(service):
    env = service.get("environment") or {}
    if isinstance(env, list):
        env = dict(e.split("=", 1) if "=" in e else (e, "") for e in env)
    return env.items()


def db_superuser_findings(services):
    """KC-48-02. (problems, notes): an app password equal to the DB root password, or a non-DB service holding
    the superuser password of a role other than its own DB user, is a problem; an app whose own DB user is
    that DB's superuser (dedicated DB containers such as orthanc-db) is a note."""
    envs = {n: {k: ("" if v is None else str(v)) for k, v in env_items(s)} for n, s in services.items()}
    superusers = {}  # password -> (db service, superuser name)
    problems, notes = [], []
    for name, e in envs.items():
        if e.get("MYSQL_ROOT_PASSWORD"):
            superusers[e["MYSQL_ROOT_PASSWORD"]] = (name, "root")
            if e.get("MYSQL_PASSWORD") and e["MYSQL_PASSWORD"] == e["MYSQL_ROOT_PASSWORD"]:
                problems.append(f"{name}: app user {e.get('MYSQL_USER')!r} has the same password as root")
        if name.endswith("-db") and e.get("POSTGRES_PASSWORD"):
            superusers[e["POSTGRES_PASSWORD"]] = (name, e.get("POSTGRES_USER", "postgres"))
    for name, e in envs.items():
        if name in {db for db, _ in superusers.values()}:
            continue
        users = {v for k, v in e.items() if k.endswith(("_USER", "USERNAME", "_DB_USER")) or k in ("USER", "POSTGRES_USER")}
        for key, value in e.items():
            if not value or value not in superusers or not re.search(r"PASSWORD|PASSWD|SECRET", key):
                continue
            db, su = superusers[value]
            if su in users and key != "POSTGRES_PASSWORD":
                notes.append(f"{name} runs as {su!r}, the superuser of its own {db} (own login: scripts/app-db-roles.py)")
            else:
                problems.append(f"{name}: {key} is the superuser password of {db} ({su!r}); the app should not hold it")
    return problems, sorted(set(notes))


def reporting_problems(services):
    """KC-48: reporting consumers read through the read-only nidan_reporting login only."""
    out = []
    for name in ("nidan-dhis2", "superset"):
        env = dict(env_items(services.get(name, {}))) if name in services else {}
        for key, value in env.items():
            user = None
            if key in ("OPENMRS_DB_URL", "OPENELIS_DB_URL", "ODOO_DB_URL"):
                m = re.match(r"^[\w+]+://([^:@/]+)", str(value or ""))
                user = m.group(1) if m else ""
            elif key in ("OPENMRS_DB_USER", "OPENELIS_DB_USER", "ODOO_DB_USER"):
                user = str(value or "")
            if user is not None and user != "nidan_reporting":
                out.append(f"{name}: {key} reads as {user!r}, not the read-only nidan_reporting login")
    return out


def dicom_problems(services):
    """KC-31: production Orthanc only takes DICOM from registered modalities."""
    env = dict(env_items(services.get("orthanc", {}))) if "orthanc" in services else None
    if env is None:
        return []
    out = []
    if str(env.get("ORTHANC__DICOM_ALWAYS_ALLOW_STORE", "true")).strip().lower() != "false":
        out.append("orthanc: any calling system may store DICOM (set ORTHANC_DICOM_ALLOW_ANY_STORE=false)")
    try:
        modalities = json.loads(env.get("ORTHANC__DICOM_MODALITIES") or "{}")
    except ValueError:
        modalities = None
    if not isinstance(modalities, dict) or not modalities:
        out.append("orthanc: ORTHANC_DICOM_MODALITIES is empty or not a JSON object (no registered modality)")
    else:
        for name, m in modalities.items():
            if not isinstance(m, dict) or not m.get("AET") or not m.get("Host"):
                out.append(f"orthanc: modality {name} needs AET and Host")
    return out


def keycloak_db_isolation_problems(services):
    """KC-07-10: Keycloak's DB password belongs to keycloak + keycloak-db only. If any other service holds the same
    value (any variable), one leaked app secret would also open Keycloak's database, or the reverse."""
    kc = dict(env_items(services.get("keycloak-db") or {})).get("POSTGRES_PASSWORD")
    if not kc:
        return []
    return [f"{name}: {key} equals Keycloak's database password; use a value of its own (KC-07-10)"
            for name, svc in sorted(services.items()) if name not in ("keycloak", "keycloak-db")
            for key, value in env_items(svc) if value is not None and str(value) == str(kc)]


def username_pattern_problems(services):
    """KC-55-04: the pattern handed to keycloak-config-cli must refuse every reserved username."""
    import importlib.util
    pats = [(n, dict(env_items(s)).get("KC_USERNAME_PATTERN")) for n, s in services.items()
            if "KC_USERNAME_PATTERN" in dict(env_items(s))]
    if not pats:
        return []
    spec = importlib.util.spec_from_file_location("ru", ROOT / "scripts" / "reserved-usernames.py")
    ru = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(ru)
    files = [str(ROOT / f) for f in os.environ.get("COMPOSE_ENV_FILES", ".env,.env.local").split(",") if f]
    reserved = ru.reserved(files)
    out = []
    for name, pat in pats:
        if not pat:
            out.append(f"{name}: KC_USERNAME_PATTERN is empty (reserved usernames not enforced)")
            continue
        try:
            leaks = sorted(r for r in reserved if re.fullmatch(pat, r))
        except re.error as e:
            out.append(f"{name}: KC_USERNAME_PATTERN does not compile ({e})")
            continue
        if leaks:
            out.append(f"{name}: KC_USERNAME_PATTERN accepts reserved usernames: {', '.join(leaks[:5])}")
    return out


def code_otp_problems(services, dist=ROOT / "keycloak" / "providers" / "dist"):
    """The realm's login flow uses the nidan-code-otp extension: without its jar the realm import fails."""
    if "keycloak" not in services or any(dist.glob("nidan-code-otp*.jar")):
        return []
    return ["keycloak: keycloak/providers/dist/nidan-code-otp.jar is missing; build it (keycloak/providers/README.md)"]


def main():
    out = subprocess.run(["docker", "compose", "--profile", "*", "config", "--format", "json"],
                         cwd=ROOT, capture_output=True, text=True)
    if out.returncode != 0:
        print(out.stderr.strip(), file=sys.stderr)
        return 1
    config = json.loads(out.stdout)
    services = config.get("services", {})
    production = os.environ.get("NIDAN_DEPLOYMENT", "").lower() == "production" or any(
        str(dict(env_items(s)).get("NIDAN_DEPLOYMENT", "")).lower() == "production" for s in services.values())
    demo = tracked_values()
    problems = []
    for name, svc in sorted(services.items()):
        for key, value in env_items(svc):
            if not SECRET_NAME.search(key) or NOT_SECRET.search(key):
                continue
            value = "" if value is None else str(value)
            if not value and key in OPTIONAL_EMPTY:
                continue
            if not value and name == "odoo" and key == "POSTGRES_PASSWORD":
                continue        # ODOO_APP_POSTGRES_PASSWORD= : Odoo without the DB superuser (KC-48-02); odoo-db keeps its own
            why = ("empty" if not value
                   else "placeholder" if value.upper().startswith("CHANGEME")
                   else "well-known default" if value.lower() in KNOWN_WEAK
                   else "public demo value (tracked .env / env.template)" if value in demo
                   else f"shorter than {MIN_LEN} chars" if len(value) < MIN_LEN
                   else f"shorter than {SHARED_MIN_LEN} chars (the service will not start)"
                   if key in SHARED_SECRETS and len(value) < SHARED_MIN_LEN
                   else None)
            if why:
                problems.append(f"{name}: {key} is {why}")
    problems += username_pattern_problems(services)
    problems += keycloak_db_isolation_problems(services)
    problems += dicom_problems(services)
    cli = dict(env_items(services.get("keycloak-config-cli", {})))
    if cli and cli.get("DEFAULT_LOCALE") not in ("en", "ne", "es", "fr", "ar"):  # supportedLocales in the realm file
        problems.append(f"keycloak-config-cli: DEFAULT_LOCALE {cli.get('DEFAULT_LOCALE')!r} is not en, ne, es, fr or ar")
    problems += reporting_problems(services)
    problems += code_otp_problems(services)
    su_problems, su_notes = db_superuser_findings(services)
    problems += su_problems
    mode = "production" if production else "demo"
    for note in su_notes:
        print("  note: " + note)
    print(f"preflight ({mode}): {len(problems)} weak secret setting(s)")
    for p in problems:
        print("  " + p)
    if production and problems:
        print("refusing: generate per-site values with scripts/generate-secrets.sh --fresh-install", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
