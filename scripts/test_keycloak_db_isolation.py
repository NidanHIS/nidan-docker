"""Self-test for preflight's KC-07-10 rule: `python3 -I scripts/test_keycloak_db_isolation.py`."""
import importlib.util
from pathlib import Path

spec = importlib.util.spec_from_file_location("pf", Path(__file__).resolve().parent / "preflight.py")
pf = importlib.util.module_from_spec(spec)
spec.loader.exec_module(pf)

KC = "Keycloak-DB-Only-Value-1234567890"
base = {"keycloak-db": {"environment": {"POSTGRES_PASSWORD": KC}},
        "keycloak": {"environment": {"KC_DB_PASSWORD": KC}},
        "odoo-db": {"environment": {"POSTGRES_PASSWORD": "Odoo-Own-Value-1234567890"}}}
assert pf.keycloak_db_isolation_problems(base) == []
shared = dict(base, **{"openmrs-db": {"environment": {"MYSQL_ROOT_PASSWORD": KC}}})
p = pf.keycloak_db_isolation_problems(shared)
assert len(p) == 1 and p[0].startswith("openmrs-db: MYSQL_ROOT_PASSWORD") and KC not in p[0], p   # value never printed
assert pf.keycloak_db_isolation_problems({"odoo": {"environment": {"X_PASSWORD": "a"}}}) == []   # no Keycloak: nothing
print("keycloak-db isolation self-test passed")
