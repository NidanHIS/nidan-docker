"""KC-31 self-test: dicom-firewall.sh against a fake iptables, and preflight's DICOM rule.
Run: python3 -I scripts/test_dicom_lockdown.py"""
import importlib.util
import os
import subprocess
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
FAKE = r'''#!/usr/bin/env python3
import sys, json, os
db = os.environ["FAKE_RULES"]
rules = json.load(open(db)) if os.path.exists(db) else ["-A DOCKER-USER -j RETURN", "-A DOCKER-USER -s 10.9.9.9/32 -j DROP"]
a = sys.argv[1:]
if a[:2] == ["-S", "DOCKER-USER"]:
    print("-N DOCKER-USER"); print("\n".join(rules)); sys.exit(0)
if a[:2] == ["-I", "DOCKER-USER"]:
    rules.insert(int(a[2]) - 1, "-A DOCKER-USER " + " ".join(a[3:]))
elif a[:2] == ["-D", "DOCKER-USER"]:
    rules.remove("-A DOCKER-USER " + " ".join(a[2:]))
json.dump(rules, open(db, "w"))
'''

with tempfile.TemporaryDirectory() as d:
    d = Path(d)
    ipt = d / "iptables"
    ipt.write_text(FAKE)
    ipt.chmod(0o755)
    env = dict(os.environ, IPTABLES=str(ipt), FAKE_RULES=str(d / "rules.json"), DICOM_MODALITY_CIDR="192.168.10.0/24")

    def run(**extra):
        r = subprocess.run(["sh", str(HERE / "dicom-firewall.sh"), "--apply"], env=dict(env, **extra),
                           capture_output=True, text=True)
        assert r.returncode == 0, r.stderr
        import json
        return json.loads((d / "rules.json").read_text())

    first = run()
    ours = [r for r in first if "nidan-dicom" in r]
    assert len(ours) == 2 and "ACCEPT" in ours[0] and "-s 192.168.10.0/24" in ours[0] and ours[1].endswith("-j DROP")
    assert "-A DOCKER-USER -s 10.9.9.9/32 -j DROP" in first and "-A DOCKER-USER -j RETURN" in first
    again = run()
    assert again == first, "re-run must be idempotent"
    moved = run(DICOM_MODALITY_CIDR="10.20.0.0/16")
    ours = [r for r in moved if "nidan-dicom" in r]
    assert len(ours) == 2 and "10.20.0.0/16" in ours[0] and "192.168.10.0/24" not in " ".join(moved)

spec = importlib.util.spec_from_file_location("pf", HERE / "preflight.py")
pf = importlib.util.module_from_spec(spec)
spec.loader.exec_module(pf)
svc = lambda **env: {"orthanc": {"environment": env}}
assert len(pf.dicom_problems(svc(ORTHANC__DICOM_ALWAYS_ALLOW_STORE="true", ORTHANC__DICOM_MODALITIES="{}"))) == 2
ok = '{"CT1": {"AET": "CT1", "Host": "192.168.10.21", "Port": 104, "AllowStore": true}}'
assert pf.dicom_problems(svc(ORTHANC__DICOM_ALWAYS_ALLOW_STORE="false", ORTHANC__DICOM_MODALITIES=ok)) == []
assert any("needs AET and Host" in p for p in pf.dicom_problems(
    svc(ORTHANC__DICOM_ALWAYS_ALLOW_STORE="false", ORTHANC__DICOM_MODALITIES='{"X": {"Port": 104}}')))
assert pf.dicom_problems({}) == []
print("dicom lockdown: OK")
