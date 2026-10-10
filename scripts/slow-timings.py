#!/usr/bin/env python3
"""Markdown table of the @slow Playwright tests and their durations (backlog KC-42-04).

  scripts/slow-timings.py report.json [...]      (Playwright JSON reports; the nightly job appends it to the run summary)
"""
import json
import re
import sys


def rows(report):
    out = []

    def walk(suite):
        for spec in suite.get("specs", []):
            if "@slow" not in spec.get("title", ""):
                continue
            for t in spec.get("tests", []):
                r = (t.get("results") or [{}])[-1]
                ids = " ".join(re.findall(r"@(KC-\d+-\d+)", spec["title"]))
                out.append((ids, r.get("status", "?"), r.get("duration", 0) / 1000, spec["title"].split(" @")[0]))
        for s in suite.get("suites", []):
            walk(s)

    for s in report.get("suites", []):
        walk(s)
    return out


def main(argv):
    if len(argv) < 2:
        print(__doc__)
        return 2
    found = [r for path in argv[1:] for r in rows(json.load(open(path, encoding="utf-8")))]
    print("| ACs | result | seconds | check |\n|---|---|---:|---|")
    for ids, status, secs, title in sorted(found):
        print(f"| {ids} | {status} | {secs:.0f} | {title} |")
    if not found:
        print("| - | - | - | no @slow test ran |")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
