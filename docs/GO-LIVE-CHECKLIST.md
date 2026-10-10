# Go-live checklist — one per site (backlog KC-49-02)

**Nothing reaches a hospital until this is signed** (spec ship rule 8). Keep the signed copy with the site's
records; do not commit filled-in copies (they name people and the site).

| # | Check | Evidence | Done by / date |
|---|---|---|---|
| 1 | `scripts/release-gate.py` passes for the release being installed (every @security AC green) | gate output | |
| 2 | `NIDAN_DEPLOYMENT=production scripts/preflight.py` passes with the site's `.env.local` (KC-05-03) | output | |
| 3 | `scripts/nidan-auth-check.py` reports 0 contradictions | output | |
| 4 | Leaked credentials rotated (KC-52, `secret-rotation-register`): every row closed for this site | register | |
| 5 | LAN scan from another machine: only the gateway ports answer (KC-02-05) | nmap output | |
| 6 | Backup: nightly job installed (`docs/backup-nightly.cron`), one backup taken, **restored on another host** (KC-28) | restore log | |
| 7 | Incident runbook walked through with the people in its "who may act" table (KC-50) | names | |
| 8 | DICOM: each modality can send, an unknown AE title is refused (KC-31) | test notes | |
| 9 | Threat model updated; independent review / pen test of this release; every High+ finding closed or risk-accepted in writing (KC-49-01) | report | |
| 10 | Cutover rehearsed on a staging copy (docs/RUNBOOK-central-login-cutover.md) | dry-run diff output | |

**Signed (hospital):** ______________________ **Signed (Trigonal):** ______________________ **Date:** __________
