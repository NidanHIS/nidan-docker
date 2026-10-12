"""Self-test for check-realm.py: `python3 -I scripts/test_check_realm.py`."""
import copy
import importlib.util
from pathlib import Path

import yaml

HERE = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location("cr", HERE / "check-realm.py")
cr = importlib.util.module_from_spec(spec)
spec.loader.exec_module(cr)

base = yaml.safe_load((HERE.parent / "keycloak/realm/base/nidan.yaml").read_text())
master = yaml.safe_load((HERE.parent / "keycloak/realm/master/automation.yaml").read_text())
assert cr.check(base, "base") == [] and cr.check(master, "master") == []

bad = copy.deepcopy(master)
bad["clients"].append({"clientId": "console-2", "standardFlowEnabled": False, "implicitFlowEnabled": False,
                       "directAccessGrantsEnabled": False, "fullScopeAllowed": False, "serviceAccountsEnabled": True})
bad["clients"][1]["standardFlowEnabled"] = True
out = cr.check(bad, "master")
assert any("only ['nidan-config-cli', 'nidan-incident', 'nidan-role-sync']" in p for p in out), out
assert any("nidan-incident: machine client must be service-account only" in p for p in out), out

weak = copy.deepcopy(base)
weak["registrationAllowed"] = True
weak["clients"][0]["directAccessGrantsEnabled"] = True
weak["clients"][0]["redirectUris"] = ["https://*"]
out = cr.check(weak, "base")
for frag in ("registrationAllowed", "directAccessGrantsEnabled", "exact https"):
    assert any(frag in p for p in out), (frag, out)
unstated = copy.deepcopy(base)
del unstated["resetPasswordAllowed"]
assert any("resetPasswordAllowed" in p for p in cr.check(unstated, "base")), "unstated flag must be refused"
env = cr.config_cli_env()
assert {"KC_OPENELIS_CLIENT_SECRET", "KC_AUTOMATION_CLIENT_SECRET", "KC_CONFIG_CLI_CLIENT_SECRET"} <= env
probs = cr.substitution_problems("# Variables: $(env:NAME)\nsecret: $(env:KC_NOT_PASSED)  # ok\nx: $(env:GATEWAY_DOMAIN)\n"
                                 "y: 1   # e.g. $(env:FOO)\n", "t.yaml", env)
assert [p.split(": ", 1)[0] for p in probs] == ["t.yaml:1", "t.yaml:2", "t.yaml:4"], probs
seeded = copy.deepcopy(base)
next(r for r in seeded["roles"]["realm"] if r["name"] == "default-roles-nidan")["composites"]["client"]["account"].append("manage-account")
assert any("default-roles-nidan" in p for p in cr.check(seeded, "base"))

# jobs (moved from check-catalog.py): composites expanded, R3 baseline, R6, KC-09-08
import tempfile
with tempfile.TemporaryDirectory() as d:
    cr.REALM_DIR = Path(d)
    (Path(d) / "r.yaml").write_text("""roles:
  realm:
    - name: ward-bundle
      composites: {realm: [inner]}
    - name: inner
      composites: {client: {openmrs: [System Developer]}}
groups:
  - name: staff
    subGroups: [{name: nurse, realmRoles: [ward-bundle]}]
""")
    got = cr.job_role_mappings()
assert ("/staff/nurse", "openmrs", "System Developer") in got, got
docs = [yaml.safe_load("""groups:
  - name: staff
    subGroups:
      - {name: cashier, clientRoles: {nidan-gateway: [app-odoo], odoo: [point_of_sale.group_pos_user]}}
      - {name: reporter, clientRoles: {nidan-gateway: [app-superset], superset: [Gamma]}}
      - {name: empty}
users:
  - {username: admin, groups: [/staff/cashier]}
  - {username: service-account-cis, groups: [staff/empty]}
  - {username: ram, groups: [/staff/reporter, /app-superusers]}
  - {username: sita, groups: [/staff/cashier]}
""")]
mappings = [("/staff/cashier", "nidan-gateway", "app-odoo"), ("/staff/cashier", "odoo", "point_of_sale.group_pos_user"),
            ("/staff/reporter", "nidan-gateway", "app-superset"), ("/staff/reporter", "superset", "Gamma")]
out = cr.job_and_user_rules(mappings, docs)
assert any("/staff/cashier" in p and "base.group_user" in p for p in out), out
assert any("user admin" in p and "R6" in p for p in out) and any("service-account-cis" in p for p in out), out
assert any("user ram" in p and "KC-09-08" in p for p in out) and not any("sita" in p for p in out), out
assert len(out) == 4, out
print("check-realm: OK")
