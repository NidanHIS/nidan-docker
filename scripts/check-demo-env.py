#!/usr/bin/env python3
"""The tracked demo .env may only hold reviewed public demo values (backlog KC-05-07).

  scripts/check-demo-env.py            check the working-tree .env (CI)
  scripts/check-demo-env.py --staged   check the staged .env (pre-commit hook)
  scripts/check-demo-env.py --write    re-approve the current .env (after review; commit the result)

security/demo-env.allow lists KEY and a sha256 prefix of each approved value, never the value.
Fails on a new key, a changed value, or a tracked .env.local. Real site values belong in the
git-ignored .env.local (scripts/generate-secrets.sh --fresh-install).
"""
import hashlib
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
ALLOW = ROOT / "security" / "demo-env.allow"


def parse(text):
    out = {}
    for line in text.splitlines():
        k, sep, v = line.partition("=")
        if sep and k.strip() and not k.lstrip().startswith("#"):
            out[k.strip()] = v.strip()
    return out


def digest(value):
    return hashlib.sha256(value.encode("utf-8")).hexdigest()[:16]


def read_allow():
    allow = {}
    if ALLOW.exists():
        for line in ALLOW.read_text(encoding="utf-8").splitlines():
            if line.strip() and not line.startswith("#"):
                k, h = line.split()
                allow[k] = h
    return allow


def problems(env, allow, tracked_local=False):
    out = [f"{k}: not on the demo allow-list" for k in env if k not in allow]
    out += [f"{k}: value changed since it was approved" for k, v in env.items() if k in allow and digest(v) != allow[k]]
    if tracked_local:
        out.append(".env.local is tracked by git: it holds site secrets (git rm --cached .env.local)")
    return out


def git(*args):
    return subprocess.run(["git", *args], cwd=ROOT, capture_output=True, text=True)


def main(argv):
    if "--staged" in argv:
        staged = git("show", ":.env")
        text = staged.stdout if staged.returncode == 0 else ""
    else:
        text = (ROOT / ".env").read_text(encoding="utf-8") if (ROOT / ".env").exists() else ""
    env = parse(text)
    if "--write" in argv:
        ALLOW.write_text("# KEY sha256[:16] of each approved public demo value (scripts/check-demo-env.py --write)\n"
                         + "".join(f"{k} {digest(v)}\n" for k, v in sorted(env.items())), encoding="utf-8")
        print(f"approved {len(env)} demo values -> {ALLOW.relative_to(ROOT)}")
        return 0
    tracked_local = bool(git("ls-files", "--", ".env.local", ":(glob)**/.env.local").stdout.strip())
    found = problems(env, read_allow(), tracked_local)
    print(f"demo .env: {len(env)} values, {len(found)} problem(s)")
    for p in found:
        print("  " + p)
    if found:
        print("put site values in .env.local; if a new value really is a public demo value, review it and run "
              "scripts/check-demo-env.py --write", file=sys.stderr)
    return 1 if found else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
