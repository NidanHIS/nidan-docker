"""Self-test for check-theme.py: `python3 -I scripts/test_check_theme.py`."""
import importlib.util
import tempfile
from pathlib import Path

spec = importlib.util.spec_from_file_location("ct", Path(__file__).resolve().parent / "check-theme.py")
ct = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ct)

assert ct.problems() == [], ct.problems()
with tempfile.TemporaryDirectory() as d:
    t = Path(d) / "x/login"
    (t / "messages").mkdir(parents=True)
    (t / "login.ftl").write_text('<p>${msg("a")}</p>\n<p>${message.summary?no_esc}</p>\n<#noautoesc>x</#noautoesc>\n')
    (t / "messages/messages_en.properties").write_text("invalidUserMessage=Wrong.\naccountDisabledMessage=Account is disabled.\n"
                                                       "x=<b>bold</b>\n")
    (t / "messages/messages_ne.properties").write_text("invalidUserMessage=Galat.\n")
    out = ct.problems(Path(d))
    assert sum("unescaped output" in p for p in out) == 2, out
    assert any("x contains HTML" in p for p in out), out
    assert any("accountDisabledMessage differs" in p for p in out), out
    assert any("en/ne keys differ" in p for p in out), out
    assert any("does not say which Keycloak version" in p for p in out), out
    (t / "login.ftl").write_text('<#-- Copied from keycloak.v2 login.ftl, Keycloak 26.7.0 -->\n<p>${msg("a")}</p>\n')
    out = ct.problems(Path(d), keycloak_version="26.8.0")
    assert any("copied from Keycloak 26.7.0 but compose runs 26.8.0" in p for p in out), out
    (t / "login.ftl").write_text('<#-- Copied from keycloak.v2 login.ftl, Keycloak 26.8.0 -->\n<p>${msg("a")}</p>\n')
    assert not any("login.ftl" in p for p in ct.problems(Path(d), keycloak_version="26.8.0"))
print("check-theme: OK")
