"""Self-test for release-gate.py: `python3 -I scripts/test_release_gate.py`."""
import importlib.util
from pathlib import Path

spec = importlib.util.spec_from_file_location("rg", Path(__file__).resolve().parent / "release-gate.py")
rg = importlib.util.module_from_spec(spec)
spec.loader.exec_module(rg)

# Same shape as a real Playwright JSON report (checked against one produced by specs/routes.spec.ts).
REPORT = {"suites": [{"title": "routes.spec.ts", "specs": [], "suites": [{"title": "x", "specs": [
    {"title": "unknown paths are 404 @KC-10-03 @security", "tags": ["@KC-10-03", "@security"],
     "tests": [{"status": "expected", "results": [{"status": "passed"}]}]},
    {"title": "case variants @KC-10-03", "tags": ["@KC-10-03"],
     "tests": [{"status": "skipped", "results": [{"status": "skipped"}]}]},
    {"title": "forged host @KC-56-06 @security", "tags": ["@KC-56-06"],
     "tests": [{"status": "expected", "results": [{"status": "passed"}]}]},
    {"title": "flaky @KC-11-01", "tags": ["@KC-11-01"],
     "tests": [{"status": "unexpected", "results": [{"status": "failed"}]}]},
]}]}]}
res = rg.playwright_results(REPORT)
assert res == {"KC-10-03": ["passed", "skipped"], "KC-56-06": ["passed"], "KC-11-01": ["failed"]}, res

ACS = [{"id": i, "security": sec} for i, sec in (("KC-10-03", True), ("KC-56-06", True), ("KC-11-01", True),
                                                 ("KC-05-01", True), ("KC-24-03", True), ("KC-15-01", False),
                                                 ("KC-99-01", True))]
EVIDENCE = {"KC-05-01": [{"status": "passing"}], "KC-24-03": [{"status": "passing"}, {"status": "tool"}],
            "KC-15-01": [{"status": "tool"}]}
red = dict(rg.red_security_acs(ACS, EVIDENCE, res))
assert set(red) == {"KC-10-03", "KC-11-01", "KC-24-03", "KC-99-01"}, red   # skipped / failed / one tool / nothing
assert "skipped" in red["KC-10-03"] and "failed" in red["KC-11-01"] and "tool" in red["KC-24-03"]
assert "KC-15-01" not in red                                                 # not a security AC
assert rg.main(["x"]) == 1                                                   # today: refuses
print("release-gate: OK")
