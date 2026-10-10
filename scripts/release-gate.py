#!/usr/bin/env python3
"""Go-live gate: refuse a site release while any security acceptance criterion is red or skipped (KC-49-03).

  scripts/release-gate.py [--playwright tests/e2e-auth/test-results/report.json]

An @security AC (tests/e2e-auth/acceptance-criteria.json) is GREEN only when
  * every entry for it in tests/e2e-auth/ac-evidence.yaml has status: passing, or
  * every Playwright test tagged with it in the JSON report passed (none skipped, none failed).
Everything else - tool, partial, fixme, skipped, failed, missing - is RED. Exit 1 lists the red ones.
The [MAN] criteria (threat model, pen test, signed site checklist) are KC-49-01/02: docs/GO-LIVE-CHECKLIST.md.
"""
import json
import re
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
E2E = ROOT / "tests" / "e2e-auth"


def playwright_results(report):
    """{AC id: [status, ...]} from a Playwright JSON report (tags in titles, e.g. @KC-10-03)."""
    out = {}

    def walk(suite):
        for spec in suite.get("specs", []):
            ids = set(re.findall(r"@(KC-\d+-\d+)", spec.get("title", "") + " " + " ".join(spec.get("tags", []))))
            for test in spec.get("tests", []):
                results = test.get("results") or [{"status": "skipped"}]
                status = "skipped" if test.get("status") == "skipped" else results[-1].get("status", "failed")
                for ac in ids | {t.lstrip("@") for t in test.get("tags", []) if t.lstrip("@").startswith("KC-")}:
                    out.setdefault(ac, []).append(status)
        for child in suite.get("suites", []):
            walk(child)

    for suite in report.get("suites", []):
        walk(suite)
    return out


def red_security_acs(acs, evidence, results):
    red = []
    for ac in acs:
        if not ac.get("security"):
            continue
        i = ac["id"]
        ev = evidence.get(i) or []
        pw = results.get(i) or []
        ev_green = bool(ev) and all((e or {}).get("status") == "passing" for e in ev)
        pw_green = bool(pw) and all(s == "passed" for s in pw)
        if not (ev_green or pw_green):
            why = ("playwright " + ",".join(sorted(set(pw)))) if pw else (
                "evidence " + ",".join(sorted({(e or {}).get("status", "?") for e in ev})) if ev else "no test or evidence")
            red.append((i, why))
    return red


def main(argv):
    acs = json.loads((E2E / "acceptance-criteria.json").read_text())["acs"]
    evidence = yaml.safe_load((E2E / "ac-evidence.yaml").read_text()) or {}
    results = {}
    if "--playwright" in argv:
        report = Path(argv[argv.index("--playwright") + 1])
        results = playwright_results(json.loads(report.read_text())) if report.exists() else {}
        if not results:
            print(f"no Playwright results in {report}: every Playwright-only security AC counts as red")
    red = red_security_acs(acs, evidence, results)
    total = sum(1 for a in acs if a.get("security"))
    print(f"release gate: {total - len(red)}/{total} security acceptance criteria green")
    for i, why in red:
        print(f"  RED {i}: {why}")
    if red:
        print("refusing: a site release needs every @security AC green (KC-49-03) and the signed checklist (KC-49-02)")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
