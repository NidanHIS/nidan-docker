"""Self-test for dry-run-diff.py: run with `python3 -I scripts/test_dry_run_diff.py`."""
import csv
import importlib.util
import json
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent.parent
spec = importlib.util.spec_from_file_location("drd", HERE / "dry-run-diff.py")
drd = importlib.util.module_from_spec(spec)
spec.loader.exec_module(drd)

REALM = """groups:
  - name: staff
    subGroups:
      - name: pharmacist
        clientRoles:
          openmrs: [Inventory Dispensing]
          odoo: [nidan_blood_bank.group_blood_bank_manager]
"""
LINK = ["username", "app", "app_user_id", "decision"]


def export(extra_openmrs=(), extra_odoo=()):
    om = ["Inventory Dispensing", *extra_openmrs]
    # Odoo stores implied groups as memberships: manager -> user -> stock user
    od = ["nidan_blood_bank.group_blood_bank_manager", "nidan_blood_bank.group_blood_bank_user",
          "stock.group_stock_user", *extra_odoo]
    return {
        "openmrs": {"users": [], "roles": {r: 1 for r in om} | {"Site Custom": 3},
                    "memberships": [{"user_id": "7", "role": r} for r in om]},
        "odoo": {"users": [], "roles": {r: 1 for r in od}, "memberships": [{"user_id": "9", "role": r} for r in od]},
    }


def run(exp, jobs, link=None):
    with tempfile.TemporaryDirectory() as d:
        d = Path(d)
        drd.cc.REALM_DIR, drd.cc.CATALOG_DIR = d / "realm", HERE.parent / "keycloak/realm/roles"
        (d / "realm").mkdir()
        (d / "realm/jobs.yaml").write_text(REALM)
        (d / "e.json").write_text(json.dumps(exp))
        wb = d / "wb"
        wb.mkdir()
        for name, fields, rows in (("link-table.csv", LINK, link or [["ram", "openmrs", "7", "link"], ["ram", "odoo", "9", "link"]]),
                                   ("jobs.csv", ["username", "job", "superuser", "accept_loss"], jobs)):
            with open(wb / name, "w", newline="") as fh:
                w = csv.writer(fh)
                w.writerow(fields)
                w.writerows(rows)
        code = drd.main(["x", str(d / "e.json"), str(wb), str(ROOT)])
        losses, notes, orphans = drd.diff(exp, drd.read_csv(wb / "link-table.csv"), drd.read_csv(wb / "jobs.csv"),
                                          drd.job_grants(ROOT), drd.cc.derived(ROOT))
        return code, losses, notes, orphans


def test_clean_mapping_exits_zero():
    # KC-26-02 (implied Odoo groups count as granted, not lost)
    code, losses, notes, _ = run(export(), [["ram", "pharmacist", "", ""]])
    assert (code, losses, notes) == (0, [], []), (losses, notes)


def test_lost_permission_listed_and_fails():
    # KC-26-01
    code, losses, _, _ = run(export(extra_odoo=["account.group_account_manager"]), [["ram", "pharmacist", "", ""]])
    assert code == 1 and any("account.group_account_manager" in l for l in losses), losses


def test_accepted_loss_passes():
    code, losses, _, _ = run(export(extra_odoo=["account.group_account_manager"]),
                             [["ram", "pharmacist", "", "odoo:account.group_account_manager"]])
    assert (code, losses) == (0, []), losses


def test_superuser_today_must_be_routed_to_app_superusers():
    # KC-26-04: not silently dropped, not kept by a job; only superuser=yes (operator) keeps it
    exp = export(extra_openmrs=["System Developer"])
    code, losses, _, _ = run(exp, [["ram", "pharmacist", "", ""]])
    assert code == 1 and any("System Developer" in l and "app-superusers" in l for l in losses), losses
    code, losses, _, _ = run(exp, [["ram", "pharmacist", "yes", ""]])
    assert (code, losses) == (0, []), losses


def test_no_job_loses_everything():
    code, losses, _, _ = run(export(), [])
    assert code == 1 and len(losses) == 4, losses


def test_unknown_job_and_unlinked_rows():
    code, _, notes, _ = run(export(), [["ram", "chef", "", ""]])
    assert code == 1 and any("'chef'" in n for n in notes), notes
    # rows not decided "link" are not cut over, so nothing to diff
    code, losses, notes, _ = run(export(), [], link=[["ram", "openmrs", "7", ""], ["ram", "odoo", "9", "skip"]])
    assert (code, losses, notes) == (0, [], [])


def test_orphan_roles_reported_not_fatal():
    # KC-26-03
    code, _, _, orphans = run(export(), [["ram", "pharmacist", "", ""]])
    assert code == 0 and any("Site Custom" in o for o in orphans), orphans


def test_site_overlay_role_is_not_an_orphan():
    exp = export()
    _, _, orphans = drd.diff(exp, [], [], {}, drd.cc.derived(ROOT), site={"openmrs": {"Site Custom"}})
    assert not any("Site Custom" in o for o in orphans), orphans


def test_failed_export_blocks():
    exp = export()
    exp["openelis"] = {"error": "connection refused"}
    code, _, _, _ = run(exp, [["ram", "pharmacist", "", ""]])
    assert code == 1


if __name__ == "__main__":
    n = 0
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
            n += 1
    print(f"dry-run-diff: {n} tests passed")
