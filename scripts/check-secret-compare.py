#!/usr/bin/env python3
"""Flag non-constant-time comparisons of secrets (backlog KC-51-03).

  scripts/check-secret-compare.py [MONOREPO_ROOT]

A comparison is flagged when one side names a secret (secret, password, passwd, token, api_key,
apikey, admin_key, webhook) and it uses:
  * Python: ==, != (but not `is None`, empty-string or None checks)
  * Java:   .equals(...) / .equalsIgnoreCase(...)
Use hmac.compare_digest / MessageDigest.isEqual / SharedSecrets.matches instead.
A `secret-compare: ok (<reason>)` comment on the line or the line above accepts it (state the reason).
Test code is skipped. Exit 0 = clean, 1 = findings.
"""
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPOS = ["nidan-middleware", "odoo_19_addons", "nidan_odoo_extra_addons", "odoo_payment_gateway",
         "nidan-dhis2-integration", "nidan-patient-portal", "OpenELIS-Global-2/src/main/java/org/openelisglobal/nidan"]
NAME = r"[A-Za-z0-9_.]*(?:secret|password|passwd|token|api_?key|admin_?key|webhook)[A-Za-z0-9_]*"
PY_CMP = re.compile(rf"(?i)\b({NAME})\b\s*(==|!=)\s*(?!None\b|''|\"\"|\[\])\S|\S\s*(==|!=)\s*\b({NAME})\b(?!\s*\()")
JAVA_CMP = re.compile(rf"(?i)\b{NAME}\b\s*\)?\s*\.equals(IgnoreCase)?\(|\.equals(IgnoreCase)?\(\s*{NAME}\b")
SKIP_DIRS = ("/test/", "/tests/", "/target/", "/node_modules/", "/.git/", "/build/", "/dist/", "/.venv/", "/venv/",
             "/site-packages/")
NOT_SECRETS = re.compile(r"(?i)(token_type|tokens?\b\s*(==|!=)\s*\d|password_policy|secret_name|_count|len\()")


def scan(root):
    for repo in REPOS:
        base = root / repo
        if not base.exists():
            continue
        for f in base.rglob("*"):
            p = f.as_posix()
            if f.suffix not in (".py", ".java") or any(s in p for s in SKIP_DIRS) or f.name.startswith("test_"):
                continue
            lines = f.read_text(encoding="utf-8", errors="ignore").splitlines()
            for n, line in enumerate(lines, 1):
                code = line.split("#", 1)[0] if f.suffix == ".py" else line.split("//", 1)[0]
                accepted = "secret-compare: ok" in line or (n > 1 and "secret-compare: ok" in lines[n - 2])
                if accepted or NOT_SECRETS.search(code):
                    continue
                rx = PY_CMP if f.suffix == ".py" else JAVA_CMP
                if rx.search(code):
                    yield f"{f.relative_to(root)}:{n}: {line.strip()[:110]}"


def main():
    root = Path(sys.argv[1]).resolve() if len(sys.argv) > 1 else HERE.parent.parent
    hits = list(scan(root))
    print(f"{len(hits)} non-constant-time secret comparison(s)")
    for h in hits:
        print("  " + h)
    return 1 if hits else 0


if __name__ == "__main__":
    sys.exit(main())
