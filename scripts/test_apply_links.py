"""Self-test for apply-links.py: `python3 -I scripts/test_apply_links.py`."""
import csv
import hashlib
import importlib.util
import json
import subprocess
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location("al", HERE / "apply-links.py")
al = importlib.util.module_from_spec(spec)
spec.loader.exec_module(al)

S1 = "6f1c2a9e-0b7d-4c1e-9f3a-2d5e8b7c6a10"
S2 = "11111111-2222-4333-8444-555555555555"
EXPORT = {
    "openmrs": {"users": [{"id": "7", "username": "ram"}, {"id": "8", "username": "sita"}]},
    "odoo": {"users": [{"id": "9", "username": "ram"}]},
    "openelis": {"users": [{"id": "3", "username": "ram"}]},
    "superset": {"users": [{"id": "1", "username": "ram"}]},
}


def row(app, uid, sub, decision="link"):
    return {"app": app, "app_user_id": uid, "keycloak_sub": sub, "decision": decision}


def test_clean_plan():
    out, links, problems, notes = al.plan(EXPORT, [row("openmrs", "7", S1), row("odoo", "9", S1.upper()),
                                                   row("openelis", "3", S1), row("superset", "1", S1),
                                                   row("openmrs", "8", S2, decision="")])
    assert problems == [], problems
    assert [len(out[a]) for a in al.LINKED_APPS] == [1, 1, 1]
    assert ("odoo", "9", S1) in links  # sub lower-cased
    assert any("superset needs no link key" in n for n in notes)
    assert "username = 'ram'" in out["openmrs"][0] and f"system_id = '{S1}'" in out["openmrs"][0]
    assert "nidan_keycloak" in out["odoo"][0] and "AND other.id <> 3" in out["openelis"][0]


def test_refusals():
    cases = {
        "not a UUID": [row("openmrs", "7", "abc' OR '1'='1")],
        "not numeric": [row("openmrs", "7; DROP TABLE users", S1)],
        "not in the export": [row("openmrs", "99", S1)],
        "linked to 2 Keycloak users": [row("openmrs", "7", S1), row("openmrs", "7", S2)],   # KC-36-02
        "linked to 2 openmrs accounts": [row("openmrs", "7", S1), row("openmrs", "8", S1)],
        "unknown app": [row("orthanc", "1", S1)],
    }
    for why, rows in cases.items():
        _, _, problems, _ = al.plan(EXPORT, rows)
        assert any(why in p for p in problems), (why, problems)
    _, _, problems, _ = al.plan({**EXPORT, "odoo": {"error": "down"}}, [row("openmrs", "7", S1)])
    assert any("export failed" in p for p in problems), problems
    bad_name = {**EXPORT, "openmrs": {"users": [{"id": "7", "username": "o'brien"}]}}
    _, _, problems, _ = al.plan(bad_name, [row("openmrs", "7", S1)])
    assert any("unexpected characters" in p for p in problems), problems


def test_render_parse_roundtrip_and_apply_transactions():
    out, links, _, _ = al.plan(EXPORT, [row("openmrs", "7", S1), row("openelis", "3", S1)])
    text = al.render(out)
    parsed = al.parse_plan(text)
    assert parsed["openmrs"] == out["openmrs"] and parsed["odoo"] == [] and parsed["openelis"] == out["openelis"]
    calls = []

    def fake(argv, input, capture_output, text):
        calls.append((argv[3], input))
        return subprocess.CompletedProcess(argv, 0, "", "")
    res = al.apply(text, run=fake)
    assert set(res) == {"openmrs", "openelis"}  # odoo has nothing to run
    by = dict(calls)
    assert by["nidan-openmrs-db"].startswith("START TRANSACTION;") and by["nidan-openmrs-db"].rstrip().endswith("COMMIT;")
    assert by["nidan-openelis-db"].startswith("BEGIN;")


def test_verify_reports_missing_links():
    def fake(argv, input, capture_output, text):
        out = "NOT-LINKED\t7\n" if "nidan-openmrs-db" in argv else ""
        assert "READ ONLY" in input  # the check never writes
        return subprocess.CompletedProcess(argv, 0, out, "")
    missing = al.verify([("openmrs", "7", S1), ("openelis", "3", S1)], run=fake)
    assert missing == [("openmrs", "7")], missing


def test_cli_requires_the_reviewed_digest():
    with tempfile.TemporaryDirectory() as d:
        d = Path(d)
        (d / "e.json").write_text(json.dumps(EXPORT))
        with open(d / "link-table.csv", "w", newline="") as fh:
            w = csv.DictWriter(fh, ["username", "app", "app_user_id", "decision", "keycloak_sub"])
            w.writeheader()
            w.writerow({"username": "ram", "app": "openmrs", "app_user_id": "7", "decision": "link", "keycloak_sub": S1})
        assert al.main(["x", "plan", str(d / "e.json"), str(d)]) == 0
        text = (d / "link-plan.sql").read_text()
        good = hashlib.sha256(text.encode()).hexdigest()
        assert al.main(["x", "apply", str(d / "e.json"), str(d), "0" * 64]) == 1
        (d / "link-plan.sql").write_text(text + "UPDATE users SET system_id = 'x';\n")
        assert al.main(["x", "apply", str(d / "e.json"), str(d), good]) == 1  # edited plan refused


def test_password_lock_plan_and_apply():
    # KC-36-03: only linked accounts, only the chosen ward, values generated once into the file
    import re
    with tempfile.TemporaryDirectory() as d:
        d = Path(d)
        (d / "e.json").write_text(json.dumps(EXPORT))
        with open(d / "link-table.csv", "w", newline="") as fh:
            w = csv.DictWriter(fh, ["username", "app", "app_user_id", "decision", "keycloak_sub"])
            w.writeheader()
            for app, uid, name, sub in (("openmrs", "7", "ram", S1), ("openmrs", "8", "sita", S2), ("odoo", "9", "ram", S1),
                                        ("openelis", "3", "ram", S1)):
                w.writerow({"username": name, "app": app, "app_user_id": uid, "decision": "link", "keycloak_sub": sub})
        (d / "ward.txt").write_text("ram\n")
        assert al.main(["x", "lock-plan", str(d / "e.json"), str(d), "--only", str(d / "ward.txt")]) == 0
        text = (d / "password-lock.sql").read_text()
        parsed = al.parse_plan(text)
        assert [len(parsed[a]) for a in al.LINKED_APPS] == [1, 1, 1], parsed  # sita (user 8) not in this ward
        assert f"user_id = 7 AND system_id = '{S1}'" in parsed["openmrs"][0]
        assert re.search(r"password = '\$pbkdf2-sha512\$25000\$[./A-Za-z0-9]+\$[./A-Za-z0-9]+'", parsed["odoo"][0])
        assert f"AND external_id = '{S1}'" in parsed["openelis"][0]
        assert al.main(["x", "lock-plan", str(d / "e.json"), str(d), "--only", str(d / "ward.txt")]) == 0
        assert (d / "password-lock.sql").read_text() != text, "fresh random values each plan"
        good = hashlib.sha256((d / "password-lock.sql").read_text().encode()).hexdigest()
        assert al.main(["x", "lock-apply", str(d / "e.json"), str(d), "0" * 64]) == 1
        (d / "password-lock.sql").write_text((d / "password-lock.sql").read_text() + "UPDATE users SET password='x';\n")
        assert al.main(["x", "lock-apply", str(d / "e.json"), str(d), good]) == 1  # edited after review


if __name__ == "__main__":
    n = 0
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
            n += 1
    print(f"apply-links: {n} tests passed")
