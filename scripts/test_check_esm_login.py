"""Self-test for check-esm-login.py: `python3 -I scripts/test_check_esm_login.py`."""
import importlib.util
import io
import json
import shutil
import tarfile
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location("ce", HERE / "check-esm-login.py")
ce = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ce)

PATCHED = ("function endSingleSignOn(config) { window.location.assign(interpolateUrl(config.provider.logoutUrl)); }\n"
           "if (config.provider.type === 'oauth2' && isLoginEnabled && endSingleSignOn(config)) { return; }\n"
           "performLogout()")


def tgz(path, logout_src, dist_js):
    with tarfile.open(path, "w:gz") as tar:
        for name, text in (("package/src/redirect-logout/redirect-logout.component.tsx", logout_src),
                           ("package/src/config-schema.ts", "logoutUrl: {}" if "logoutUrl" in logout_src else ""),
                           ("package/dist/main.js", dist_js)):
            data = text.encode()
            info = tarfile.TarInfo(name)
            info.size = len(data)
            tar.addfile(info, io.BytesIO(data))


with tempfile.TemporaryDirectory() as d:
    front = Path(d)
    for f in ("spa-assemble-config.json", "Dockerfile", "startup.sh", "config-sso.json"):
        shutil.copy(HERE.parent / "openmrs" / "frontend" / f, front / f)
    name = json.loads((front / "spa-assemble-config.json").read_text())["frontendModules"]["@openmrs/esm-login-app"]
    (front / "packages").mkdir()

    tgz(front / "packages" / name, PATCHED, 'e.provider.logoutUrl&&window.location.assign(i(e.provider.logoutUrl))')
    assert ce.problems(front) == [], ce.problems(front)

    # upstream esm-login: source and bundle without the patch
    tgz(front / "packages" / name, "// do nothing, do not redirect", "void 0")
    p = ce.problems(front)
    assert len(p) == 4 and all(name in x for x in p), p

    # patched source but a stale bundle
    tgz(front / "packages" / name, PATCHED, "void 0")
    assert [x for x in ce.problems(front) if "dist/" in x], ce.problems(front)

    # the earlier patch: REST logout first, then leave (the SPA could race to its own login)
    tgz(front / "packages" / name, "performLogout().then(() => endSingleSignOn(config)) {});\n" + PATCHED.split("\n")[0],
        "logoutUrl")
    assert any("before any REST logout" in x for x in ce.problems(front)), ce.problems(front)

    # SSO config pointing log out anywhere else, or not shipped
    tgz(front / "packages" / name, PATCHED, "logoutUrl")
    good = (front / "config-sso.json").read_text()
    (front / "config-sso.json").write_text(good.replace("/oauth2logout", "/logout"))
    assert any("config-sso.json" in x for x in ce.problems(front))
    (front / "config-sso.json").write_text(good)
    (front / "startup.sh").write_text("exec nginx")
    assert any("not shipped" in x for x in ce.problems(front))
    shutil.copy(HERE.parent / "openmrs" / "frontend" / "startup.sh", front / "startup.sh")

    # assembled from something other than the tarball the Dockerfile installs
    cfg = json.loads((front / "spa-assemble-config.json").read_text())
    cfg["frontendModules"]["@openmrs/esm-login-app"] = "next"
    (front / "spa-assemble-config.json").write_text(json.dumps(cfg))
    assert "not our patched tarball" in ce.problems(front)[0]

# the real unpatched tarball still in the repo fails
old = HERE.parent / "openmrs" / "frontend" / "packages" / "openmrs-esm-login-app-10.0.0-nidan.tgz"
if old.exists():
    with tempfile.TemporaryDirectory() as d:
        front = Path(d)
        for f in ("Dockerfile", "startup.sh", "config-sso.json"):
            shutil.copy(HERE.parent / "openmrs" / "frontend" / f, front / f)
        (front / "packages").mkdir()
        shutil.copy(old, front / "packages" / old.name)
        (front / "spa-assemble-config.json").write_text(json.dumps({"frontendModules": {"@openmrs/esm-login-app": old.name}}))
        (front / "Dockerfile").write_text(f"COPY packages/{old.name} .\nRUN yarn add ./{old.name}\nCOPY config-sso.json x\n")
        assert len(ce.problems(front)) == 4, ce.problems(front)
print("check-esm-login self-test passed")
