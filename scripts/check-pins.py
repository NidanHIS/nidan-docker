#!/usr/bin/env python3
"""Supply-chain pins (backlog KC-46-01): images by tag AND digest, no moving refs.

  scripts/check-pins.py                 fail on any violation not in security/unpinned.baseline
  scripts/check-pins.py --all           list every violation (the pinning punch list)
  scripts/check-pins.py --write-baseline  record today's violations (review the diff before committing)

Checks docker-compose.yml (production) and every Dockerfile* under this repo:
  * compose image (with ${VAR:-default} resolved to the default): needs name:tag@sha256:<64 hex>,
    and the tag must not be a moving ref (latest, next, stable, main, master, dev, develop, nidan,
    *-dev*, local, N.x, or no version digit at all such as alpine)
  * Dockerfile FROM (not a build stage): same rule
  * git clone of a branch, or of a ref set from an ARG whose default is not a version tag / commit
  * yarn/npm add of a registry package without an exact version (local ./x.tgz is KC-46-03)
The baseline is a ratchet: entries that are fixed must be removed (the check fails on stale ones).
"""
import re
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
BASELINE = ROOT / "security" / "unpinned.baseline"
MOVING = re.compile(r"^(latest|next|stable|main|master|dev|develop|nidan|local|edge|nightly)$|-dev\b|\bdev-", re.I)
DIGEST = re.compile(r"@sha256:[0-9a-f]{64}$")
VERSION_REF = re.compile(r"^(v?\d+(\.\d+)+([-.][\w.]+)?|[0-9a-f]{40})$")


def resolve(value):
    return re.sub(r"\$\{[A-Za-z_][A-Za-z0-9_]*:?-([^}]*)\}", r"\1", str(value))


def image_problem(ref):
    ref = resolve(ref).strip()
    if "$" in ref:
        return f"{ref}: variable with no default"
    name = ref.split("@")[0]
    last = name.rsplit("/", 1)[-1]
    tag = last.split(":", 1)[1] if ":" in last else "latest"
    if MOVING.search(tag) or re.search(r"\.x\b", tag) or not re.search(r"\d", tag):
        return f"{ref}: moving tag {tag!r}"
    if not DIGEST.search(ref):
        return f"{ref}: no @sha256 digest"
    return None


def compose_violations(path):
    out = []
    doc = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    for name, svc in sorted((doc.get("services") or {}).items()):
        if svc.get("image"):
            p = image_problem(svc["image"])
            if p:
                out.append(f"{path.relative_to(ROOT)}: service {name}: {p}")
    return out


def dockerfile_violations(path):
    out, args, stages = [], {}, set()
    rel = path.relative_to(ROOT)
    text = re.sub(r"\\\n", " ", path.read_text(encoding="utf-8"))
    for line in text.splitlines():
        s = line.strip()
        m = re.match(r"ARG\s+(\w+)=(\S+)", s, re.I)
        if m:
            args[m.group(1)] = m.group(2).strip('"')
        m = re.match(r"FROM\s+(?:--platform=\S+\s+)?(\S+)(?:\s+AS\s+(\S+))?", s, re.I)
        if m:
            ref = re.sub(r"\$\{?(\w+)\}?", lambda a: args.get(a.group(1), a.group(0)), m.group(1))
            if ref not in stages and ref.lower() != "scratch":
                p = image_problem(ref)
                if p:
                    out.append(f"{rel}: FROM {p}")
            if m.group(2):
                stages.add(m.group(2))
        for cm in re.finditer(r"git clone\b[^;&|]*?(?:--branch|-b)\s+(\S+)", s):
            ref = cm.group(1).strip('"')
            vm = re.fullmatch(r"\$\{?(\w+)\}?", ref)
            val = args.get(vm.group(1), "") if vm else ref
            if not VERSION_REF.match(val):
                out.append(f"{rel}: git clone of moving ref {ref} (= {val or 'unset'})")
        for am in re.finditer(r"\b(?:yarn add|npm (?:i|install))\s+([^;&|]+)", s):
            for spec in am.group(1).split():
                if spec.startswith("-") or spec.startswith((".", "/")) or spec.endswith(".tgz"):
                    continue
                spec = re.sub(r"\$\{(\w+):-([^}]*)\}", r"\2", spec)
                ver = spec.rsplit("@", 1)[1] if spec.count("@") > (1 if spec.startswith("@") else 0) else ""
                if not re.fullmatch(r"\d+\.\d+\.\d+([-+][\w.]+)?", ver):
                    out.append(f"{rel}: package {spec} without an exact version")
    return out


def violations():
    out = compose_violations(ROOT / "docker-compose.yml")
    for f in sorted(ROOT.rglob("Dockerfile*")):
        if "node_modules" not in f.parts and f.is_file():
            out += dockerfile_violations(f)
    return out


def main(argv):
    found = violations()
    if "--write-baseline" in argv:
        BASELINE.parent.mkdir(exist_ok=True)
        BASELINE.write_text("# Unpinned images/refs accepted for now (KC-46-01). Fix and delete lines; never add.\n"
                            + "".join(v + "\n" for v in sorted(set(found))), encoding="utf-8")
        print(f"baseline: {len(set(found))} violation(s)")
        return 0
    if "--all" in argv:
        for v in found:
            print(v)
        print(f"{len(found)} violation(s)")
        return 1 if found else 0
    base = set()
    if BASELINE.exists():
        base = {l for l in BASELINE.read_text(encoding="utf-8").splitlines() if l and not l.startswith("#")}
    new = sorted(set(found) - base)
    stale = sorted(base - set(found))
    print(f"pins: {len(found)} violation(s), {len(found) - len(new)} in baseline, {len(new)} new, {len(stale)} fixed")
    for v in new:
        print("  NEW   " + v)
    for v in stale:
        print("  FIXED (remove from security/unpinned.baseline) " + v)
    return 1 if new or stale else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
