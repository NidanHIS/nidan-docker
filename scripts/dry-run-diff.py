#!/usr/bin/env python3
"""Dry-run permission diff + orphan report before a site cutover (backlog KC-26, spec §9.2 step 5).

  scripts/dry-run-diff.py EXPORT.json WORKBOOK_DIR [MONOREPO_ROOT] [--site keycloak/realm/sites/<site>]

Inputs: the bootstrap export, and in WORKBOOK_DIR the reviewed link-table.csv (rows with
decision=link) plus jobs.csv, confirmed by the hospital:
  username, job, superuser, accept_loss
  job          a /staff/<job> group in keycloak/realm/** (blank = no job yet: everything is lost)
  superuser    "yes" routes the person to the operator-managed app-superusers group: their superuser
               roles held today are kept there, never via a job (KC-26-04)
  accept_loss  "app:role; app:role" losses the hospital has signed off
For every linked person: roles held today (export, per linked app account) minus roles the job
grants (Keycloak composites expanded; Odoo implied groups and OpenMRS inherited roles counted as
granted). Any loss not accepted -> listed, exit 1 (KC-26-01). Clean -> exit 0 (KC-26-02).
Local roles not in the catalog are listed as orphans and do not fail the run (KC-26-03).
"""
import csv
import importlib.util
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
_spec = importlib.util.spec_from_file_location("check_catalog", HERE / "check-catalog.py")
cc = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(cc)


def job_grants(root):
    """{job name: {app: {roles}}} with app-side inheritance (Odoo implied, OpenMRS inherited) added."""
    # ponytail: Odoo core-module implications (not in this repo) are not modelled; they surface as
    # losses to accept or add to the job. Read them from the site's res_groups_implied_rel if noisy.
    _found, implied = cc.odoo_xml(root)
    inherits = cc.openmrs_inheritance(root)
    closure = {"odoo": implied, "openmrs": inherits}
    out = {}
    for path, app, role in cc.job_role_mappings():
        job = path.split("/")[2]
        todo, seen = [role], out.setdefault(job, {}).setdefault(app, set())
        while todo:
            r = todo.pop()
            if r not in seen:
                seen.add(r)
                todo.extend(closure.get(app, {}).get(r, ()))
    return out


def read_csv(path):
    return list(csv.DictReader(open(path, newline="", encoding="utf-8"))) if Path(path).exists() else []


def diff(export, link_rows, job_rows, grants, truth, site=None):
    held = {}  # (username, app) -> {roles} today
    for r in link_rows:
        if (r.get("decision") or "").strip().lower() != "link":
            continue
        app, uid = r["app"], str(r["app_user_id"])
        roles = {m["role"] for m in (export.get(app) or {}).get("memberships", []) if str(m["user_id"]) == uid}
        held.setdefault((r["username"].lower(), app), set()).update(roles)
    jobs = {r["username"].strip().lower(): r for r in job_rows}
    losses, notes = [], []
    for (user, app), roles in sorted(held.items()):
        j = jobs.get(user, {})
        job = (j.get("job") or "").strip()
        if job and job not in grants:
            notes.append(f"{user}: job {job!r} is not a /staff group in the realm")
        granted = grants.get(job, {}).get(app, set())
        accepted = {a.strip() for a in (j.get("accept_loss") or "").split(";") if a.strip()}
        superuser = (j.get("superuser") or "").strip().lower() == "yes"
        for role in sorted(roles - granted):
            is_super = (truth.get(app, {}).get(role) or {}).get("superuser", False)
            if is_super and superuser:
                continue  # kept through app-superusers, an operator decision
            if f"{app}:{role}" in accepted:
                continue
            why = ("superuser today: set superuser=yes (app-superusers) or accept the loss" if is_super
                   else "not granted by job " + (repr(job) if job else "(none assigned)"))
            losses.append(f"{user} loses {app}:{role!r} ({why})")
    orphans = []
    for app, d in sorted(export.items()):
        known = set(truth.get(app, {})) | (site or {}).get(app, set())
        for role, n in sorted((d.get("roles") or {}).items()):
            if role not in known:
                orphans.append(f"{app}:{role!r} ({n} users) not in the catalog")
    return losses, notes, orphans


def main(argv):
    site = None
    if "--site" in argv:
        i = argv.index("--site")
        site = cc.site_roles(argv[i + 1])
        argv = argv[:i] + argv[i + 2:]
    if len(argv) not in (3, 4):
        print(__doc__)
        return 2
    export = json.loads(Path(argv[1]).read_text(encoding="utf-8"))
    wb = Path(argv[2])
    root = Path(argv[3]).resolve() if len(argv) == 4 else HERE.parent.parent
    failed = [app for app, d in export.items() if "error" in d]
    losses, notes, orphans = diff(export, read_csv(wb / "link-table.csv"), read_csv(wb / "jobs.csv"),
                                  job_grants(root), cc.derived(root), site)
    for title, items in (("UNACCEPTED PERMISSION LOSS", losses), ("PROBLEMS", notes),
                         ("ORPHAN ROLES (report only)", orphans)):
        print(f"{title}: {len(items)}")
        for i in items:
            print("  " + i)
    if failed:
        print("EXPORT FAILED for: " + ", ".join(failed) + " (cannot prove no loss)")
    return 1 if losses or notes or failed else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
