#!/usr/bin/env bash
# The whole Keycloak / central-login acceptance suite, the same way every time (see README.md).
#
#   ./run-keycloak-suite.sh                 full: every real-app, realm and gateway spec on the full harness
#   ./run-keycloak-suite.sh --all           full + contract + sso-stub + orthanc + disruptive
#   ./run-keycloak-suite.sh --full --disruptive --contract --sso-stub --orthanc   any combination
#   ./run-keycloak-suite.sh --matrix-only   regenerate KEYCLOAK-TEST-MATRIX.md from the latest reports/run-*
#
# Stages (each writes reports/run-<time>/<stage>-<group>.json; ac-matrix.py turns that run into
# reports/run-<time>/KEYCLOAK-TEST-MATRIX.md, copied to ./KEYCLOAK-TEST-MATRIX.md):
#   full        harness/sso-openmrs.yml (Keycloak behind the gateway's :9443, OpenMRS, Odoo, OpenELIS, Superset, CIS +
#               Kafka, Orthanc + OHIF, DHIS2 app). Bring it up first: README.md "Full harness".
#   disruptive  the outage tests, one at a time (KC-32-03 CIS retry, KC-14-07 paywall without Keycloak, KC-11-07 outage
#               page, KC-10-11 upstream down). Keycloak is dev-mem: each outage drops the realm, which is re-imported.
#   contract    harness/docker-compose.yml (real gateway, stub apps), SSO off then on, as CI does. Its ports clash with
#               the full harness, so the full harness gateway is stopped meanwhile and started again after.
#   sso-stub    harness/sso.yml (login flow on stub apps). Stops the full harness Keycloak + gateway meanwhile and
#               re-imports the realm after.
#   orthanc     harness/orthanc.yml (Orthanc lockdown checks).
#
# Env: PAYWALL_PATIENT (an Odoo patient uuid with an unpaid cart; paywall tests skip without it), SLOW=1 to include
# @slow tests in the full stage (they always run in disruptive). Exit 1 if any stage failed or the matrix shows a
# FAILED or MISSING criterion.
set -uo pipefail
cd "$(dirname "$0")"
E2E=$PWD
ND=$(cd ../.. && pwd)
H="$E2E/harness"
P=nidan-sso-openmrs-harness
KC=http://localhost:18180/auth
KC_PUBLIC=https://localhost:18445/auth
rc=0

full=0; contract=0; ssostub=0; orthanc=0; disruptive=0; matrixonly=0
[ $# -eq 0 ] && full=1
for a in "$@"; do
  case "$a" in
    --full) full=1 ;; --contract) contract=1 ;; --sso-stub) ssostub=1 ;; --orthanc) orthanc=1 ;;
    --disruptive) disruptive=1 ;; --all) full=1; contract=1; ssostub=1; orthanc=1; disruptive=1 ;;
    --matrix-only) matrixonly=1 ;;
    -h|--help) sed -n '2,26p' "$0"; exit 0 ;;
    *) echo "unknown option $a" >&2; exit 2 ;;
  esac
done
# every run keeps its own reports (nothing from an earlier run can leak into this run's matrix)
if [ $matrixonly -eq 1 ]; then
  RUN=$(ls -d reports/run-* 2>/dev/null | sort | tail -n 1); [ -n "$RUN" ] || { echo "no run in reports/" >&2; exit 2; }
else
  RUN="reports/run-$(date +%Y%m%d-%H%M%S)"; mkdir -p "$RUN"
fi

say() { printf '\n=== %s\n' "$*"; }
# pw <report-name> <playwright args...>: run, keep the JSON report, remember a failure
pw() {
  local name=$1; shift
  say "playwright: $name"
  npx playwright test "$@"; local r=$?
  [ -f test-results/report.json ] && cp test-results/report.json "$RUN/$name.json"
  [ $r -eq 0 ] || { echo "!!! $name failed (exit $r)"; rc=1; }
}
running() { [ "$(docker inspect -f '{{.State.Running}}' "$1" 2>/dev/null)" = true ]; }
healthy() { [ "$(docker inspect -f '{{.State.Health.Status}}' "$1" 2>/dev/null)" = healthy ]; }
wait_for() { local i; for i in $(seq 1 "${2:-60}"); do eval "$1" && return 0; sleep 5; done; return 1; }

# The realm on the dev-mem Keycloak: re-import when missing, then the hospital-admin permissions (KC-09) every time.
realm_ready() {
  wait_for "healthy $P-keycloak-1" 60 || { echo "Keycloak not healthy" >&2; return 1; }
  if [ "$(curl -sk -o /dev/null -w '%{http_code}' "$KC_PUBLIC/realms/nidan/.well-known/openid-configuration")" != 200 ]; then
    say "realm missing: importing from keycloak/realm"
    "$H/compose.sh" up --no-deps config-cli >/dev/null 2>&1 || { echo "realm import failed" >&2; return 1; }
  fi
  local tf; tf=$(mktemp "${TMPDIR:-/tmp}/kc-token.XXXXXX"); chmod 600 "$tf"
  curl -s -d grant_type=password -d client_id=admin-cli -d username=admin -d password=harness-admin-password \
    "$KC/realms/master/protocol/openid-connect/token" | python3 -c 'import sys,json;print(json.load(sys.stdin)["access_token"])' > "$tf"
  KC_TOKEN_FILE="$tf" python3 -I "$ND/scripts/keycloak-admin-permissions.py" --apply --url "$KC" >/dev/null
  python3 -I "$H/manual-users.py" --fixups-only >/dev/null || { echo "harness callbacks failed" >&2; return 1; }
  local prc=$?; rm -f "$tf"            # its own temp file (held the admin token)
  return $prc
}

full_env() {
  export OPENMRS_SSO_FULL=1 OPENMRS_DB_EXEC=1 ODOO_SSO_FULL=1 SUPERSET_SSO_FULL=1 OPENELIS_SSO_FULL=1 CIS_SYNC_FULL=1 \
    IMAGING_SSO_FULL=1 DHIS2_LOCAL=1 KC_ADMIN_PERMISSIONS=1 KEYCLOAK_URL=$KC KEYCLOAK_PUBLIC_URL=$KC_PUBLIC \
    GATEWAY_DOMAIN=localhost GATEWAY_URL=https://localhost GATEWAY_HTTP=http://localhost GATEWAY_OE_URL=https://localhost:18444
  [ -n "${PAYWALL_PATIENT:-}" ] || echo "note: PAYWALL_PATIENT unset - the paywall tests (KC-14-04/05/07) will skip"
}

# the stub-harness stages must not see the full stage's settings (they would run the real-app specs against stubs)
clear_full_env() {
  unset OPENMRS_SSO_FULL OPENMRS_DB_EXEC ODOO_SSO_FULL SUPERSET_SSO_FULL OPENELIS_SSO_FULL CIS_SYNC_FULL IMAGING_SSO_FULL \
    DHIS2_LOCAL KC_ADMIN_PERMISSIONS KEYCLOAK_URL KEYCLOAK_PUBLIC_URL GATEWAY_DOMAIN GATEWAY_URL GATEWAY_HTTP GATEWAY_OE_URL
}

require_full_harness() {
  local missing=()
  for s in keycloak gateway oauth2-proxy openmrs-backend openmrs-frontend odoo openelis superset cis kafka orthanc dhis2-local; do
    running "$P-$s-1" || missing+=("$s")
  done
  if [ ${#missing[@]} -gt 0 ]; then
    echo "full harness not running (missing: ${missing[*]}). See README.md \"Full harness\"." >&2
    return 1
  fi
}

if [ $matrixonly -eq 0 ] && [ $full -eq 1 ]; then
  say "stage full"
  if require_full_harness && realm_ready; then
    full_env
    grep_slow=(--grep-invert @slow); [ "${SLOW:-}" = 1 ] && grep_slow=(--grep .)   # not (): bash 3.2 + set -u
    pw full-apps --project=sso-on --workers=1 "${grep_slow[@]}" \
      specs/openmrs-sso.spec.ts specs/odoo-sso.spec.ts specs/superset-sso.spec.ts specs/openelis-sso.spec.ts \
      specs/keycloak-origin.spec.ts specs/cis-sync.spec.ts specs/imaging-sso.spec.ts specs/port80.spec.ts specs/multi-app.spec.ts specs/two-step.spec.ts
    pw full-gateway --project=sso-on --workers=2 \
      specs/launcher.spec.ts specs/status-pages.spec.ts specs/exempt-routes.spec.ts specs/routes.spec.ts \
      specs/live-exposure.spec.ts specs/i18n.spec.ts
    pw full-realm --project=prod-like --workers=1 "${grep_slow[@]}" specs/realm.spec.ts
    pw full-dhis2-local --project=sso-off specs/dhis2-local.spec.ts
  else
    rc=1
  fi
fi

if [ $matrixonly -eq 0 ] && [ $disruptive -eq 1 ]; then
  say "stage disruptive (Keycloak outages; the realm is re-imported after each)"
  if require_full_harness && realm_ready; then
    full_env; export HARNESS_DISRUPTIVE=1
    for g in KC-32-03 KC-14-07 KC-11-07 KC-10-11; do
      pw "disruptive-$g" --project=sso-on --workers=1 -g "@$g\\b" specs/cis-sync.spec.ts specs/openmrs-sso.spec.ts
      # gateway first: it has a fixed address that a Keycloak started before it can take (then the gateway cannot start)
      running "$P-gateway-1" || docker start "$P-gateway-1" >/dev/null
      running "$P-keycloak-1" || docker start "$P-keycloak-1" >/dev/null
      realm_ready || rc=1
    done
    unset HARNESS_DISRUPTIVE
  else
    rc=1
  fi
fi

if [ $matrixonly -eq 0 ] && [ $contract -eq 1 ]; then
  say "stage contract (stub apps; the full harness gateway is paused)"
  clear_full_env
  paused=0; running "$P-gateway-1" && { docker stop "$P-gateway-1" >/dev/null; paused=1; }
  C="$H/docker-compose.yml"
  ( export GATEWAY_URL=https://localhost:18443 GATEWAY_HTTP=http://localhost:18080 GATEWAY_OE_URL=https://localhost:18444 \
      STAFF_URL=https://127.0.0.1:18445 STAFF_ECHO=1 SSO_HARNESS=1
    HARNESS_SSO=false docker compose -f "$C" up -d --build --wait
    pw contract-sso-off --project sso-off --grep-invert @needs-app
    HARNESS_SSO=true docker compose -f "$C" up -d --wait
    pw contract-sso-on --project sso-on --grep-invert @needs-app
    exit $rc ) || rc=1
  docker compose -f "$C" stop >/dev/null
  [ $paused -eq 1 ] && docker start "$P-gateway-1" >/dev/null
fi

if [ $matrixonly -eq 0 ] && [ $ssostub -eq 1 ]; then
  say "stage sso-stub (login flow on stub apps; the full harness Keycloak and gateway are paused)"
  clear_full_env
  kc_paused=0; gw_paused=0
  running "$P-gateway-1" && { docker stop "$P-gateway-1" >/dev/null; gw_paused=1; }
  running "$P-keycloak-1" && { docker stop "$P-keycloak-1" >/dev/null; kc_paused=1; }
  F="$H/sso.yml"
  ( export KC_USERNAME_PATTERN="$(python3 -I "$ND/scripts/reserved-usernames.py" | sed -n 's/^KC_USERNAME_PATTERN=//p')"
    docker compose -f "$F" up -d --build --wait keycloak
    docker compose -f "$F" --profile import up --no-deps --abort-on-container-exit --exit-code-from config-cli config-cli
    docker compose -f "$F" up -d --build --wait
    SSO_FULL=1 KEYCLOAK_URL=http://localhost:18180/auth KEYCLOAK_PUBLIC_URL=https://localhost:18445/auth GATEWAY_URL=https://localhost:18443 \
      pw sso-stub --project=sso-on specs/sso-flow.spec.ts
    exit $rc ) || rc=1
  docker compose -f "$F" stop >/dev/null
  [ $gw_paused -eq 1 ] && docker start "$P-gateway-1" >/dev/null    # gateway first (fixed address, see above)
  [ $kc_paused -eq 1 ] && docker start "$P-keycloak-1" >/dev/null
  [ $kc_paused -eq 1 ] && { realm_ready || rc=1; }
fi

if [ $matrixonly -eq 0 ] && [ $orthanc -eq 1 ]; then
  say "stage orthanc"
  clear_full_env
  O="$H/orthanc.yml"
  docker compose -f "$O" up -d
  wait_for "curl -sf -u nidan:harness-orthanc-admin http://127.0.0.1:18042/system >/dev/null" 40   # gitleaks:allow (harness)
  ORTHANC_URL=http://127.0.0.1:18042 pw orthanc --project=sso-off specs/orthanc.spec.ts
  docker compose -f "$O" stop >/dev/null
fi

say "acceptance matrix"
shopt -s nullglob; reports=("$RUN"/*.json); shopt -u nullglob
[ ${#reports[@]} -gt 0 ] || { echo "no test reports in $RUN: nothing ran"; exit 1; }
python3 -I ac-matrix.py "${reports[@]}" --out "$RUN/KEYCLOAK-TEST-MATRIX.md" || rc=1
cp "$RUN/KEYCLOAK-TEST-MATRIX.md" KEYCLOAK-TEST-MATRIX.md
echo "matrix: $E2E/KEYCLOAK-TEST-MATRIX.md"
exit $rc
