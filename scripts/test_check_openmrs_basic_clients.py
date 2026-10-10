"""Self-test for check-openmrs-basic-clients.py: `python3 -I scripts/test_check_openmrs_basic_clients.py`."""
import importlib.util
import subprocess
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location("cob", HERE / "check-openmrs-basic-clients.py")
cob = importlib.util.module_from_spec(spec)
spec.loader.exec_module(cob)

with tempfile.TemporaryDirectory() as d:
    root = Path(d)
    repo = root / "odoo_19_addons"
    (repo / "mod/tests").mkdir(parents=True)
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    (repo / "mod/client.py").write_text("pw = env['OPENMRS_PASSWORD']\n")
    (repo / "mod/tests/test_x.py").write_text("OPENMRS_PASSWORD = 'x'\n")     # tests skipped
    (repo / "mod/README.md").write_text("set NIDAN_SYNC_PASSWORD\n")           # docs skipped
    (repo / ".gitignore").write_text("local.env\n")
    (repo / "local.env").write_text("OPENMRS_PASSWORD=real\n")                 # ignored: skipped
    (repo / "mod/new.py").write_text("requests.get(u, auth=(u, cfg['openmrs.password']))\n")
    cob.ALLOW = root / "allow"
    cob.ALLOW.write_text("odoo_19_addons/mod/client.py  known\nodoo_19_addons/mod/gone.py  fixed already\n"
                         "nidan-middleware/x.java  repo not checked out\n")
    new, stale, missing, found = cob.check(root)
    assert found == {"odoo_19_addons/mod/client.py", "odoo_19_addons/mod/new.py"}, found
    assert new == ["odoo_19_addons/mod/new.py"], new          # a new Basic client fails
    assert stale == ["odoo_19_addons/mod/gone.py"], stale     # ratchet
    assert "nidan-middleware" in missing                      # not checked out: reported, not stale
print("check-openmrs-basic-clients: OK")
