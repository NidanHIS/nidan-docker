"""Self-test for check-catalog.py: run with `python3 -I scripts/test_check_catalog.py`."""
import importlib.util
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location("cc", HERE / "check-catalog.py")
cc = importlib.util.module_from_spec(spec)
spec.loader.exec_module(cc)

CSV = """Uuid,Role name,Description,Inherited roles,Privileges
1,Privilege Level: High,,,Edit Users;Assign System Developer Role
2,Application: Enters Vitals,,Privilege Level: High,Add Observations
3,Application: Reads Charts,,,View Patients
"""
JOB = """groups:
  - name: staff
    subGroups:
      - name: {job}
        clientRoles:
          openmrs: [{role}]
"""


def run(job_role=None, drop_from_catalog=None, unflag=None):
    with tempfile.TemporaryDirectory() as d:
        root = Path(d)
        roles = root / "openmrs-backend/openmrs-content-referenceapplication-demo/configuration/roles/x"
        roles.mkdir(parents=True)
        (roles / "r.csv").write_text(CSV)
        realm = root / "realm"
        cc.REALM_DIR, cc.CATALOG_DIR = realm, realm / "roles"
        cc.write(root)
        cat = cc.CATALOG_DIR / "openmrs.yaml"
        text = cat.read_text()
        if drop_from_catalog:
            text = text.replace(f"- name: '{drop_from_catalog}'", "- name: 'Gone'").replace(
                f"- name: {drop_from_catalog}\n", "- name: Gone\n")
        if unflag:
            data = cc.yaml.safe_load(text)
            for r in data["roles"]:
                if r["name"] == unflag:
                    r["superuser"] = False
            text = cc.yaml.safe_dump(data)
        cat.write_text(text)
        if job_role:
            (realm / "jobs.yaml").write_text(JOB.format(job="nurse", role=repr(job_role)))
        return cc.check(root)


def test_clean():
    assert run() == [], run()


def test_inherited_admin_privilege_makes_superuser():
    roles = cc.openmrs_roles  # derivation, not the catalog's say-so
    with tempfile.TemporaryDirectory() as d:
        p = Path(d) / "openmrs-backend/openmrs-content-referenceapplication-demo/configuration/roles/x"
        p.mkdir(parents=True)
        (p / "r.csv").write_text(CSV)
        got = roles(Path(d))
    assert got["Application: Enters Vitals"][1] is True
    assert got["Application: Reads Charts"][1] is False


def test_job_mapping_superuser_fails():
    out = run(job_role="Application: Enters Vitals")
    assert any("/staff/nurse" in p and "KC-57" in p for p in out), out


def test_job_mapping_core_superuser_fails():
    out = run(job_role="System Developer")
    assert any("KC-57" in p for p in out), out


def test_job_mapping_plain_role_ok():
    assert run(job_role="Application: Reads Charts") == []


def test_job_mapping_superuser_via_composite_fails():
    with tempfile.TemporaryDirectory() as d:
        realm = Path(d)
        cc.REALM_DIR, cc.CATALOG_DIR = realm, realm / "roles"
        (realm / "r.yaml").write_text("""roles:
  realm:
    - name: ward-bundle
      composites: {realm: [inner]}
    - name: inner
      composites: {client: {openmrs: [System Developer]}}
groups:
  - name: staff
    subGroups: [{name: nurse, realmRoles: [ward-bundle]}]
""")
        got = cc.job_role_mappings()
    assert ("/staff/nurse", "openmrs", "System Developer") in got, got


def test_job_baseline_and_user_rules():
    docs = [cc.yaml.safe_load("""groups:
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
    out = cc.job_and_user_rules(mappings, docs)
    assert any("/staff/cashier" in p and "base.group_user" in p for p in out), out
    assert not any("/staff/reporter" in p and "baseline" in p for p in out), out
    assert any("user admin" in p and "R6" in p for p in out), out
    assert any("service-account-cis" in p for p in out), out
    assert any("user ram" in p and "KC-09-08" in p for p in out), out
    assert not any("sita" in p for p in out), out
    assert len(out) == 4, out


def test_site_overlay_roles_must_be_in_the_export():
    # KC-24-03
    with tempfile.TemporaryDirectory() as d:
        (Path(d) / "roles").mkdir()
        (Path(d) / "roles/openelis.yaml").write_text("app: openelis\nroles:\n  - {name: Microbiology Bench}\n"
                                                      "  - {name: Ghost Role}\n  - {name: Global Administrator}\n")
        export = {"openelis": {"roles": {"Microbiology Bench": 4, "Global Administrator": 1}}}
        out = cc.check_site(d, export)
        failed = cc.check_site(d, {"openelis": {"error": "connection refused"}})
    assert not any("Microbiology Bench" in p for p in out), out
    assert any("'Ghost Role' is not in the site export" in p for p in out), out
    assert any("'Global Administrator' is already a base" in p for p in out), out
    assert failed == ["openelis: no export data for the site, cannot confirm 3 overlay role(s)"], failed


def test_site_superuser_role_in_a_job_fails():
    with tempfile.TemporaryDirectory() as d:
        realm = Path(d)
        (realm / "sites/h1/roles").mkdir(parents=True)
        (realm / "sites/h1/roles/openelis.yaml").write_text("app: openelis\nroles:\n  - {name: Site Lab Admin, superuser: true}\n")
        (realm / "jobs.yaml").write_text("groups:\n  - name: staff\n    subGroups:\n      - name: labtech\n"
                                         "        clientRoles: {openelis: [Site Lab Admin]}\n")
        cc.REALM_DIR, cc.CATALOG_DIR = realm, HERE.parent / "keycloak/realm/roles"
        out = cc.check(HERE.parent.parent)
    assert any("/staff/labtech" in p and "Site Lab Admin" in p for p in out), out


def test_openelis_liquibase_roles_with_any_attribute_spacing():
    with tempfile.TemporaryDirectory() as d:
        lb = Path(d) / "OpenELIS-Global-2/src/main/resources/liquibase/x"
        lb.mkdir(parents=True)
        (lb / "a.xml").write_text("""<databaseChangeLog><changeSet id="1" author="a">
  <insert schemaName="clinlims" tableName="system_role">
    <column name="id" valueSequenceNext="system_role_seq"/>
    <column name="name"            value="Paywall Administration"/>
  </insert>
  <insert tableName="system_role"><column name="name" value="Results"/></insert>
</changeSet></databaseChangeLog>""")
        got = cc.openelis_roles(Path(d))
    assert set(got) == {"Paywall Administration", "Results"}, got


def test_renamed_role_fails_both_ways():
    out = run(drop_from_catalog="Application: Reads Charts")
    assert any("'Gone' no longer defined" in p for p in out), out
    assert any("'Application: Reads Charts'" in p and "missing from the catalog" in p for p in out), out


def test_catalog_cannot_unflag_superuser():
    out = run(unflag="Application: Enters Vitals")
    assert any("superuser per its source" in p for p in out), out


if __name__ == "__main__":
    n = 0
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
            n += 1
    print(f"check-catalog: {n} tests passed")
