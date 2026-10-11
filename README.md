# NidanEHR Docker Distribution

This repository contains the containerized architecture for **NidanEHR**, orchestrating OpenMRS 3.0, Odoo, OpenELIS, Orthanc, and Keycloak behind a central Gateway.

## Architecture

| Service | internal Host | URL Path | Description |
|---|---|---|---|
| **Gateway** | `gateway` | `/` | Nginx Reverse Proxy & SSL Termination |
| **OpenMRS Frontend** | `openmrs-frontend` | `/openmrs` | O3 SPA Assets (static) |
| **OpenMRS Backend** | `openmrs-backend` | `/openmrs/ws` | Java API (Tomcat) |
| **Odoo 19** | `odoo` | `/odoo` | ERP System |
| **OpenELIS Backend** | `openelis` | `/openelis` | Lab System Backend |
| **OpenELIS FHIR** | `openelis-fhir` | `/openelis-fhir` | Lab System FHIR API |
| **OpenELIS Frontend** | `openelis-frontend` | `/openelis-frontend` | Lab System Frontend |
| **Orthanc** | `orthanc` | `/orthanc-container` | PACS Server with OHIF Viewer |
| **Keycloak** | `keycloak` | `/auth` | Identity Management |

## Directory Structure

*   `openmrs/`: **The Core Distro**. Contains the Maven project `pom.xml` to build the WAR and the Dockerfile that layers it into Tomcat.
*   `odoo/`: Odoo Docker context with custom `addons/`.
*   `proxy/`: Nginx configuration for the main gateway.
*   `keycloak/`: Realm imports and themes.
*   `_backup_legacy/`: Archived legacy Bahmni-based configuration (can be removed).
*   `docker-compose.yml`: Main orchestration file.

## Build & Deployment Instructions

### Prerequisites
1. **To evaluate the stack**, nothing to do. `.env` is committed with demo
   credentials so `docker compose up -d` works straight from a clone. Every
   value in it is public.

2. **For a real deployment**, do not edit `.env`. It is tracked, so adding it to
   `.gitignore` will not stop your credentials being committed. Put overrides in
   `.env.local`, which is untracked:
   ```bash
   cp env.template .env.local
   scripts/generate-secrets.sh --fresh-install   # unique random value for every CHANGEME secret (0600 file)
   # fill by hand what it lists: HMIS/DHIS2 credentials, keystore passwords, BACKUP_AGE_RECIPIENT
   echo 'NIDAN_DEPLOYMENT=production' >> .env.local
   COMPOSE_ENV_FILES=.env,.env.local scripts/preflight.py && \
   COMPOSE_ENV_FILES=.env,.env.local docker compose up -d
   ```
   `scripts/preflight.py` refuses a production start when any secret in the
   effective config is a value shipped in `.env`/`env.template`, a placeholder,
   a well-known default or shorter than 16 characters — a published default is
   not safer than a leaked one. In production mode the middleware webhooks
   (CIS/OIS) and Superset also refuse demo secrets themselves. Compose stops
   with an error if any required secret is unset.
   **Fresh production install:** after `generate-secrets.sh --fresh-install` (it also writes `KC_USERNAME_PATTERN`),
   `NIDAN_DEPLOYMENT=production scripts/preflight.py` still refuses until three site steps are done: register the
   DICOM modalities (`ORTHANC_DICOM_MODALITIES`, `ORTHANC_DICOM_ALLOW_ANY_STORE=false`), create the read-only
   reporting login (`scripts/reporting-db-users.py --apply`, then `REPORTING_DB_USER/PASSWORD`), and keep Odoo off its
   DB superuser account. All three are described in `env.template` (checked on a scratch fresh install, 2026-10-09).

   **Upgrading a running site** that gains a new secret (e.g. `ORTHANC_VIEWER_PASSWORD`): run the same
   `scripts/generate-secrets.sh --fresh-install` against its existing `.env.local`. It only fills keys that
   are missing or still CHANGEME and never touches a value already there (verified 2026-10-09: 45 values
   unchanged, 1 added), so existing database passwords stay as they are.
   `--fresh-install` matters: DB passwords take effect only when the DB volumes
   are created, so do not regenerate them on a site that already has data.
   Rotating an existing deployment: see `docs/RUNBOOK-rotate-debezium-credential.md`.
   **OpenMRS admin password (KC-05-06):** on its next start after this upgrade, nidancore changes OpenMRS's stock
   `admin` / `Admin123` to `OPENMRS_ADMIN_PASSWORD` (only while it is still the stock one). Before upgrading, find
   anything still using admin/Admin123 against OpenMRS (Odoo Settings → Nidan connector username/password, scripts)
   and point it at the new value or at its own account.
   **Networks (KC-43):** databases, Kafka and ZooKeeper sit on the internal `data-net`, shared only with the
   services that use them; the gateway and the central-login services (`auth-net`) are not on it. On an upgraded
   site `docker compose up -d` recreates those containers on the new network (data volumes are kept). Check the
   layout with `python3 -I tests/e2e-auth/harness/net-probe.py`.
   **Ports and origins (2026-10-10):** the gateway publishes only 80, 443, 8443, 8069 and 9443 (plus DICOM 4242 on
   `DICOM_BIND_IP`). Orthanc 8042, CIS 8081, OIS 8082, OpenELIS 8083/8085, Superset 8088 and the DHIS2 app 5003 are
   no longer published (KC-02); port 80 only redirects to https and serves certificate renewal (KC-47). Odoo is
   served by the gateway on `https://<host>:8069` (app-odoo gate; its service APIs, DB manager and RPC are 404 there)
   - repoint POS terminals and bookmarks, and set `ODOO_WEB_BASE_URL` / `DASHBOARD_ODOO_URL` to https if `.env.local`
   pins them. Keycloak's login is on `https://<host>:9443/auth` (`KEYCLOAK_PUBLIC_URL` defaults to it); Odoo re-reads
   its provider settings at every start, so a moved origin needs only a restart. Open 8069 and 9443 in the firewall.
   **Secrets (KC-51-04):** with `NIDAN_DEPLOYMENT=production`, CIS, OIS and Odoo refuse to start with a shared secret
   under 32 characters or a demo value (`NIDAN_ODOO_SECRET`, `NIDAN_OIS_SECRET`, the webhook secrets,
   `DEAD_LETTER_ADMIN_PASSWORD`). Run `scripts/preflight.py` first: it names each short one.
   **CIS (KC-32):** Kafka retry is on by default for CIS (`CIS_INTEGRATION_KAFKA_RETRY_ATTEMPTS`, default 8), so a
   Keycloak restart does not dead-letter events. CIS reads OpenMRS with the OpenMRS app login (`OPENMRS_DB_USER` /
   `OPENMRS_DB_PASSWORD`, now required); the Debezium login is used only with `DEBEZIUM_ENABLED=true`. nidancore's
   "Nidan Sync" role gains the bed-management privileges visit sync needs (applied at OpenMRS start).
   **Login limits and staff:** Keycloak login/token requests are limited per client IP (`KC_LOGIN_RATE_PER_MIN`,
   default 10); if every user reaches the gateway through one NAT address, list it in `KC_LOGIN_RATE_EXEMPT`.
   Disabling someone on the Staff page now signs them out of every app first (open Odoo POS sessions close).
   **Own database logins (optional):** Keycloak, CIS/OIS and Orthanc can stop using their database's superuser:
   `KEYCLOAK_DB_APP_PASSWORD=... scripts/app-db-roles.py --apply keycloak` (likewise `integration`, `orthanc`) creates
   a non-superuser owner role, then set the printed `*_DB_APP_USER` / `*_DB_APP_PASSWORD` in `.env.local` and
   `docker compose up -d`. Back up first (scripts/volume-backup.sh); without the settings nothing changes.

### 1. OpenMRS 3.0 Frontend (SPA)
The frontend is built using `npx openmrs assemble` to create a custom distribution of micro-frontends.

**Option A: Manual Build (Recommended for Development/Debugging)**
Run this if you want to see the build process output or "own" the build artifact on your host machine.
```bash
docker run --rm -it \
  -v $(pwd)/openmrs/frontend:/app \
  -w /app \
  node:20-alpine \
  /bin/sh -c "npm install -g openmrs@next && cp spa-build-config.json spa-assemble-config.json && npx openmrs assemble --manifest --mode config --config spa-assemble-config.json --target ./spa"
```
*   This creates a `./openmrs/frontend/spa` directory with `index.html` and assets.

**Option B: Docker Compose Build**
Standard deploy. Note: This can take 5-10 minutes on the first run as it downloads ~50 modules.
```bash
docker-compose build openmrs-frontend
```

### 2. OpenMRS Backend (API)
Compiles the custom distro using Maven and layers it into Tomcat.
```bash
docker-compose build openmrs-backend
```
*   **Source**: `openmrs/distro/pom.xml` defines the modules.
*   **Verification**: Check logs with `docker-compose logs -f openmrs-backend`.

### 3. Odoo 19
Builds the Odoo image with custom addons.
```bash
docker-compose build odoo
```
*   Add custom modules to `odoo/addons/`.

### 4. Nginx Gateway
Rebuild the proxy if you change `nginx.conf` or the dashboard `index.html.template`.
```bash
docker-compose build gateway
```

### 5. Orthanc (PACS Server)
Orthanc uses the official Orthanc Docker image with custom configuration and OHIF plugin.
```bash
# No build needed - uses pre-built image
# Configuration is in orthanc/orthanc.json
# OHIF viewer is integrated via Orthanc plugin
```

### 6. OpenELIS (Lab System)
OpenELIS uses pre-built images from I-TECH. Configuration is managed through environment variables and volume mounts.
```bash
# No build needed - uses pre-built images
# Configuration files are in openelis/volume/
```

### 8. Keycloak (Identity Management)
Keycloak uses the official Quay.io image with realm imports.
```bash
# No build needed - uses pre-built image
# Realm configuration is in keycloak/imports/
```

---

## Running the Stack
Start all services in detached mode:
```bash
docker-compose up -d
```
*   **Access**: [https://localhost](https://localhost) (Service Dashboard)
*   **OpenMRS**: [https://localhost/openmrs](https://localhost/openmrs)
*   **Odoo**: [https://localhost:8069](https://localhost:8069) (through the gateway; Odoo itself is not published)


## Scripts

Each has a usage block at its top; `scripts/test_*.py` are their self-tests (CI: `.github/workflows/security-checks.yml`).

| Script | What it does |
|---|---|
| `scripts/apply-links.py` | Apply the reviewed link table at a site cutover (backlog KC-36, spec D22). |
| `scripts/bootstrap-export.py` | Bootstrap exporter + review workbook for a live site (backlog KC-25, spec §9.2 D41/D22). |
| `scripts/check-artifacts.py` | Committed build artifacts must be accounted for (backlog KC-46-03). |
| `scripts/check-backup-safety.py` | No script writes a plaintext database dump or puts a DB password on argv (backlog KC-28-01, KC-28-04). |
| `scripts/check-ingress.py` | Public hostnames in any compose file (incl. the Coolify export) reach only `gateway`, over TLS (backlog KC-44-01); known cases in `security/ingress.pending`. |
| `scripts/check-publish.py` | Workflows that push images are triggered only by tags/releases (backlog KC-46-02); today's branch publishers are in `security/publish.pending`. |
| `scripts/check-esm-login.py` | The esm-login tarball the O3 SPA is built from keeps the oauth2 log-out patch (log out goes to `/openmrs/oauth2logout`, ending the Keycloak session) and `config-sso.json` is shipped and loaded with OpenMRS central login on (backlog KC-35-03). |
| `scripts/openmrs-weak-passwords.py` | Read-only: lists active OpenMRS accounts whose password is still `Admin123` or a blocklisted word (e.g. provider logins CIS created with its old shared default); the check runs inside MariaDB (backlog KC-33-03). |
| `scripts/disable-sso.py` | Switch central login off (`--apply` writes `NIDAN_SSO_ENABLED=false` to `.env.local`) only when no app's local admin (OpenMRS, Odoo, OpenELIS, Superset, Orthanc) still has a demo/default password; checks read-only in each app's own storage and prints a reset slip per unsafe app (backlog KC-29-03). |
| `scripts/legacy-realms.py` | Lists Keycloak realms other than `master` and `nidan` (left by the old compose's `--import-realm`, e.g. `nidan-realm` with an admin user); `--delete REALM` removes one after a backup (backlog KC-04-02). |
| `scripts/staff-config.py` | Writes `proxy/staff-site/` from the realm's `/staff/*` groups: the Staff page's jobs and the gateway's only routable job-group ids (backlog KC-21-09). Re-run after adding a job, then restart the gateway. |
| `scripts/keycloak-admin-permissions.py` | Hospital-admin scope inside Keycloak (fine-grained admin permissions v2): `/staff/*` job groups only, never admins, roles or other groups (backlog KC-09). Dry run unless `--apply`; re-run after adding a job. |
| `scripts/realm-audit.py` | Nightly (docs/realm-audit.cron): admins without OTP, an admin inside a `/staff/*` job, default role wider than `account/view-profile` (backlog KC-09-08/09). Exit 1 on any finding. |
| `scripts/slow-timings.py` | Markdown table of the `@slow` Playwright checks and their durations; the nightly e2e run appends it to its summary (backlog KC-42-04). |
| `scripts/keycloak-restore-drill.sh` | Keycloak restore drill: back up a Keycloak with `volume-backup.sh` (throwaway key), restore into a new volume, log in there, within 15 min (backlog KC-28-06). Leaves two stopped drill projects behind. |
| `scripts/keycloak-event-shipper.py` | Every 5 min (docs/keycloak-events.cron): appends Keycloak's new login and admin events to JSONL files outside its database (backlog KC-23-05). |
| `scripts/keycloak-reapply-after-restore.py` | After a Keycloak restore: re-applies disables, removals and deletions recorded since the backup, never a grant (backlog KC-28-07). Dry run unless `--apply`. |
| `scripts/check-catalog.py` | Role catalog check (backlog KC-24 / R1, KC-57). |
| `scripts/check-demo-env.py` | The tracked demo .env may only hold reviewed public demo values (backlog KC-05-07). |
| `scripts/check-openmrs-basic-clients.py` | Inventory of OpenMRS Basic-auth clients across the monorepo (backlog KC-32-06). |
| `scripts/check-pins.py` | Supply-chain pins (backlog KC-46-01): images by tag AND digest, no moving refs. |
| `scripts/check-docs.py` | Every compose variable and every script is documented (backlog KC-29-01). |
| `scripts/check-public-routes.py` | Fail when any unauthenticated route is not on the reviewed allow-list (backlog KC-53-05). |
| `scripts/check-realm.py` | Static checks on the Keycloak realm files (backlog KC-54-01, KC-01-03 static part). |
| `scripts/check-theme.py` | Keycloak theme safety: no unaudited unescaped output, no HTML in messages, en/ne keys match, no account enumeration (backlog KC-17-06). |
| `scripts/check-routes.py` | Compare the gateway's nginx.conf with the route contract proxy/routes.yaml (backlog KC-01-02/06). |
| `scripts/check-secret-compare.py` | Flag non-constant-time comparisons of secrets (backlog KC-51-03). |
| `scripts/create-gateway-truststore.sh` | Build a PKCS#12 truststore for CIS when calling HTTPS gateways (e.g. OpenELIS). |
| `scripts/dicom-firewall.sh` | Only the modality LAN may reach Orthanc's DICOM port (backlog KC-31-03). |
| `scripts/dry-run-diff.py` | Dry-run permission diff + orphan report before a site cutover (backlog KC-26, spec §9.2 step 5). |
| `scripts/fetch-keycloak-ids.py` | Fill keycloak_sub in a reviewed link table (cutover runbook step 5, backlog KC-36). |
| `scripts/generate-secrets.sh` | Generate unique per-site secrets into .env.local (untracked, mode 0600). Backlog KC-05. |
| `scripts/incident.py` | Incident response for central login (backlog KC-50). Dry run by default: prints every step; --execute acts. |
| `scripts/nidan-auth-check.py` | Print each component's auth mode and fail on contradictions (backlog KC-27-02/03/05, KC-14-06). |
| `scripts/preflight.py` | Refuse a production start on demo or weak secrets (backlog KC-05-03). |
| `scripts/release-gate.py` | Go-live gate: refuse a site release while any security acceptance criterion is red or skipped (KC-49-03). |
| `scripts/reporting-db-users.py` | Read-only reporting users for Superset and the DHIS2 app (backlog KC-48). |
| `scripts/reserved-usernames.py` | Reserved Keycloak usernames -> the realm's username pattern (backlog KC-55-03/04, D21). |
| `scripts/site-ca.sh` | Per-site CA for bare-IP / LAN-hostname hospitals (backlog KC-12-04/05, D55). |
| `scripts/verify-backup-restore.sh` | ST-10.2.4 — prove the most recent backup actually restores. |
| `scripts/volume-backup.sh` | Cold backup/restore of the nidan stateful volumes, encrypted at rest. |

## Pre-commit hook

Once per clone: `git config core.hooksPath scripts/git-hooks`. It refuses a commit that adds an unapproved
value to the tracked demo `.env` or stages `.env.local` (`scripts/check-demo-env.py`; CI runs the same check).
