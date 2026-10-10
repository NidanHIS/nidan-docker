"""Self-test for check-secret-compare.py: `python3 -I scripts/test_check_secret_compare.py`."""
import importlib.util
import tempfile
from pathlib import Path

spec = importlib.util.spec_from_file_location("csc", Path(__file__).resolve().parent / "check-secret-compare.py")
csc = importlib.util.module_from_spec(spec)
spec.loader.exec_module(csc)

PY = '''
def a(provided, expected_secret, token):
    if provided != expected_secret:          # flagged
        return 1
    if expected_secret is None:              # fine
        return 2
    if hmac.compare_digest(provided, expected_secret):   # fine
        return 3
    if expected_secret == '':                # fine (emptiness check)
        return 4
    # secret-compare: ok (test fixture says why)
    if token == provided:                    # accepted by the line above
        return 5
'''
JAVA = '''
class A {
  boolean x(String sharedSecret, String s) { return sharedSecret.equals(s); }        // flagged
  boolean y(String s, String apiKey) { return !s.equals(apiKey); }                    // flagged
  boolean z(String sharedSecret, String s) { return SharedSecrets.matches(sharedSecret, s); }
}
'''
with tempfile.TemporaryDirectory() as d:
    root = Path(d)
    (root / "nidan-middleware").mkdir()
    (root / "nidan-middleware" / "a.py").write_text(PY)
    (root / "nidan-middleware" / "A.java").write_text(JAVA)
    (root / "nidan-middleware" / "tests").mkdir()
    (root / "nidan-middleware" / "tests" / "t.py").write_text("if secret == x: pass\n")  # test code skipped
    hits = list(csc.scan(root))
lines = sorted(h.split(":")[0].split("/")[-1] + ":" + h.split(":")[1] for h in hits)
assert lines == ["A.java:3", "A.java:4", "a.py:3"], lines
print("check-secret-compare self-test: OK")
