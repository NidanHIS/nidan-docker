# Runbook — central login incidents

**Story:** KC-50 · **Tool:** `scripts/incident.py` (dry run unless `--execute`)

Every step below changes who can sign in to the whole hospital. Write down the time and the reason for
each action as you go: that record is part of the evidence to keep.

---

## Who may act — fill this in

**A deployment with blanks here has not completed KC-50.**

| | |
|---|---|
| **May run `revoke-all` / rotate keys** | `FILL IN — named people, with role` |
| **Must be told within 1 hour** | `FILL IN — hospital director / IT lead` |
| **Holds the automation client secret** (`KC_AUTOMATION_CLIENT_ID/SECRET`) | `FILL IN` |
| **Second operator for OTP resets** (never reset your own) | `FILL IN` |
| **Reviewed on** | `FILL IN — re-confirm every 6 months` |

The automation client `nidan-incident` (keycloak/realm/master/automation.yaml) is a master-realm client with
client-credentials only (operators have OTP, which cannot be scripted). Its secret `KC_AUTOMATION_CLIENT_SECRET`
is in `.env.local` (keycloak-config-cli sets it from there); keep an operator copy beside the backup key, and
rotate it by hand after any suspected server compromise.

## The three tools

| Situation | Command | Effect |
|---|---|---|
| Sessions may be stolen (cookie or token leak, stolen laptop with an open session) | `scripts/incident.py revoke-all` | Everyone signs in again within ~2 min: new gateway cookie secret, realm not-before, every Keycloak session logged out (Odoo ends its own sessions too) |
| Realm signing key may be exposed | `scripts/incident.py rotate-realm-keys`, then after 12 h `… --retire-old` | New key signs from now; retiring makes old tokens fail everywhere |
| A machine client secret leaked | `scripts/incident.py rotate-client-secret cis` (or `odoo-sync`, `portal-sync`, …) | Old secret refused at once; the consumer restarts with the new one |

Run each once without `--execute` and read the steps before running it for real. `.env.local` is backed
up next to itself (`.env.local.bak-<time>`, mode 0600) before any change.

## Lost admin OTP device

1. A **second** operator (never the owner) signs in to the admin console through the SSH tunnel.
2. Users → the operator → Credentials → delete the OTP credential; set "Configure OTP" as a required
   action.
3. The owner signs in and enrolls the new device the same day, with the second operator present: until she
   does, whoever holds her password could enrol a device instead. `scripts/realm-audit.py` (nightly,
   docs/realm-audit.cron) lists every admin still without OTP.
4. Evidence: admin event for the credential removal (Keycloak keeps admin events 365 days), who asked,
   who acted.

## Compromised operator account

1. Disable the account in the master realm (Users → disable). Sessions end at the next token refresh;
   to be sure, sign the user out (Users → Sessions → sign out).
2. `scripts/incident.py rotate-client-secret` for every client the operator could have read the secret
   of, and rotate `KC_AUTOMATION_CLIENT_SECRET` in the master realm by hand.
3. If the operator could reach realm keys: `rotate-realm-keys`, then `--retire-old` after 12 h.
4. If staff sessions could have been taken: `revoke-all`.
5. Evidence: master-realm admin events and login events for the account, the gateway access log for its
   source IPs, the time each step ran.

## Leaked backup key

Follow `docs/RUNBOOK-backup-key-management.md`. In addition, because backups contain the Keycloak
database (password hashes, OTP secrets, client secrets):

1. Assume every client secret is known: `rotate-client-secret` for each client.
2. Force OTP re-enrolment for operators (required action "Configure OTP").
3. `rotate-realm-keys`, then `--retire-old` after 12 h.
4. Staff passwords are hashed; still, ask staff to change passwords at their next sign-in (Keycloak:
   required action "Update Password" for all users) if the backups were outside hospital control.

## Restoring Keycloak from a backup

A restored Keycloak is as it was at backup time: anyone disabled, moved out of a job or removed since then is back.
Before staff can reach it:

1. Restore (`scripts/volume-backup.sh restore <dir> keycloak-db-data`) and start Keycloak, gateway still closed.
2. `KC_TOKEN_FILE=/secure/token scripts/keycloak-reapply-after-restore.py /srv/nidan-audit/keycloak --since <backup time>`
   (read the plan), then the same with `--apply`. Backup time = the backup directory's timestamp.
3. Run `scripts/realm-audit.py`; open the gateway. Rehearsed by `scripts/keycloak-restore-drill.sh` (nightly in CI).

