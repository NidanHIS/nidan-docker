"""Self-test for openmrs-weak-passwords.py: `python3 -I scripts/test_openmrs_weak_passwords.py`."""
import importlib.util
from pathlib import Path

spec = importlib.util.spec_from_file_location("wp", Path(__file__).resolve().parent / "openmrs-weak-passwords.py")
wp = importlib.util.module_from_spec(spec)
spec.loader.exec_module(wp)

w = wp.words()
assert w[0] == "Admin123" and "password123" in w and len(w) == len(set(w))
q = wp.sql(["Admin123", "it's", "back\\slash"])
assert "SHA2(CONCAT('Admin123', salt), 512)" in q
assert "'it''s'" in q                       # quotes doubled, cannot end the literal
assert "'back\\\\slash'" in q               # backslash escaped for MariaDB
assert q.count(" OR ") == 2 and "retired = 0" in q and q.startswith("SELECT COALESCE(username, system_id)")
print("openmrs-weak-passwords self-test passed")
