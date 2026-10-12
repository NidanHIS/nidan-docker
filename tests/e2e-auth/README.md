# Central login (Keycloak) acceptance suite

Every Keycloak / central-login feature has a Playwright test tagged with the acceptance criterion it proves
(`@KC-nn-mm`, plus `@security` for security criteria; ids from the backlog, listed in `acceptance-criteria.json`).
One command runs them the same way every time and writes a matrix of all 311 criteria with their result:

```bash
cd nidan-docker/tests/e2e-auth
npm ci && npx playwright install chromium          # once
PAYWALL_PATIENT=<odoo patient uuid> ./run-keycloak-suite.sh          # full harness stage (default)
PAYWALL_PATIENT=<uuid> ./run-keycloak-suite.sh --all                  # every stage
```

Results: `KEYCLOAK-TEST-MATRIX.md` (and `reports/run-<time>/`, one folder per run), the HTML report in
`playwright-report/`. The run exits 1 when a test fails or a criterion is not covered by anything.

## Stages

| stage | harness | what it covers | side effects |
|---|---|---|---|
| `--full` (default) | `harness/sso-openmrs.yml` | real apps behind the gateway: OpenMRS, Odoo/POS, OpenELIS, Superset, CIS + Kafka, Orthanc + OHIF, DHIS2 app (local login); the realm; Keycloak's :9443 origin; :8069; port 80; launcher, status pages, exempt routes, live exposure | creates test users in the dev-mem realm; applies the hospital-admin permissions |
| `--disruptive` | same | KC-32-03 (CIS retries while Keycloak is down), KC-14-07 (paywall without Keycloak), KC-11-07 (outage page), KC-10-11 (upstream down) | stops Keycloak / upstreams; the realm is re-imported after each |
| `--contract` | `harness/docker-compose.yml` | the gateway contract with stub apps, SSO off and on: gates, Staff page and its API, launcher, routes | pauses the full-harness gateway (ports), (re)builds the stub harness |
| `--sso-stub` | `harness/sso.yml` | the login flow on stub apps (`sso-flow.spec.ts`) | pauses the full-harness Keycloak and gateway; realm re-imported after |
| `--orthanc` | `harness/orthanc.yml` | Orthanc lockdown | starts/stops the Orthanc harness |

Not run by the script: `@slow` tests in `--full` unless `SLOW=1`; the LAN port scan (KC-02-01/02) needs
`LAN_HOST=<hospital host>` from another machine on the LAN; the prod-like CI job (`.github/workflows/e2e-gateway.yml`).

## Matrix results

`PASSED` / `FAILED` / `SKIPPED` (tests ran; skips name their missing env or harness) / `NOT RUN` (the stage holding
its tests was not selected) / `EVIDENCE` (a CI test, tool or document in `ac-evidence.yaml`) / `MANUAL` (a person
checks it, `docs/GO-LIVE-CHECKLIST.md`) / `DEFERRED` (moved to a later phase by decision: KC-41-01/02, per-study
viewer links through orthanc-auth-service, phase 2) / `MISSING` (nothing covers it: fails the run).

## Full harness (first time, then reuse)

```bash
cd nidan-docker/tests/e2e-auth/harness
images/build.sh            # images from the working tree + OCA auth_oidc and Python libs into harness/.build
# OpenMRS custom OMODs (incl. oauth2login and nidancore from the working tree) in nidan-docker/openmrs/custom_modules,
# as for the real stack (repo CLAUDE.md "OpenMRS backend"), or point OPENMRS_CUSTOM_MODULES at another folder.
./compose.sh up -d --wait keycloak
./compose.sh up --no-deps config-cli          # realm import (again after any Keycloak restart: it is dev-mem)
./compose.sh up -d                             # everything else
```

First start only:
- OpenMRS initialises an empty database (about 15 minutes), then restart `openmrs-backend` once so oauth2login's
  servlet registers. The distro's `authentication` module must be stopped (it overrides oauth2login, KC-34-01).
- Odoo starts with base + auth_oidc; install the rest once:
  `docker exec nidan-sso-openmrs-harness-odoo-1 odoo -c /etc/odoo/odoo.conf -d nidan --stop-after-init --http-port=8075 -i nidan_keycloak_sso,nidan_connector,nidan_pos_enhance,nidan_billing_sync,nidan_payment_management,nidan_portal_desk,pos_esewa_qr,pos_khalti_qr,trigonal_ird`
- `PAYWALL_PATIENT`: an OpenMRS patient uuid that Odoo knows (res.partner `patient_uuid`) with an unpaid POS cart of
  Rs 500 (KC-14-04 checks that amount). Without it the paywall tests skip.

Addresses on the full harness: gateway https://localhost (443), :8443 as 18444, Keycloak login https://localhost:18445/auth
(the gateway's :9443), Odoo https://localhost:8069 (through the gateway), Keycloak admin API http://localhost:18180/auth
(production: SSH tunnel to 127.0.0.1:8180), Odoo JSON-RPC back door 127.0.0.1:18069, DHIS2 app 127.0.0.1:15003.
Throwaway credentials only (`harness-*`, Keycloak admin `admin` / `harness-admin-password`, test users `Harness-Pass-123`).

## Adding a test

Tag the test title with every criterion it proves (`@KC-nn-mm`), `@security` when the criterion is a security one,
and `@sso-on-only` / `@slow` as appropriate; gate it on the harness it needs with a `test.skip(!process.env.X, ...)`
and add `X` to the right stage in `run-keycloak-suite.sh`. New criteria go into the backlog and
`acceptance-criteria.json`; anything proven outside Playwright goes into `ac-evidence.yaml`. `ac-coverage.py` (CI)
fails on tags that point at no criterion and on security criteria whose tests lack `@security`.

## What each spec covers

| spec | features |
|---|---|
| `openmrs-sso` | O3 login/logout through Keycloak, user created and roles/names synced from the token every login, machine accounts, Basic refused, paywall, outage page, upstream down, role-heavy cookies |
| `odoo-sso` | Odoo central login, groups and POS shops from the token, no password login for linked people, central login off page, QR payment pages, service APIs 404 on :8069, POS websocket gate, disable closes the websocket, Bearer reads of OpenMRS |
| `openelis-sso` | OpenELIS first login provisions by sub with Lab Tech roles, same-name colleagues, :8443 and /api with/without session, paywall |
| `superset-sso` | no local admin login, cookie names the gateway expires, Superset JWT without the gateway cookie refused, no sign-in without a Superset role, no public dashboards |
| `keycloak-origin` | issuer on :9443, Keycloak paths 404 elsewhere, master/admin/registration refused on every port, login rate limit, real client IP in events, internal back-channel, cross-origin isolation |
| `realm` | realm settings: no self-registration, no password grant, reserved names, token contracts, staff cannot edit themselves, hospital-admin permissions, OTP for admins, login messages (English/Nepali), password policy, lockout, lifetimes, events |
| `multi-app` | people with two or three apps (clinician, front desk, lab cashier): one login opens each permitted app with its own roles, the rest refuse, launcher cards exact; user switch on one browser; removing one app keeps the others; disable signs out of every app |
| `cis-sync` | CIS writes patients and visits as nidan-sync with a Keycloak token; retries through a Keycloak outage |
| `imaging-sso` | a doctor opens a study in OHIF through the gateway |
| `dhis2-local` | the DHIS2 app's local login with central login off |
| `port80` | port 80: redirect and ACME only; healthcheck on the internal listener |
| `gate`, `exempt-routes`, `routes`, `live-exposure` | every route's gate, exempt routes, path tricks, TLS, internal APIs not reachable, port scans |
| `launcher`, `status-pages`, `i18n` | launcher app list and identity, outage / not-allowed pages, Nepali/English |
| `staff`, `staff-api` | Staff page (add, change job, disable signs out first, reset password, admins never listed) and its API allow-list |
| `sso-flow` | the login flow on stub apps: sign-in, sign-out, role changes, Keycloak outage |
| `orthanc` | Orthanc viewer is read-only, no Lua over REST |
