"""Self-test for check-demo-env.py: `python3 -I scripts/test_check_demo_env.py`."""
import importlib.util
from pathlib import Path

spec = importlib.util.spec_from_file_location("cde", Path(__file__).resolve().parent / "check-demo-env.py")
cde = importlib.util.module_from_spec(spec)
spec.loader.exec_module(cde)

env = cde.parse("# c\nA=sample\nB=\nC = x=y \n")
assert env == {"A": "sample", "B": "", "C": "x=y"}, env
allow = {k: cde.digest(v) for k, v in env.items()}
assert cde.problems(env, allow) == []
assert any("not on the demo allow-list" in p for p in cde.problems({**env, "NEW_SECRET": "x"}, allow))
assert any("value changed" in p for p in cde.problems({**env, "A": "real-site-password"}, allow))
assert any(".env.local is tracked" in p for p in cde.problems(env, allow, tracked_local=True))
# the allow-list never contains a value
assert "sample" not in "".join(f"{k} {h}" for k, h in allow.items())
# the real tracked .env matches its allow-list
assert cde.problems(cde.parse((cde.ROOT / ".env").read_text()), cde.read_allow()) == []
print("check-demo-env: OK")
