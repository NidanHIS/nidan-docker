#!/usr/bin/env python3
"""Images are published only from a git tag (backlog KC-46-02).

  scripts/check-publish.py [MONOREPO_ROOT]       (default: the directory above nidan-docker)

A GitHub workflow that pushes an image (docker/build-push-action with `push:` not false, or `docker push`)
may be triggered only by `push: tags:` and/or `release:`. A branch push, pull_request, schedule or
workflow_dispatch (which runs on any branch) can publish an image that never passed the suite on a tag.
Pushes whose `if:` / `push:` condition excludes an event (event_name checks, tag-ref checks) do not count
for it. Workflows listed in security/publish.pending ("path  reason", path relative to the monorepo root) are reported, not failed,
until the release process is moved to tags; a listed workflow that is fixed fails as stale. Listed files
that are not checked out here (single-repo CI) are skipped.
"""
import re
import sys
from pathlib import Path

import yaml

HERE = Path(__file__).resolve().parent
PENDING = HERE.parent / "security" / "publish.pending"
TAG_ONLY = {"release"}


def workflows(root):
    pats = (".github/workflows/*.y*ml", "*/.github/workflows/*.y*ml", "*/*/.github/workflows/*.y*ml")
    return sorted({p for pat in pats for p in Path(root).glob(pat) if "node_modules" not in p.parts})


def publishes(doc):
    """None if no step pushes an image, else the conditions guarding the pushes (job/step `if`, `push:` value)."""
    guards = None
    for job in (doc.get("jobs") or {}).values():
        for step in (job or {}).get("steps") or []:
            uses, run = str(step.get("uses", "")), str(step.get("run", ""))
            push = str((step.get("with") or {}).get("push", "false"))
            if (uses.startswith("docker/build-push-action") and push.lower() != "false") or \
                    re.search(r"\bdocker\s+(image\s+)?push\b|\bbuildx\b.*--push\b", run):
                guards = (guards or []) + [" ".join(str(x) for x in ((job or {}).get("if", ""), step.get("if", ""), push))]
    return guards


def excluded(guard, event):
    """True if this push condition can never be true for *event* (or only ever on a tag ref)."""
    if "||" not in guard and (re.search(r"startsWith\(\s*github\.ref\s*,\s*'refs/tags/", guard)
                              or "github.ref_type == 'tag'" in guard):
        return True
    if "||" in guard:  # a disjunction can still be true for this event (e.g. != 'push' || files changed)
        return False
    if re.search(rf"github\.event_name\s*!=\s*'{event}'", guard):
        return True
    only = re.findall(r"github\.event_name\s*==\s*'([a-z_]+)'", guard)
    return bool(only) and event not in only


def bad_triggers(doc, guards=()):
    on = doc.get("on", doc.get(True))  # PyYAML reads the bare key `on` as True
    if isinstance(on, str):
        on = {on: None}
    elif isinstance(on, list):
        on = {k: None for k in on}
    out = []
    for event, cfg in (on or {}).items():
        if event in TAG_ONLY:
            continue
        if guards and all(excluded(g, event) for g in guards):
            continue  # every push is skipped for this event, e.g. push: ${{ github.event_name != 'pull_request' }}
        if event == "push" and isinstance(cfg, dict) and cfg.get("tags") and not cfg.get("branches"):
            continue
        out.append(event + (" (branches)" if event == "push" else ""))
    return out


def findings(root):
    root = Path(root).resolve()
    out = {}
    for f in workflows(root):
        try:
            doc = yaml.safe_load(f.read_text(encoding="utf-8")) or {}
        except yaml.YAMLError:
            continue
        guards = publishes(doc) if isinstance(doc, dict) else None
        if guards:
            bad = bad_triggers(doc, guards)
            if bad:
                out[f.relative_to(root).as_posix()] = bad
    return out


def main(argv, pending_file=PENDING):
    root = Path(argv[1] if len(argv) > 1 else HERE.parent.parent).resolve()
    # "path  reason": two spaces end the path (vendored trees have spaces in their names)
    pending = {l.split("  ")[0].strip() for l in pending_file.read_text().splitlines() if l.strip() and not l.startswith("#")} \
        if pending_file.exists() else set()
    found = findings(root)
    new = sorted(set(found) - pending)
    stale = sorted(p for p in pending - set(found) if (root / p).exists())
    for path, bad in sorted(found.items()):
        print(("  PENDING " if path in pending else "  FAIL    ") + f"{path}: publishes an image on {', '.join(bad)}")
    for path in stale:
        print(f"  STALE   {path}: publishes from tags only now, remove it from security/publish.pending")
    print(f"publish: {len(found)} workflow(s) publish from a non-tag trigger, {len(new)} not pending, {len(stale)} stale")
    return 1 if new or stale else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
