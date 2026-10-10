"""Self-test for check-artifacts.py: `python3 -I scripts/test_check_artifacts.py`."""
import importlib.util
import tempfile
from pathlib import Path

spec = importlib.util.spec_from_file_location("ca", Path(__file__).resolve().parent / "check-artifacts.py")
ca = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ca)

with tempfile.TemporaryDirectory() as d:
    ca.ROOT = Path(d)
    (ca.ROOT / "a.omod").write_bytes(b"one")
    (ca.ROOT / "b.tgz").write_bytes(b"two")
    good = {"a.omod": {"sha256": ca.sha256("a.omod")}, "b.tgz": {"sha256": ca.sha256("b.tgz")}}
    assert ca.check(["a.omod", "b.tgz"], good) == []
    (ca.ROOT / "a.omod").write_bytes(b"tampered")
    assert any("sha256 differs" in p for p in ca.check(["a.omod", "b.tgz"], good))
    assert any("not in the manifest" in p for p in ca.check(["a.omod", "b.tgz", "c.jar"], good))
    assert any("no longer committed" in p for p in ca.check(["b.tgz"], good))
    e = ca.entry("b.tgz", {"source_commit": "abc123"})
    assert e["source_commit"] == "abc123" and e["source"] == "not in monorepo"  # hand-filled fields kept
assert "openmrs-esm-login-app-10.0.0-nidan.tgz".startswith(ca.AUTH_RELEVANT)
print("check-artifacts: OK")
