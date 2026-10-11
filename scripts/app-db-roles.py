#!/usr/bin/env python3
"""Own database logins for Keycloak, CIS/OIS and Orthanc, instead of their database's superuser (preflight note).

  KEYCLOAK_DB_APP_PASSWORD=... scripts/app-db-roles.py [--apply] [keycloak|integration|orthanc ...]

For each database (all three when none is named) it creates a LOGIN role that is NOT a superuser and cannot create
databases or roles (<db>_app, or *_DB_APP_USER), sets its password, and makes it the owner of the database, the
public schema and every table, sequence, view and function there, so the app keeps running its own migrations
(Keycloak, Liquibase in CIS/OIS, Orthanc's index). Idempotent. Then add the two printed lines to .env.local and
`docker compose up -d` the app: compose uses *_DB_APP_USER/_PASSWORD when set and the old login otherwise, so nothing
changes until you do. The superuser stays for operators (backups, restores).

Runs inside each database container as its superuser (POSTGRES_USER); role and password reach psql only on stdin,
never on a command line. Dry run (SQL printed, password masked) unless --apply.
"""
import os
import re
import subprocess
import sys

DBS = {
    "keycloak": {"container": "nidan-keycloak-db", "prefix": "KEYCLOAK", "default_role": "keycloak_app"},
    "integration": {"container": "nidan-integration-db", "prefix": "INTEGRATION", "default_role": "nidan_app"},
    "orthanc": {"container": "nidan-orthanc-db", "prefix": "ORTHANC", "default_role": "orthanc_app"},
}

# psql variables :role / :pw arrive as \set lines on stdin (never in any argv): the role is a checked identifier, the
# password a checked [A-Za-z0-9_.-] string, so neither can break out of its quotes; SQL quotes them again (%I, %L).
SQL = r"""
\set ON_ERROR_STOP on
SELECT format('CREATE ROLE %I LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT', :'role')
 WHERE NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = :'role') \gexec
SELECT format('ALTER ROLE %I LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE PASSWORD %L', :'role', :'pw') \gexec
SELECT format('ALTER DATABASE %I OWNER TO %I', current_database(), :'role') \gexec
SELECT format('ALTER SCHEMA public OWNER TO %I', :'role') \gexec
SELECT format('ALTER %s %I.%I OWNER TO %I',
              CASE c.relkind WHEN 'r' THEN 'TABLE' WHEN 'p' THEN 'TABLE' WHEN 'S' THEN 'SEQUENCE'
                             WHEN 'v' THEN 'VIEW' WHEN 'm' THEN 'MATERIALIZED VIEW' END,
              n.nspname, c.relname, :'role')
  FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
 WHERE n.nspname = 'public' AND c.relkind IN ('r', 'p', 'S', 'v', 'm')
   AND NOT (c.relkind = 'S' AND EXISTS (SELECT 1 FROM pg_depend d WHERE d.objid = c.oid AND d.deptype IN ('a', 'i')))
   \gexec
SELECT format('ALTER FUNCTION %s OWNER TO %I', p.oid::regprocedure, :'role')
  FROM pg_proc p JOIN pg_namespace n ON n.oid = p.pronamespace
 WHERE n.nspname = 'public' AND p.prokind = 'f' \gexec
SELECT rolname, rolsuper, rolcreatedb, rolcreaterole FROM pg_roles WHERE rolname = :'role';
"""


def role_ok(name):
    return name.replace("_", "").isalnum() and name[:1].isalpha() and len(name) <= 63


def plan(db, environ):
    cfg = DBS[db]
    role = environ.get(f"{cfg['prefix']}_DB_APP_USER") or cfg["default_role"]
    password = environ.get(f"{cfg['prefix']}_DB_APP_PASSWORD", "")
    problems = []
    if not role_ok(role):
        problems.append(f"{cfg['prefix']}_DB_APP_USER {role!r}: letters, digits and _ only, starting with a letter")
    if len(password) < 32 or not re.fullmatch(r"[A-Za-z0-9_.-]+", password):
        problems.append(f"{cfg['prefix']}_DB_APP_PASSWORD must be set: 32+ characters of A-Z a-z 0-9 _ . - "
                        "(generate one: openssl rand -hex 24)")
    return cfg, role, password, problems


def container(cfg, environ):
    return environ.get(f"{cfg['prefix']}_DB_CONTAINER") or cfg["container"]   # e.g. a test harness's name


def run(cfg, role, password, name):
    # role and password reach psql only on stdin (\set), never in an argv on the host or in the container
    r = subprocess.run(["docker", "exec", "-i", name, "sh", "-c",
                        'exec psql -X -q -U "$POSTGRES_USER" -d "$POSTGRES_DB" -f /dev/stdin'],
                       input=f"\\set role '{role}'\n\\set pw '{password}'\n{SQL}", text=True, capture_output=True)
    return r.returncode, (r.stdout + r.stderr).strip()


def main(argv, environ=os.environ, runner=run):
    args = [a for a in argv[1:] if a != "--apply"]
    apply = "--apply" in argv
    unknown = [a for a in args if a not in DBS]
    if unknown:
        print(__doc__)
        return 2
    rc = 0
    for db in args or list(DBS):
        cfg, role, password, problems = plan(db, environ)
        name = container(cfg, environ)
        print(f"== {db} ({name}): role {role}")
        if problems:
            for p in problems:
                print("  " + p)
            rc = 1
            continue
        if not apply:
            print(SQL.replace(":'pw'", "'********'").replace(":'role'", f"'{role}'"))
            continue
        code, out = runner(cfg, role, password, name)
        print("  " + out.replace("\n", "\n  "))
        if code != 0:
            rc = 1
            continue
        print(f"  done. Add to .env.local, then `docker compose up -d` the app:\n"
              f"    {cfg['prefix']}_DB_APP_USER={role}\n    {cfg['prefix']}_DB_APP_PASSWORD=<the value you used>")
    return rc


if __name__ == "__main__":
    sys.exit(main(sys.argv))
