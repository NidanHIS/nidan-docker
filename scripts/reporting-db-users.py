#!/usr/bin/env python3
"""Read-only reporting users for Superset and the DHIS2 app (backlog KC-48).

  REPORTING_DB_PASSWORD=... scripts/reporting-db-users.py [--apply] [openmrs|openelis|odoo ...]

Creates/updates `nidan_reporting` in each clinical database: SELECT on every table EXCEPT credential stores.
Tables holding credentials get column-level SELECT without the secret columns; tables that are secrets as a
whole get nothing. The login is read-only at the database level (PostgreSQL default_transaction_read_only;
MariaDB grants SELECT only). Idempotent: re-run after app upgrades (MariaDB has no default privileges, so
new tables are only granted by a re-run; PostgreSQL covers them through ALTER DEFAULT PRIVILEGES).

The password travels on stdin to the database container (never on a command line). Dry run (prints the SQL
with the password masked) unless --apply. Run as the DB superuser inside each container (MariaDB root via
MYSQL_ROOT_PASSWORD, PostgreSQL POSTGRES_USER), so nothing new is needed on the host.
"""
import os
import re
import subprocess
import sys

ROLE = "nidan_reporting"
# db -> engine, container, database, schema, {table: secret columns or None = whole table off-limits}
DBS = {
    "openmrs": {"engine": "mysql", "container": "nidan-openmrs-db", "db": "openmrs", "schema": "openmrs",
                "secret": {"users": ["password", "salt", "secret_question", "secret_answer"],
                           "user_property": None, "global_property": None}},
    "openelis": {"engine": "pg", "container": "nidan-openelis-db", "db": "clinlims", "schema": "clinlims",
                 "secret": {"login_user": ["password"]}},
    "odoo": {"engine": "pg", "container": "nidan-odoo-db", "db": '"${ODOO_DB_NAME:-nidan}"', "schema": "public",
             "secret": {"res_users": ["password", "oauth_access_token", "totp_secret"], "res_users_apikeys": None,
                        "auth_totp_device": None, "auth_passkey_key": None, "ir_config_parameter": None}},
}


def _q(s):
    return "'" + s.replace("'", "''") + "'"


def pg_sql(cfg, password):
    s, role = cfg["schema"], ROLE
    lines = [
        "BEGIN;",
        f"DO $$ BEGIN IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '{role}') THEN CREATE ROLE {role}; END IF; END $$;",
        f"ALTER ROLE {role} LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS PASSWORD {_q(password)};",
        f"ALTER ROLE {role} SET default_transaction_read_only = on;",
        f"GRANT USAGE ON SCHEMA {s} TO {role};",
        f"REVOKE ALL ON ALL TABLES IN SCHEMA {s} FROM {role};",
        f"GRANT SELECT ON ALL TABLES IN SCHEMA {s} TO {role};",
    ]
    # Default privileges must be set FOR the role that creates the app's tables (the app user, not the
    # superuser running this), or tables created by later upgrades would never be readable.
    first = next(iter(cfg["secret"]))
    lines.append(
        f"DO $$ DECLARE o text := (SELECT tableowner FROM pg_tables WHERE schemaname = '{s}' AND tablename = '{first}'); "
        f"BEGIN IF o IS NOT NULL THEN EXECUTE format('ALTER DEFAULT PRIVILEGES FOR ROLE %I IN SCHEMA {s} "
        f"GRANT SELECT ON TABLES TO {role}', o); END IF; END $$;")
    for table, cols in cfg["secret"].items():
        lines.append(f"DO $$ BEGIN IF to_regclass('{s}.{table}') IS NOT NULL THEN "
                     f"EXECUTE 'REVOKE ALL ON {s}.{table} FROM {role}'; END IF; END $$;")
        if cols:
            hidden = ", ".join(_q(c) for c in cols)
            lines.append(
                "DO $$ DECLARE c text; BEGIN FOR c IN SELECT quote_ident(column_name) FROM information_schema.columns "
                f"WHERE table_schema = '{s}' AND table_name = '{table}' AND column_name NOT IN ({hidden}) LOOP "
                f"EXECUTE format('GRANT SELECT (%s) ON {s}.{table} TO {role}', c); END LOOP; END $$;")
    lines.append("COMMIT;")
    return "\n".join(lines) + "\n"


def mysql_sql(cfg, password):
    """Static part; the per-table GRANTs are generated inside the server by mysql_grant_query()."""
    user = f"'{ROLE}'@'%'"
    return "\n".join([
        f"CREATE USER IF NOT EXISTS {user} IDENTIFIED BY {_q(password)};",
        f"ALTER USER {user} IDENTIFIED BY {_q(password)};",
        f"REVOKE ALL PRIVILEGES, GRANT OPTION FROM {user};",
    ]) + "\n"


def mysql_grant_query(cfg):
    """A SELECT that prints one GRANT per table/column set, executed by a second mysql client."""
    schema, user = cfg["schema"], f"''{ROLE}''@''%''"
    whole = [t for t, c in cfg["secret"].items()]
    partial = {t: c for t, c in cfg["secret"].items() if c}
    excl = ", ".join(f"'{t}'" for t in whole)
    parts = [f"SELECT CONCAT('GRANT SELECT ON `{schema}`.`', table_name, '` TO {user};') FROM information_schema.tables "
             f"WHERE table_schema = '{schema}' AND table_name NOT IN ({excl})"]
    for t, cols in partial.items():
        hidden = ", ".join(f"'{c}'" for c in cols)
        parts.append(f"SELECT CONCAT('GRANT SELECT (', GROUP_CONCAT(CONCAT('`', column_name, '`')), ') ON `{schema}`.`{t}` "
                     f"TO {user};') FROM information_schema.columns WHERE table_schema = '{schema}' AND table_name = '{t}' "
                     f"AND column_name NOT IN ({hidden}) HAVING COUNT(*) > 0")
    return " UNION ALL ".join(parts)


def commands(name, password):
    """(argv, stdin) that applies the grants inside the DB container as its superuser."""
    cfg = DBS[name]
    if cfg["engine"] == "pg":
        shell = f'psql -X -q -v ON_ERROR_STOP=1 -U "$POSTGRES_USER" -d {cfg["db"]}'
        return ["docker", "exec", "-i", cfg["container"], "sh", "-c", shell], pg_sql(cfg, password)
    query = mysql_grant_query(cfg).replace('"', '\\"')
    shell = ('export MYSQL_PWD="$MYSQL_ROOT_PASSWORD"; mysql -uroot && '
             f'mysql -uroot -N -B -e "{query}" | mysql -uroot && mysql -uroot -e "FLUSH PRIVILEGES"')
    return ["docker", "exec", "-i", cfg["container"], "sh", "-c", shell], mysql_sql(cfg, password)


def main(argv):
    names = [a for a in argv[1:] if a in DBS] or list(DBS)
    password = os.environ.get("REPORTING_DB_PASSWORD", "")
    if not re.fullmatch(r"[A-Za-z0-9]{16,}", password):
        print("set REPORTING_DB_PASSWORD (16+ letters/digits; generate-secrets.sh writes one)", file=sys.stderr)
        return 2
    for name in names:
        argv_, stdin = commands(name, password)
        print(f"== {name} ({DBS[name]['container']})")
        if "--apply" not in argv:
            print(stdin.replace(password, "********"), end="")
            continue
        res = subprocess.run(argv_, input=stdin, capture_output=True, text=True)
        if res.returncode != 0:
            print(f"FAILED: {(res.stderr or res.stdout).strip()[:400]}", file=sys.stderr)
            return 1
        print("ok")
    if "--apply" not in argv:
        print("dry run: nothing changed (add --apply)")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
