"""Self-test for check-pins.py: `python3 -I scripts/test_check_pins.py`."""
import importlib.util
import tempfile
from pathlib import Path

spec = importlib.util.spec_from_file_location("cp", Path(__file__).resolve().parent / "check-pins.py")
cp = importlib.util.module_from_spec(spec)
spec.loader.exec_module(cp)
D = "@sha256:" + "a" * 64

assert cp.image_problem("postgres:15.8" + D) is None
assert cp.image_problem("${X:-ghcr.io/o/app:1.2.3" + D + "}") is None
for bad in ["postgres:15.8", "postgres" + D, "app:latest" + D, "app:next", "app:2.8.x-amazoncorretto-21" + D,
            "nginx:alpine" + D, "core:2.8.1-dev-x" + D, "nidan-cis:local", "${IMG}"]:
    assert cp.image_problem(bad), bad

with tempfile.TemporaryDirectory() as d:
    cp.ROOT = Path(d)
    f = Path(d) / "Dockerfile"
    f.write_text(f"""ARG REF=main
ARG TAG=v1.4.0
FROM node:22.11.0-alpine{D} AS dev
FROM dev
RUN git clone --depth 1 --branch "${{REF}}" https://x/y /c && \\
    git clone -b ${{TAG}} https://x/z /z
RUN yarn add ./local-1.0.0.tgz openmrs@${{V:-next}} left-pad@1.3.0 @scope/pkg@2.0.0 react
FROM nginx:1.27
""")
    got = cp.dockerfile_violations(f)
    assert any("moving ref" in g and "= main" in g for g in got), got
    assert not any("TAG" in g for g in got), got
    assert any("openmrs@next" in g for g in got) and any("react" in g for g in got), got
    assert not any(x in " ".join(got) for x in ("left-pad", "@scope/pkg", "local-1.0.0", "FROM dev")), got
    assert sum("FROM" in g for g in got) == 1 and any("nginx:1.27" in g for g in got), got
print("check-pins: OK")
