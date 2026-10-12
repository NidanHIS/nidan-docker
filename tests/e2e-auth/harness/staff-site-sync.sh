#!/usr/bin/env bash
# Staff page jobs for the FULL harness (sso-openmrs.yml). Its Keycloak is dev-mem, so the /staff/<job> group ids change
# at every realm import; a real site runs scripts/staff-config.py once. This writes them from the live realm and loads
# them into the running gateway (no restart or recreate): the Staff page can then list and change people.
#   harness/staff-site-sync.sh        (run-keycloak-suite.sh does this after each realm check)
# harness/staff-site/ (fake ids) stays for the stub harness, whose Staff tests fake Keycloak.
set -euo pipefail
here=$(cd "$(dirname "$0")" && pwd)
KC=${KEYCLOAK_URL:-http://localhost:18180/auth}
GW=${GATEWAY_CONTAINER:-nidan-sso-openmrs-harness-gateway-1}
out="$here/.build/staff-site-full"
mkdir -p "$out"
tf=$(mktemp "${TMPDIR:-/tmp}/kc-token.XXXXXX"); chmod 600 "$tf"; trap 'rm -f "$tf"' EXIT
curl -s -d grant_type=password -d client_id=admin-cli -d username=admin -d password=harness-admin-password \
  "$KC/realms/master/protocol/openid-connect/token" | python3 -c 'import sys,json;print(json.load(sys.stdin)["access_token"])' > "$tf"
KC_TOKEN_FILE="$tf" python3 -I "$here/../../../scripts/staff-config.py" "$out" --url "$KC" >/dev/null
docker cp "$out/staff-groups.map" "$GW:/etc/nginx/staff/staff-groups.map" >/dev/null
docker cp "$out/staff-config.json" "$GW:/usr/share/nginx/html/staff/staff-config.json" >/dev/null
docker exec "$GW" nginx -t -q && docker exec "$GW" nginx -s reload
echo "staff page: $(python3 -c "import json,sys;print(', '.join(j['name'] for j in json.load(open(sys.argv[1]))['jobs']))" "$out/staff-config.json")"
