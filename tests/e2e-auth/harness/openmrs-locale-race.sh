#!/bin/sh
# OpenMRS 2.8 allowed-locales race (nidancore NidancoreActivator warms the cache): restart OpenMRS on the
# sso-openmrs.yml harness, wait 30 s, send 30 first requests at once, and fail if any REST call hit
# ArrayIndexOutOfBoundsException in getAllowedLocales. Without the warm-up this failed every time (2026-10-09).
#   tests/e2e-auth/harness/openmrs-locale-race.sh [RUNS]
set -eu
C=nidan-sso-openmrs-harness-openmrs-backend-1; G=nidan-sso-openmrs-harness-gateway-1
URL=http://openmrs-backend:8080/openmrs/ws/rest/v1/session
fail=0
for n in $(seq 1 "${1:-3}"); do
  docker restart "$C" >/dev/null; T=$(date -u +%Y-%m-%dT%H:%M:%SZ)
  until docker logs --since "$T" "$C" 2>&1 | grep -q "Server startup in"; do sleep 2; done
  sleep 30
  docker exec "$G" sh -c "for i in \$(seq 1 30); do wget -qO /dev/null --header 'Accept: application/json' $URL 2>/dev/null & done; wait" || true
  hits=$(docker logs --since "$T" "$C" 2>&1 | grep -c "BaseRestController.handleException.*out of bounds" || true)
  echo "run $n: $hits REST call(s) failed in getAllowedLocales"
  [ "$hits" = 0 ] || fail=1
done
exit $fail
