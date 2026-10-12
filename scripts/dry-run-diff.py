#!/usr/bin/env python3
"""Dry-run permission diff + orphan report before a site cutover (backlog KC-26, spec §9.2 step 5).

  scripts/dry-run-diff.py EXPORT.json WORKBOOK_DIR

Inputs: the bootstrap export, and in WORKBOOK_DIR the reviewed link-table.csv (rows with
decision=link) plus jobs.csv, confirmed by the hospital:
  username, job, superuser, accept_loss
  job          a /staff/<job> group in keycloak/realm/** (blank = no job yet: everything is lost)
  superuser    "yes" routes the person to the operator-managed app-superusers group: their superuser
               roles held today are kept there, never via a job (KC-26-04)
  accept_loss  "app:role; app:role" losses the hospital has signed off
For every linked person: roles held today (export, per linked app account) minus roles the job
grants (Keycloak composites expanded; Odoo implied groups and OpenMRS inherited roles counted as
granted, from the export: the site's own data). Any loss not accepted -> listed, exit 1 (KC-26-01).
Clean -> exit 0 (KC-26-02). A job granting a superuser role (KC-57; superuser per the site's data,
bootstrap-export.py superusers) is a problem. Roles people hold today that no job grants are listed
as orphans and do not fail the run (KC-26-03).
"""
import csv
import importlib.util
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent


def load(name, file):
    spec = importlib.util.spec_from_file_location(name, HERE / file)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


realm = load("check_realm", "check-realm.py")
be = load("bootstrap_export", "bootstrap-export.py")


def job_grants(export, mappings=None):
    """{job name: {app: {roles}}} with app-side inheritance (Odoo implied, OpenMRS inherited) from the export."""
    out = {}
    for path, app, role in realm.job_role_mappings() if mappings is None else mappings:
        out.setdefault(path.split("/")[2], {}).setdefault(app, set()).update(
            be.includes_closure(export.get(app) or {}, role))
    return out


def read_csv(path):
    return list(csv.DictReader(open(path, newline="", encoding="utf-8"))) if Path(path).exists() else []


def diff(export, link_rows, job_rows, grants):
    held = {}  # (username, app) -> {roles} today
    for r in link_rows:
        if (r.get("decision") or "").strip().lower() != "link":
            continue
        app, uid = r["app"], str(r["app_user_id"])
        roles = {m["role"] for m in (export.get(app) or {}).get("memberships", []) if str(m["user_id"]) == uid}
        held.setdefault((r["username"].lower(), app), set()).update(roles)
    jobs = {r["username"].strip().lower(): r for r in job_rows}
    supers = {app: be.superusers(app, d) for app, d in export.items() if "error" not in d}
    losses, notes = [], []
    for job, apps in sorted(grants.items()):
        for app, roles in sorted(apps.items()):
            for role in sorted(roles & supers.get(app, set())):
                notes.append(f"job {job!r} grants the {app} superuser role {role!r}; only app-superusers may (KC-57)")
    for (user, app), roles in sorted(held.items()):
        j = jobs.get(user, {})
        job = (j.get("job") or "").strip()
        if job and job not in grants and f"{user}: job {job!r} is not a /staff group in the realm" not in notes:
            notes.append(f"{user}: job {job!r} is not a /staff group in the realm")
        granted = grants.get(job, {}).get(app, set())
        accepted = {a.strip() for a in (j.get("accept_loss") or "").split(";") if a.strip()}
        superuser = (j.get("superuser") or "").strip().lower() == "yes"
        for role in sorted(roles - granted):
            is_super = role in supers.get(app, set())
            if is_super and superuser:
                continue  # kept through app-superusers, an operator decision
            if f"{app}:{role}" in accepted:
                continue
            why = ("superuser today: set superuser=yes (app-superusers) or accept the loss" if is_super
                   else "not granted by job " + (repr(job) if job else "(none assigned)"))
            losses.append(f"{user} loses {app}:{role!r} ({why})")
    orphans = []
    for app, d in sorted(export.items()):
        granted = set().union(*(apps.get(app, set()) for apps in grants.values()))
        for role, n in sorted((d.get("roles") or {}).items()):
            if n and role not in granted and role not in supers.get(app, set()):
                orphans.append(f"{app}:{role!r} ({n} users) is granted by no job")
    return losses, notes, orphans


def main(argv, mappings=None):
    if len(argv) != 3:
        print(__doc__)
        return 2
    export = json.loads(Path(argv[1]).read_text(encoding="utf-8"))
    wb = Path(argv[2])
    failed = [app for app, d in export.items() if "error" in d]
    losses, notes, orphans = diff(export, read_csv(wb / "link-table.csv"), read_csv(wb / "jobs.csv"),
                                  job_grants(export, mappings))
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
