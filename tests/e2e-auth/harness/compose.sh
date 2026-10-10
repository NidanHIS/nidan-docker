#!/bin/sh
# docker compose for the sso-openmrs harness, every profile, with the realm's reserved-username pattern (config-cli
# needs it). Paths for the optional mounts (OCA_AUTH_OIDC, ODOO_PYLIBS, SUPERSET_PYLIBS, OPENMRS_CUSTOM_MODULES) come
# from the caller's environment.   harness/compose.sh up -d --no-deps config-cli
set -eu
here=$(cd "$(dirname "$0")" && pwd)
KC_USERNAME_PATTERN="$(python3 -I "$here/../../../scripts/reserved-usernames.py" 2>/dev/null | sed -n 's/^KC_USERNAME_PATTERN=//p')"
export KC_USERNAME_PATTERN
exec docker compose -p nidan-sso-openmrs-harness -f "$here/sso-openmrs.yml" --profile odoo --profile superset \
  --profile openelis --profile dhis2 --profile cis --profile orthanc "$@"
