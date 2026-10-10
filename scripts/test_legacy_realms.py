"""Self-test for legacy-realms.py (fake admin API): `python3 -I scripts/test_legacy_realms.py`."""
import importlib.util
import os
import tempfile
from pathlib import Path

spec = importlib.util.spec_from_file_location("lr", Path(__file__).resolve().parent / "legacy-realms.py")
lr = importlib.util.module_from_spec(spec)
spec.loader.exec_module(lr)

realms = [{"realm": "master"}, {"realm": "nidan"}, {"realm": "nidan-realm"}, {"realm": "openelis"}]
calls = []
def fake(base, token, method, path):
    calls.append((method, path))
    if method == "DELETE":
        realms[:] = [r for r in realms if "/" + r["realm"] != path]
        return None
    return list(realms)

with tempfile.NamedTemporaryFile("w", delete=False) as t:
    t.write("token")
os.environ["KC_TOKEN_FILE"] = t.name
assert lr.legacy(realms) == ["nidan-realm", "openelis"]
assert lr.main(["x"], call=fake) == 1 and not any(m == "DELETE" for m, _ in calls)        # list only
for keep in ("master", "nidan"):
    assert lr.main(["x", "--delete", keep], call=fake) == 2                                # never master or nidan
assert lr.main(["x", "--delete", "unknown"], call=fake) == 2                               # only a listed legacy realm
assert not any(m == "DELETE" for m, _ in calls)
assert lr.main(["x", "--delete", "nidan-realm"], call=fake) == 1                          # one left (openelis)
assert ("DELETE", "/nidan-realm") in calls and [r["realm"] for r in realms] == ["master", "nidan", "openelis"]
assert lr.main(["x", "--delete", "openelis"], call=fake) == 0
os.unlink(t.name)
print("legacy-realms self-test passed")
