#!/usr/bin/env python3
"""No script writes a plaintext database dump or puts a DB password on argv (backlog KC-28-01, KC-28-04).

  scripts/check-backup-safety.py

Every git-tracked shell/Python file that runs pg_dump / pg_dumpall / mysqldump / mariadb-dump / mongodump must
pipe that dump through `age` in the same command, and must not pass a password on the command line
(--password=..., -p<value>, PGPASSWORD=... inline is fine only via the environment of the container).
Files listed in security/plaintext-dumps.pending (with a reason) are reported, not failed: they await removal.
"""
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PENDING = ROOT / "security" / "plaintext-dumps.pending"
DUMP = re.compile(r"\b(pg_dump|pg_dumpall|mysqldump|mariadb-dump|mongodump)\b")
ARGV_PASSWORD = re.compile(r"(--password=\S|\s-p[^\s-]\S*)")


def logical_lines(text):
    """Join backslash-continued lines so a pipeline is checked as one command."""
    buf, start = "", 1
    for n, line in enumerate(text.splitlines(), 1):
        if not buf:
            start = n
        if line.rstrip().endswith("\\"):
            buf += line.rstrip()[:-1] + " "
            continue
        yield start, buf + line
        buf = ""
    if buf:
        yield start, buf


def problems(files):
    out = []
    for f in files:
        try:
            text = f.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        for n, line in logical_lines(text):
            code = line.split("#", 1)[0] if f.suffix != ".py" else line
            if not DUMP.search(code):
                continue
            rel = f.relative_to(ROOT)
            if not re.search(r"\|\s*age\b", code):
                out.append(f"{rel}:{n}: database dump not piped through age (plaintext PHI on disk)")
            if ARGV_PASSWORD.search(code):
                out.append(f"{rel}:{n}: database password on the command line (visible in ps)")
    return out


def tracked():
    names = subprocess.run(["git", "ls-files", "--cached", "--others", "--exclude-standard"], cwd=ROOT,
                           capture_output=True, text=True).stdout.split()
    return [ROOT / n for n in names if n.endswith((".sh", ".py")) and "/test" not in "/" + n and not
            Path(n).name.startswith("test_") and Path(n).name != Path(__file__).name]


def main():
    pending = {l.split()[0] for l in PENDING.read_text().splitlines() if l.strip() and not l.startswith("#")} \
        if PENDING.exists() else set()
    found = problems(tracked())
    new = [p for p in found if p.split(":", 1)[0] not in pending]
    for p in found:
        print(("  PENDING " if p.split(":", 1)[0] in pending else "  FAIL    ") + p)
    print(f"backup safety: {len(found)} finding(s), {len(new)} not pending")
    return 1 if new else 0


if __name__ == "__main__":
    sys.exit(main())
