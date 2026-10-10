"""Self-test for check-docs.py: `python3 -I scripts/test_check_docs.py`."""
import importlib.util
import tempfile
from pathlib import Path

spec = importlib.util.spec_from_file_location("cd", Path(__file__).resolve().parent / "check-docs.py")
cd = importlib.util.module_from_spec(spec)
spec.loader.exec_module(cd)

with tempfile.TemporaryDirectory() as d:
    root = Path(d)
    (root / "scripts").mkdir()
    (root / "docker-compose.yml").write_text("x: ${A:-1} ${B} ${C:?set C}\n")
    (root / "env.template").write_text("A=1\n# B=2\n" + "".join(f"{v}=\n" for v in cd.SPEC_VARS[1:]))
    (root / "README.md").write_text("`scripts/known.py`\n")
    for n in ("known.py", "new.sh", "test_known.py"):
        (root / "scripts" / n).write_text("")
    got = cd.problems(root)
    assert got == ["C: used in compose, missing from env.template",
                   f"{cd.SPEC_VARS[0]}: spec section 3 switch missing from env.template", "scripts/new.sh: not described in README.md (## Scripts)"], got
assert cd.problems() == [], cd.problems()
print("check-docs: OK")
