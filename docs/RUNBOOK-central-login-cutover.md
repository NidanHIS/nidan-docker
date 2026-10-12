# Runbook — moving a live hospital to central login

**Stories:** KC-25, KC-26, KC-36, KC-55 · **Rehearse first** on a staging copy of the site's databases
(KC-36-04): one ward, end to end, then re-run the dry-run diff. Do not run this on a live site you
have not rehearsed.

Central login changes how every member of staff signs in. Each app only lets a Keycloak user into an
account that is **linked by Keycloak id**, so nothing below links people by name: the hospital
reviews and signs every link.

The workbook folder holds staff names. Keep it **outside any git repository** (the scripts refuse
otherwise) and delete it when the cutover is finished.

---

## 0. Before the day

| Check | How |
|---|---|
| Ports 8069 and 9443 open to staff networks | the gateway now serves Odoo on `https://<host>:8069` (Odoo itself is no longer published) and Keycloak's login on `https://<host>:9443/auth` (D1; set `KEYCLOAK_PUBLIC_URL` only if browsers use another name or port) |
| POS terminals and bookmarks use `https://<host>:8069` | plain `http://<host>:8069` gets a redirect to https; terminals must trust the gateway certificate |
| Nobody relies on the old direct ports or plain http | Orthanc 8042, CIS 8081, OIS 8082, OpenELIS 8083/8085, Superset 8088 and the DHIS2 app 5003 are no longer published (KC-02); port 80 now only redirects to https and serves certificate renewal (KC-47). Use the gateway: `/orthanc-container/`, `/superset/`, `/dhis2/`, `/openelis/`. Operators reach a service from the host with `docker compose exec` |
| Shared service secrets are 32+ characters | `scripts/preflight.py` lists any shorter one; with `NIDAN_DEPLOYMENT=production` CIS, OIS and Odoo refuse to start with them (KC-51-04). `scripts/generate-secrets.sh` makes 40 |
| Login rate limit fits the site | Keycloak login/token: `KC_LOGIN_RATE_PER_MIN` per client IP (default 10). If every user reaches the gateway through one NAT address, exempt it in `KC_LOGIN_RATE_EXEMPT` |
| Per-site secrets generated | `scripts/generate-secrets.sh --fresh-install` |
| OpenMRS `authentication` module removed from the distro | `scripts/nidan-auth-check.py` shows no KC-34-01 line |
| oauth2login OMOD built into the image | `openmrs/README.md` § Central login |
| Odoo: `auth_oidc` + `nidan_keycloak_sso` installed, `python-jose` in the image | Odoo Apps |
| Every name staff type to reach the gateway is in `GATEWAY_CERT_SANS` | others get 421 |
| OpenELIS DB superuser password differs from the app's (existing sites, KC-48-02) | `docker exec -it nidan-openelis-db psql -U postgres -c '\password postgres'` and type `OPENELIS_DB_ROOT_PASSWORD` at the prompt (never on a command line) |
| Read-only reporting login in place (KC-48) | `scripts/reporting-db-users.py --apply`, then `REPORTING_DB_USER` / `REPORTING_DB_PASSWORD` |

## 1. Export what each app has today (read-only)

```bash
scripts/bootstrap-export.py export /secure/cutover/export.json
scripts/bootstrap-export.py workbook /secure/cutover/export.json /secure/cutover
```

The export runs inside read-only transactions. The workbook gives `roles.csv`, `link-table.csv` and
`reserved-usernames.txt`.

## 2. Reserve the local-only names

```bash
scripts/reserved-usernames.py --site-file /secure/cutover/reserved-usernames.txt
```

Put the printed `KC_USERNAME_PATTERN=` line in `.env.local`. Keycloak will then refuse to create a
user with any of those names, so nobody can claim an unlinked local account.

## 3. Decide the jobs and prove nobody loses access

1. Write `/secure/cutover/jobs.csv` with the hospital: `username, job, superuser, accept_loss`.
2. Run the diff. It must exit 0:

   ```bash
   scripts/dry-run-diff.py /secure/cutover/export.json /secure/cutover   # role data from the export (this site)
   ```

   A loss is fixed by changing the job, or signed off in `accept_loss`. Superuser roles held today
   are kept only by `superuser=yes` (the operator-managed `app-superusers` group), never by a job.

## 4. Start Keycloak and load the realm

Central login is its own compose file on top of the plain stack. In `.env.local`, add it and keep every login gate
off until step 7:

```bash
COMPOSE_FILE=docker-compose.yml:keycloak.docker-compose.yml
NIDAN_SSO_ENABLED=false            # the Keycloak file's default is on
OPENMRS_AUTH_MODE=basic            # its default is jwt
DHIS2_AUTH_PROVIDER=env_file       # its default is oidc
```

From now on every `docker compose ...` includes Keycloak. Then:

```bash
NIDAN_DEPLOYMENT=production scripts/preflight.py          # must pass
docker compose up -d keycloak-db keycloak oauth2-proxy keycloak-admin-proxy
# admin console: ssh -L 8180:127.0.0.1:8180 <stack host>, then http://127.0.0.1:8180/auth/admin/ (keycloak-admin-proxy,
# KC-07-04; Keycloak's own port is on the internal auth-net, where Docker serves no published port)
docker compose run --rm keycloak-config-cli
docker compose up -d gateway       # joins auth-net (fixed 172.31.250.10) and opens :9443; gates stay off
```

The first import runs as the bootstrap admin and creates the automation clients
(`keycloak/realm/master/automation.yaml`). Then, once:

1. Hospital-admin scope (KC-09), with an operator admin token from the tunnel kept in a file:
   `KC_TOKEN_FILE=/secure/cutover/token scripts/keycloak-admin-permissions.py` (read the plan), then the same with
   `--apply`. Re-run whenever a job group is added. Without it the Staff page can do nothing (Keycloak refuses).
2. `.env.local`: `KEYCLOAK_CONFIG_CLI_CLIENT=nidan-config-cli`, `KEYCLOAK_CONFIG_CLI_GRANT=client_credentials`;
   run `keycloak-config-cli` again: it must succeed without the bootstrap admin.
3. Start the role sync: `docker compose up -d keycloak-role-sync`. It reads the roles this site's
   OpenMRS, Odoo and OpenELIS really have (their own APIs) into Keycloak at start and every hour; a role it made that
   the app dropped is deleted once nobody holds it. Run it now, after an app change, and before mapping jobs:
   `docker compose exec keycloak-role-sync python3 -I /app/role_sync.py --once` (exit 1 = read its log: an app it
   could not read, or a staff job holding a superuser role). Needs `KC_ROLE_SYNC_CLIENT_SECRET`,
   `KC_ROLE_SYNC_ADMIN_SECRET` and `NIDAN_ROLE_SYNC_SECRET` in `.env.local` (generate-secrets.sh), the apps
   restarted with them, and nidancore with the role-sync account.
4. Create each operator's own master-realm account with OTP (admin console, through the SSH tunnel).
5. Remove the bootstrap admin user in the admin console (master realm → Users). From now on nobody
   signs in to the master realm with a password alone.

On a site whose Keycloak database comes from the old compose (it imported `nidan-realm` and the OpenELIS realm at
start), remove those realms after a backup (KC-04-02): `scripts/volume-backup.sh backup <dir> keycloak-db-data`, then
`KC_TOKEN_FILE=... scripts/legacy-realms.py` (lists them) and `scripts/legacy-realms.py --delete <realm>` for each.
Realms must be exactly master and nidan.

## 5. Create the ward's Keycloak users and fill in their ids

Create each person (Staff page on `:9443`, or `kcadm.sh` through the operator's SSH tunnel). In
`link-table.csv`, mark every account the hospital confirms `decision=link`, then fill in the ids
(exact username match, nothing is overwritten or guessed):

```bash
# operator admin token from the tunnel (127.0.0.1:8180), kept in a file, never on the command line
KC_TOKEN_FILE=/secure/cutover/token scripts/fetch-keycloak-ids.py /secure/cutover
```

Rows it reports `NOT FOUND` or `AMBIGUOUS` stay blank: fix them by hand before step 6.

## 6. Write the link keys

```bash
scripts/apply-links.py plan  /secure/cutover/export.json /secure/cutover
#   read /secure/cutover/link-plan.sql, then:
scripts/apply-links.py apply /secure/cutover/export.json /secure/cutover <sha256 printed by plan>
```

`apply` runs only the plan you read, one transaction per app, then checks every link. Any
`NOT LINKED` line (account renamed since the export, id already used) must be resolved before
going on.

## 7. Switch central login on

```bash
# .env.local: remove the three lines set in step 4 (NIDAN_SSO_ENABLED=false, OPENMRS_AUTH_MODE=basic,
# DHIS2_AUTH_PROVIDER=env_file): the Keycloak file's defaults are on, jwt (OpenMRS stops accepting Basic once
# oauth2login is on; CIS and Odoo then call it with client-credentials tokens) and oidc
scripts/nidan-auth-check.py                                   # must report 0 contradictions
docker compose up -d gateway openmrs-backend openmrs-frontend openelis superset nidan-dhis2 odoo nidan-cis   # openmrs-frontend: loads config-sso.json (KC-35)
docker compose exec odoo odoo -u nidan_keycloak_sso --stop-after-init   # syncs the login provider
```

Smoke test: `tests/e2e-auth` project `sso-on`, and one real person per job signs in to each app they
should reach and is refused by one they should not.

Machine accounts (KC-32-05, KC-59-01). nidancore creates them at OpenMRS start, each with only its own role:
`nidan-sync` (Nidan Sync; CIS), `nidan-odoo` (Nidan Odoo Read; Odoo), `nidan-portal` (Nidan Portal; portal-sync).
It never removes roles. On a site where `nidan-sync` already existed, the OpenMRS log says
"nidan-sync also holds [System Developer]": remove that role from it (Legacy admin → Manage Users), then
re-run the sync smoke test (one patient + one order through CIS).

## 8. Lock the ward's old passwords (only after step 7 works for that ward)

```bash
scripts/apply-links.py lock-plan  /secure/cutover/export.json /secure/cutover --only ward-a.txt
#   read /secure/cutover/password-lock.sql, then:
scripts/apply-links.py lock-apply /secure/cutover/export.json /secure/cutover <sha256 printed by lock-plan>
```

Repeat steps 5, 6 and 8 ward by ward. After each ward, re-run the dry-run diff (step 3).

## Rolling back

Before step 8: `scripts/disable-sso.py` (check), then `scripts/disable-sso.py --apply` sets
`NIDAN_SSO_ENABLED=false` in `.env.local` and reverts the other step-7 switches (per-app overrides,
`OPENMRS_AUTH_MODE=basic`, `DHIS2_AUTH_PROVIDER=env_file`); then `docker compose up -d`. Every app is back on its own login, so the
script refuses while any app's local admin still has a demo/default password and prints a reset slip for it (KC-29-03).
The link keys written in step 6 do no harm.

After step 8 the locked accounts have no usable local password: rolling back means resetting those
passwords in each app. Lock a ward only once its staff have signed in centrally.

## Known gaps (do not improvise around them)

- OpenMRS O3 login/logout through Keycloak (KC-35) needs an `openmrs-frontend` image built from this repo's
  `openmrs/frontend` (esm-login `10.0.0-nidan.2` tarball, `config-sso.json`, `startup.sh`). The published
  `trigonaltechnology/openmrs-frontend:latest` predates it: with it, O3 log out leaves the Keycloak session open.
  Also remove the `authentication` module first (KC-34-01).
- Superset and the DHIS2 app need no link key (they refuse local accounts / store no users).
