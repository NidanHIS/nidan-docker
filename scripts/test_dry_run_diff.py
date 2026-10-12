"""Self-test for dry-run-diff.py: run with `python3 -I scripts/test_dry_run_diff.py`."""
import csv
import importlib.util
import json
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location("drd", HERE / "dry-run-diff.py")
drd = importlib.util.module_from_spec(spec)
spec.loader.exec_module(drd)

# the realm's job mappings (check-realm.job_role_mappings), passed in so the test needs no realm files
JOBS = [("/staff/pharmacist", "openmrs", "Inventory Dispensing"),
        ("/staff/pharmacist", "odoo", "nidan_blood_bank.group_blood_bank_manager")]
LINK = ["username", "app", "app_user_id", "decision"]


def export(extra_openmrs=(), extra_odoo=()):
    om = ["Inventory Dispensing", *extra_openmrs]
    # Odoo stores implied groups as memberships: manager -> user -> stock user
    od = ["nidan_blood_bank.group_blood_bank_manager", "nidan_blood_bank.group_blood_bank_user",
          "stock.group_stock_user", *extra_odoo]
    # the site's own data: what each role includes (Odoo implied groups) and OpenMRS privileges
    return {
        "openmrs": {"users": [], "roles": {r: 1 for r in om} | {"Site Custom": 3, "System Developer": 0},
                    "memberships": [{"user_id": "7", "role": r} for r in om],
                    "includes": {}, "privileges": {"Inventory Dispensing": ["Get Patients"]}},
        "odoo": {"users": [], "roles": {r: 1 for r in od} | {"base.group_system": 0},
                 "memberships": [{"user_id": "9", "role": r} for r in od],
                 "includes": {"nidan_blood_bank.group_blood_bank_manager": ["nidan_blood_bank.group_blood_bank_user"],
                              "nidan_blood_bank.group_blood_bank_user": ["stock.group_stock_user"],
                              "account.group_account_manager": ["base.group_system"]}},
    }


def run(exp, jobs, link=None):
    with tempfile.TemporaryDirectory() as d:
        d = Path(d)
        (d / "e.json").write_text(json.dumps(exp))
        wb = d / "wb"
        wb.mkdir()
        for name, fields, rows in (("link-table.csv", LINK, link or [["ram", "openmrs", "7", "link"], ["ram", "odoo", "9", "link"]]),
                                   ("jobs.csv", ["username", "job", "superuser", "accept_loss"], jobs)):
            with open(wb / name, "w", newline="") as fh:
                w = csv.writer(fh)
                w.writerow(fields)
                w.writerows(rows)
        code = drd.main(["x", str(d / "e.json"), str(wb)], mappings=JOBS)
        losses, notes, orphans = drd.diff(exp, drd.read_csv(wb / "link-table.csv"), drd.read_csv(wb / "jobs.csv"),
                                          drd.job_grants(exp, JOBS))
        return code, losses, notes, orphans


def test_clean_mapping_exits_zero():
    # KC-26-02 (implied Odoo groups count as granted, not lost)
    code, losses, notes, _ = run(export(), [["ram", "pharmacist", "", ""]])
    assert (code, losses, notes) == (0, [], []), (losses, notes)


def test_lost_permission_listed_and_fails():
    # KC-26-01
    code, losses, _, _ = run(export(extra_odoo=["point_of_sale.group_pos_user"]), [["ram", "pharmacist", "", ""]])
    assert code == 1 and any("point_of_sale.group_pos_user" in l for l in losses), losses


def test_accepted_loss_passes():
    code, losses, _, _ = run(export(extra_odoo=["point_of_sale.group_pos_user"]),
                             [["ram", "pharmacist", "", "odoo:point_of_sale.group_pos_user"]])
    assert (code, losses) == (0, []), losses


def test_superuser_from_the_sites_own_data():
    # an Odoo group implying settings (per the export, not repository files) is superuser: a job may not keep it
    exp = export(extra_odoo=["account.group_account_manager"])
    code, losses, _, _ = run(exp, [["ram", "pharmacist", "", ""]])
    assert code == 1 and any("account.group_account_manager" in l and "app-superusers" in l for l in losses), losses
    exp["openmrs"]["privileges"]["Inventory Dispensing"] = ["Manage Roles"]     # this site made it an admin role
    _, _, notes, _ = run(exp, [["ram", "pharmacist", "yes", ""]])
    assert any("'Inventory Dispensing'" in n and "KC-57" in n for n in notes), notes


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
    # KC-26-03: held today, granted by no job (and not superuser): reported, never fatal
    code, _, _, orphans = run(export(), [["ram", "pharmacist", "", ""]])
    assert code == 0 and any("Site Custom" in o for o in orphans), orphans
    assert not any("Inventory Dispensing" in o or "System Developer" in o for o in orphans), orphans


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
