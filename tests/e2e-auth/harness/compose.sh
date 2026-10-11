#!/bin/sh
# docker compose for the sso-openmrs harness, every profile, with the realm's reserved-username pattern (config-cli
# needs it). Mount paths default to what images/build.sh writes to harness/.build; OPENMRS_CUSTOM_MODULES defaults
# to nidan-docker/openmrs/custom_modules (sso-openmrs.yml).   harness/compose.sh up -d --no-deps config-cli
set -eu
here=$(cd "$(dirname "$0")" && pwd)
KC_USERNAME_PATTERN="$(python3 -I "$here/../../../scripts/reserved-usernames.py" 2>/dev/null | sed -n 's/^KC_USERNAME_PATTERN=//p')"
export KC_USERNAME_PATTERN
# add-ons and libraries built by images/build.sh (override any of them from the caller's environment)
b="$here/.build"
export OCA_AUTH_OIDC="${OCA_AUTH_OIDC:-$b/server-auth/auth_oidc}" ODOO_PYLIBS="${ODOO_PYLIBS:-$b/pylibs-odoo}" \
  SUPERSET_PYLIBS="${SUPERSET_PYLIBS:-$b/pylibs-superset}"
exec docker compose -p nidan-sso-openmrs-harness -f "$here/sso-openmrs.yml" --profile odoo --profile superset \
  --profile openelis --profile dhis2 --profile cis --profile orthanc "$@"
