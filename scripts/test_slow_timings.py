"""Self-test for slow-timings.py: `python3 -I scripts/test_slow_timings.py`."""
import importlib.util
from pathlib import Path

spec = importlib.util.spec_from_file_location("st", Path(__file__).resolve().parent / "slow-timings.py")
st = importlib.util.module_from_spec(spec)
spec.loader.exec_module(st)

report = {"suites": [{"specs": [
    {"title": "refresh and disable @KC-10-07 @KC-11-01 @security @slow", "tests": [{"results": [{"status": "passed", "duration": 131000}]}]},
    {"title": "fast @KC-10-04", "tests": [{"results": [{"status": "passed", "duration": 900}]}]}],
    "suites": [{"specs": [{"title": "outage @KC-11-03 @slow", "tests": [{"results": [{"status": "failed", "duration": 245500}]}]}]}]}]}
assert st.rows(report) == [("KC-10-07 KC-11-01", "passed", 131.0, "refresh and disable"),
                           ("KC-11-03", "failed", 245.5, "outage")], st.rows(report)
print("slow-timings: OK")
