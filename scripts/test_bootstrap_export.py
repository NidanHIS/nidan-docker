"""Self-test for bootstrap-export.py: run with `python3 -I scripts/test_bootstrap_export.py`."""
import csv
import importlib.util
import json
import sqlite3
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location("be", HERE / "bootstrap-export.py")
be = importlib.util.module_from_spec(spec)
spec.loader.exec_module(be)


def test_every_app_runs_read_only():
    # KC-25-01: plain SELECTs only, wrapped in a READ ONLY transaction (or a mode=ro SQLite open)
    for app in be.APPS:
        argv, sql = be.script_for(app)
        assert argv[:3] == ["docker", "exec", "-i"], argv
        if "sqlite" in be.APPS[app]:
            assert "mode=ro" in argv[-2]
        else:
            assert sql.splitlines()[0].upper().startswith(("START TRANSACTION READ ONLY", "BEGIN READ ONLY")), sql
            assert sql.rstrip().endswith("COMMIT;")
        assert "PASSWORD" not in " ".join(argv[:-1]).replace("$MYSQL_PASSWORD", ""), "no secret on the host argv"


def test_a_write_query_is_refused():
    saved = be.APPS["openmrs"]["queries"]
    for bad in ["UPDATE users SET retired = 1", "SELECT 1; DELETE FROM users", "SELECT * INTO OUTFILE '/x' FROM users"]:
        be.APPS["openmrs"]["queries"] = [bad]
        try:
            be.script_for("openmrs")
            raise AssertionError("accepted " + bad)
        except ValueError:
            pass
    be.APPS["openmrs"]["queries"] = saved


def test_sqlite_reader_cannot_write():
    with tempfile.TemporaryDirectory() as d:
        db = Path(d) / "s.db"
        c = sqlite3.connect(db)
        c.executescript("CREATE TABLE ab_user(id, username, active); INSERT INTO ab_user VALUES (1, 'asha', 1);")
        c.commit()
        c.close()
        ok = subprocess.run([sys.executable, "-I", "-c", be.SQLITE_RO, str(db)], input="SELECT username FROM ab_user;\n",
                            capture_output=True, text=True)
        assert ok.stdout.strip() == "asha", ok
        bad = subprocess.run([sys.executable, "-I", "-c", be.SQLITE_RO, str(db)], input="DELETE FROM ab_user;\n",
                             capture_output=True, text=True)
        assert bad.returncode != 0 and "readonly" in bad.stderr.lower(), bad
        assert sqlite3.connect(db).execute("SELECT COUNT(*) FROM ab_user").fetchone()[0] == 1


def test_export_parses_and_records_failures():
    def fake(argv, input, capture_output, text):
        if "nidan-odoo-db" in argv:
            return subprocess.CompletedProcess(argv, 2, "", "connection refused")
        return subprocess.CompletedProcess(argv, 0, "U\t7\tram\t1\t3-4\nR\tDoctor\t1\nM\t7\tDoctor\n", "")
    with tempfile.TemporaryDirectory() as d:
        data = be.export(Path(d) / "e.json", run=fake)
    assert data["openmrs"]["users"] == [{"id": "7", "username": "ram", "active": True, "note": "3-4"}]
    assert data["openmrs"]["roles"] == {"Doctor": 1}
    assert "error" in data["odoo"]


EXPORT = {
    "openmrs": {"users": [{"id": "7", "username": "ram", "active": True, "note": "3-4"},
                          {"id": "1", "username": "localadm", "active": True, "note": "1-2"}],
                "roles": {"Doctor Role": 1, "Ghost": 0, "Site Thing": 2}, "memberships": []},
    "odoo": {"users": [{"id": "9", "username": "Ram", "active": True, "note": ""}], "roles": {}, "memberships": []},
}


def test_workbook_never_autolinks_and_reserves_unlinked():
    with tempfile.TemporaryDirectory() as d:
        exp = Path(d) / "e.json"
        exp.write_text(json.dumps(EXPORT))
        wb = Path(d) / "wb"
        be.workbook(exp, wb)
        rows = list(csv.DictReader(open(wb / "link-table.csv")))
        assert len(rows) == 3 and all(r["decision"] == "" and r["keycloak_sub"] == "" for r in rows), rows
        ram = [r for r in rows if r["username"].lower() == "ram"]
        assert {r["same_name_in"] for r in ram} == {"odoo", "openmrs"}, ram   # KC-25-02: grouped for review
        assert (wb / "reserved-usernames.txt").read_text().split() == ["localadm", "ram"]  # KC-25-03

        # the operator links ram in both apps; a re-run keeps the decisions and frees only that name
        for r in rows:
            if r["username"].lower() == "ram":
                r["decision"], r["keycloak_sub"] = "link", "sub-ram"
        with open(wb / "link-table.csv", "w", newline="") as fh:
            w = csv.DictWriter(fh, be.LINK_FIELDS)
            w.writeheader()
            w.writerows(rows)
        be.workbook(exp, wb)
        again = list(csv.DictReader(open(wb / "link-table.csv")))
        assert sum(r["decision"] == "link" for r in again) == 2, again
        assert (wb / "reserved-usernames.txt").read_text().split() == ["localadm"]

        roles = {(r["app"], r["role"]): r for r in csv.DictReader(open(wb / "roles.csv"))}
        assert roles[("openmrs", "Doctor Role")]["catalog"] == "superuser"
        assert roles[("openmrs", "Ghost")]["proposed_class"] == "unused"
        assert roles[("openmrs", "Site Thing")]["catalog"] == "site-only"


def test_workbook_refuses_a_git_work_tree():
    try:
        be.workbook(HERE / "nonexistent.json", HERE / "wb")
        raise AssertionError("wrote inside git")
    except SystemExit as e:
        assert "git" in str(e)
    assert not (HERE / "wb").exists()


if __name__ == "__main__":
    n = 0
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
            n += 1
    print(f"bootstrap-export: {n} tests passed")
