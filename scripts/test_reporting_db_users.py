"""Self-test for reporting-db-users.py and preflight's KC-48 rule: `python3 -I scripts/test_reporting_db_users.py`.
The generated SQL was also run against a real Odoo 19 Postgres DB (rolled back) and a throwaway MySQL 8."""
import importlib.util
import os
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location("r", HERE / "reporting-db-users.py")
r = importlib.util.module_from_spec(spec)
spec.loader.exec_module(r)

pg = r.pg_sql(r.DBS["odoo"], "ReportingPass1234567")
assert "default_transaction_read_only = on" in pg and "NOSUPERUSER" in pg
assert "REVOKE ALL ON public.ir_config_parameter" in pg and "REVOKE ALL ON public.res_users_apikeys" in pg
assert "'password', 'oauth_access_token', 'totp_secret'" in pg and "FOR ROLE %I" in pg
my = r.mysql_grant_query(r.DBS["openmrs"])
assert "NOT IN ('users', 'user_property', 'global_property')" in my and "'password', 'salt'" in my
argv, stdin = r.commands("openelis", "ReportingPass1234567")
assert "ReportingPass1234567" not in " ".join(argv) and "ReportingPass1234567" in stdin   # password on stdin only
argv, stdin = r.commands("openmrs", "ReportingPass1234567")
assert "ReportingPass1234567" not in " ".join(argv)
weak = subprocess.run([sys.executable, "-I", str(HERE / "reporting-db-users.py")], capture_output=True, text=True,
                      env=dict(os.environ, REPORTING_DB_PASSWORD="short"))
assert weak.returncode == 2
dry = subprocess.run([sys.executable, "-I", str(HERE / "reporting-db-users.py"), "odoo"], capture_output=True, text=True,
                     env=dict(os.environ, REPORTING_DB_PASSWORD="ReportingPass1234567"))
assert dry.returncode == 0 and "ReportingPass1234567" not in dry.stdout and "dry run" in dry.stdout

ps = importlib.util.spec_from_file_location("pf", HERE / "preflight.py")
pf = importlib.util.module_from_spec(ps)
ps.loader.exec_module(pf)
bad = {"nidan-dhis2": {"environment": {"OPENMRS_DB_URL": "mysql+pymysql://openmrs:x@db:3306/openmrs"}},
       "superset": {"environment": {"ODOO_DB_USER": "nidan"}}}
assert len(pf.reporting_problems(bad)) == 2
good = {"nidan-dhis2": {"environment": {"OPENMRS_DB_URL": "mysql+pymysql://nidan_reporting:x@db:3306/openmrs"}},
        "superset": {"environment": {"ODOO_DB_USER": "nidan_reporting"}}}
assert pf.reporting_problems(good) == []

# KC-48-02: app password == root, a non-DB service holding another role's superuser password -> problems;
# an app that is its own dedicated DB's superuser -> note only
svcs = {
    "openmrs-db": {"environment": {"MYSQL_ROOT_PASSWORD": "R00t-pass-123456", "MYSQL_USER": "openmrs",  # gitleaks:allow (test fixture)
                                   "MYSQL_PASSWORD": "R00t-pass-123456"}},  # gitleaks:allow (test fixture)
    "odoo-db": {"environment": {"POSTGRES_USER": "odoo", "POSTGRES_PASSWORD": "pg-super-0987654"}},
    "odoo": {"environment": {"USER": "nidan", "PASSWORD": "app-pass-5555555", "POSTGRES_PASSWORD": "pg-super-0987654"}},
    "orthanc-db": {"environment": {"POSTGRES_USER": "orthanc", "POSTGRES_PASSWORD": "orth-3333333333"}},
    "orthanc": {"environment": {"ORTHANC_DB_USER": "orthanc", "ORTHANC__POSTGRESQL__PASSWORD": "orth-3333333333"}},
}
problems, notes = pf.db_superuser_findings(svcs)
assert any("openmrs-db: app user 'openmrs' has the same password as root" in p for p in problems), problems
assert any(p.startswith("odoo: POSTGRES_PASSWORD is the superuser password of odoo-db") for p in problems), problems
assert any("orthanc runs as 'orthanc'" in n for n in notes) and not any(p.startswith("orthanc:") for p in problems)
print("reporting-db-users: OK")
