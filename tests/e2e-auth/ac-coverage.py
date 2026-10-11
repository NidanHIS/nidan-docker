#!/usr/bin/env python3
"""AC coverage (backlog KC-42-03): which acceptance criteria have a test.

  python3 ac-coverage.py                 per-story table
  python3 ac-coverage.py KC-51 KC-53     also fail (exit 1) if any non-manual AC of these stories
                                         has neither a Playwright test nor evidence
A Playwright test counts when its title carries @KC-nn-mm; test.fixme() ones count as pending.
"""
import json
import re
import sys
from collections import defaultdict
from pathlib import Path

import yaml

HERE = Path(__file__).resolve().parent
acs = json.loads((HERE / "acceptance-criteria.json").read_text())["acs"]
evidence = yaml.safe_load((HERE / "ac-evidence.yaml").read_text()) or {}

by_id = {a["id"]: a for a in acs}
integrity = []  # KC-42-03: tags and evidence must point at real ACs; a security AC's tests carry @security
for key, entries in evidence.items():
    if key not in by_id:
        integrity.append(f"ac-evidence.yaml: {key} is not an AC id")
    for e in entries or []:
        for field in ("path", "test"):
            if e.get(field) and not any(HERE.parents[2].glob(e[field].split(" ")[0])):
                integrity.append(f"ac-evidence.yaml: {key} {field} {e[field]} does not exist")

tagged, pending = set(), set()
for spec in (HERE / "specs").glob("*.spec.ts"):
    text = spec.read_text()
    # Top-level chunks start at column 0. A test.fixme(true, ...) in a describe's own body (before its
    # first nested test) makes every test in it pending; inside a single test it makes only that one.
    for chunk in re.split(r"\n(?=test\.describe\(|test\()", text):
        parts = re.split(r"\n(?=[ \t]+test\()", chunk)
        header_fixme = "test.fixme(true" in parts[0] and chunk.startswith("test.describe(")
        for part in parts:
            ids = set(re.findall(r"@(KC-\d+-\d+)", part))
            for i in sorted(ids):
                if i not in by_id:
                    integrity.append(f"{spec.name}: @{i} is not an AC id")
                elif by_id[i]["security"] and "@security" not in part and "@security" not in parts[0]:
                    integrity.append(f"{spec.name}: a test for security AC {i} is not tagged @security")
            (pending if header_fixme or "test.fixme(true" in part else tagged).update(ids)
pending -= tagged

stories = defaultdict(list)
for a in acs:
    stories[a["id"].rsplit("-", 1)[0]].append(a)

def state(a):
    if a["id"] in tagged:
        return "pw"
    if a["id"] in evidence:
        return "ev"
    if a["id"] in pending:
        return "pending"
    # a deferred criterion (later phase, by decision) is not a gap: counted with the manual ones
    return "manual" if a["harness"] == "MAN" or a.get("deferred") else "none"

required = set(sys.argv[1:])
missing = []
print(f"{'story':7s} {'ACs':>3s} {'pw':>3s} {'ev':>3s} {'pend':>4s} {'man':>3s} {'none':>4s}")
for s in sorted(stories, key=lambda k: int(k.split("-")[1])):
    states = [state(a) for a in stories[s]]
    c = {k: states.count(k) for k in ("pw", "ev", "pending", "manual", "none")}
    if any(c[k] for k in ("pw", "ev", "pending")) or s in required:
        print(f"{s:7s} {len(states):3d} {c['pw']:3d} {c['ev']:3d} {c['pending']:4d} {c['manual']:3d} {c['none']:4d}")
    if s in required:
        missing += [a["id"] for a, st in zip(stories[s], states) if st in ("none", "pending")]
total = len(acs)
covered = sum(state(a) in ("pw", "ev") for a in acs)
print(f"\n{covered}/{total} ACs have a test or evidence ({len(tagged)} Playwright-tagged, {len(evidence)} evidence entries)")
if missing:
    print("required stories with untested ACs: " + ", ".join(missing))
for line in integrity:
    print("  INTEGRITY " + line)
sys.exit(1 if missing or integrity else 0)
