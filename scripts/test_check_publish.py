"""Self-test for check-publish.py: `python3 -I scripts/test_check_publish.py`."""
import importlib.util
import tempfile
from pathlib import Path

import yaml

spec = importlib.util.spec_from_file_location("cp", Path(__file__).resolve().parent / "check-publish.py")
cp = importlib.util.module_from_spec(spec)
spec.loader.exec_module(cp)


def wf(on, push="true", step_if=None, run=None):
    step = {"run": run} if run else {"uses": "docker/build-push-action@v6", "with": {"push": push}}
    if step_if:
        step["if"] = step_if
    return {"on": on, "jobs": {"b": {"steps": [step]}}}


def bad(doc):
    g = cp.publishes(doc)
    return cp.bad_triggers(doc, g) if g else []

assert bad(wf({"push": {"tags": ["v*"]}, "release": {"types": ["published"]}})) == []
assert bad(wf({"push": {"branches": ["main"]}})) == ["push (branches)"]
assert bad(wf({"push": {"branches": ["main"]}}, push="false")) == []                       # builds, never pushes
assert bad(wf({"push": {"branches": ["dev"]}}, run="docker push x/y:dev")) == ["push (branches)"]
assert bad(wf({"pull_request": None, "push": {"tags": ["v*"]}},
              push="${{ github.event_name != 'pull_request' }}")) == []
assert bad(wf({"push": {"branches": ["main"], "tags": ["v*"]}, "workflow_dispatch": None},
              push="${{ startsWith(github.ref, 'refs/tags/v') }}")) == []                   # tag ref only
assert bad(wf({"push": {"branches": ["main"]}, "pull_request": None},
              step_if="github.event_name != 'push' || needs.c.outputs.x == 'true'")) == ["push (branches)", "pull_request"]
assert bad(wf("workflow_dispatch")) == ["workflow_dispatch"]
assert bad(yaml.safe_load("on: [push]\njobs: {b: {steps: [{run: docker push a/b}]}}")) == ["push (branches)"]

with tempfile.TemporaryDirectory() as d:
    root = Path(d)
    w = root / "Repo With Space" / ".github" / "workflows"
    w.mkdir(parents=True)
    (w / "build.yml").write_text(yaml.safe_dump(wf({"push": {"branches": ["main"]}})))
    pending = root / "publish.pending"
    assert cp.main(["x", str(root)], pending) == 1
    pending.write_text("Repo With Space/.github/workflows/build.yml  reason\n")
    assert cp.main(["x", str(root)], pending) == 0
    (w / "build.yml").write_text(yaml.safe_dump(wf({"push": {"tags": ["v*"]}})))
    assert cp.main(["x", str(root)], pending) == 1                                         # stale entry
    pending.write_text("Gone/.github/workflows/x.yml  not checked out here\n")
    assert cp.main(["x", str(root)], pending) == 0                                         # absent: skipped
assert cp.main(["x"]) == 0
print("check-publish: OK")
