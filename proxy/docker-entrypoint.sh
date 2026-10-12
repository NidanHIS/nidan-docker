#!/bin/sh
set -e

# Generate self-signed certificate if needed (before nginx starts)
DOMAIN="${GATEWAY_DOMAIN:-localhost}"
LIVE_DIR="/etc/letsencrypt/live/${DOMAIN}"

# Chrome has ignored the CN field since v58, so a certificate with no
# subjectAltName is invalid for *every* host, localhost included.  Whatever
# staff type in the address bar must appear in the SAN list: loopback is always
# covered, and GATEWAY_CERT_SANS adds this deployment's LAN IP or hostname.
if echo "${DOMAIN}" | grep -Eq '^[0-9]+(\.[0-9]+){3}$'; then
    DOMAIN_SAN="IP:${DOMAIN}"
else
    DOMAIN_SAN="DNS:${DOMAIN}"
fi
CERT_SANS="${DOMAIN_SAN},IP:127.0.0.1${GATEWAY_CERT_SANS:+,${GATEWAY_CERT_SANS}}"
# already covered when GATEWAY_DOMAIN is left at its default
[ "${DOMAIN}" = "localhost" ] || CERT_SANS="${CERT_SANS},DNS:localhost"

# A real Let's Encrypt certificate always carries SANs, so this only ever
# replaces a missing or legacy CN-only self-signed one.
# ponytail: presence check only — editing GATEWAY_CERT_SANS later does not
# regenerate; delete ${LIVE_DIR} to pick up new names.
cert_has_san() {
    openssl x509 -noout -ext subjectAltName -in "$1" 2>/dev/null \
        | grep -q 'DNS:\|IP Address:'
}

if [ ! -f "${LIVE_DIR}/fullchain.pem" ] || [ ! -f "${LIVE_DIR}/privkey.pem" ] \
   || ! cert_has_san "${LIVE_DIR}/fullchain.pem"; then
    echo "Generating self-signed certificate for ${DOMAIN} (SAN: ${CERT_SANS})..."
    mkdir -p "${LIVE_DIR}"
    openssl req -x509 -nodes -days 365 \
        -subj "/CN=${DOMAIN}" \
        -addext "subjectAltName=${CERT_SANS}" \
        -newkey rsa:2048 \
        -keyout "${LIVE_DIR}/privkey.pem" \
        -out "${LIVE_DIR}/fullchain.pem" 2>/dev/null
    echo "Certificate generated for ${DOMAIN}"
fi

# Template the nginx configuration with environment variables
# Set default OpenELIS context path if not provided
export OPENELIS_CONTEXT_PATH="${OPENELIS_CONTEXT_PATH:-/api/OpenELIS-Global/}"
export DASHBOARD_ODOO_URL="${DASHBOARD_ODOO_URL:-http://localhost:8069}"
envsubst '${GATEWAY_DOMAIN} ${OPENELIS_CONTEXT_PATH}' < /etc/nginx/nginx.conf.template > /etc/nginx/nginx.conf

# KC-12-02: HSTS only for a DNS name with a publicly issued certificate (not bare IP, not self-signed, not the
# site CA). The file is included wherever a location sets its own headers.
if [ "$(/usr/local/bin/hsts-decide.sh "${GATEWAY_DOMAIN:-localhost}" "${LIVE_DIR}/fullchain.pem")" = on ]; then
  echo 'add_header Strict-Transport-Security "max-age=31536000" always;' > /etc/nginx/hsts.conf
else
  : > /etc/nginx/hsts.conf
fi

# KC-56-06: the host names this gateway answers to (anything else gets 421). Lower-case, no port.
{
  echo 'map $host $nidan_host_ok {'
  echo '    default 0;'
  for h in localhost 127.0.0.1 "${GATEWAY_DOMAIN:-localhost}" "${OPENELIS_DOMAIN:-}" \
           $(echo "${GATEWAY_CERT_SANS:-}" | tr ',' ' ' | sed 's/DNS://g; s/IP://g'); do
    [ -n "$h" ] && printf '    "%s" 1;\n' "$(echo "$h" | tr 'A-Z' 'a-z')"
  done | sort -u
  echo '}'
} > /etc/nginx/allowed-hosts.conf

# Central login (KC-10): the auth snippets are real only with NIDAN_SSO_ENABLED=true. Otherwise every
# /etc/nginx/auth/*.conf is written empty, so nginx behaves exactly as without central login (KC-10-14).
mkdir -p /etc/nginx/auth
for f in /etc/nginx/auth-templates/*.conf; do
  if [ "${NIDAN_SSO_ENABLED:-false}" = "true" ]; then
    cp "$f" "/etc/nginx/auth/$(basename "$f")"
  else
    : > "/etc/nginx/auth/$(basename "$f")"
  fi
done
echo "central login: ${NIDAN_SSO_ENABLED:-false}"

# KC-12-07: per-IP limit on Keycloak's public auth/token and login forms. Only digits pass as the rate and only
# address/CIDR characters as exemptions; anything else falls back to 10/min with no exemption.
rate="${KC_LOGIN_RATE_PER_MIN:-10}"
case "$rate" in ''|*[!0-9]*) echo "KC_LOGIN_RATE_PER_MIN must be a number: using 10" >&2; rate=10 ;; esac
[ "$rate" -ge 1 ] 2>/dev/null || rate=10
{
  echo 'geo $nidan_kc_exempt {'
  echo '    default 0;'
  for c in $(echo "${KC_LOGIN_RATE_EXEMPT:-}" | tr ',' ' '); do
    case "$c" in *[!0-9A-Fa-f.:/]*) echo "KC_LOGIN_RATE_EXEMPT: ignoring $c" >&2 ;; *) printf '    %s 1;\n' "$c" ;; esac
  done
  echo '}'
  echo 'map $nidan_kc_exempt $nidan_kc_limit_key { 1 ""; default $binary_remote_addr; }'
  echo "limit_req_zone \$nidan_kc_limit_key zone=nidan_kc_oidc:10m rate=${rate}r/m;"
  echo "limit_req_zone \$nidan_kc_limit_key zone=nidan_kc_actions:10m rate=${rate}r/m;"
  echo 'limit_req_status 429;'
} > /etc/nginx/kc-rate.conf
echo "keycloak login rate: ${rate}/min per IP${KC_LOGIN_RATE_EXEMPT:+ (exempt: $KC_LOGIN_RATE_EXEMPT)}"

# KC-30-06: behind the Orthanc gates the gateway, not the browser, authenticates to Orthanc. The credentials stay in
# this file (root-only) and the request; a browser-sent Authorization is always replaced. Login off: gates are empty.
( umask 077
  basic() { printf 'proxy_set_header Authorization "Basic %s";\n' "$(printf '%s:%s' "$1" "$2" | base64 | tr -d '\n')"; }
  if [ "${NIDAN_SSO_ENABLED:-false}" = "true" ] && [ -n "${ORTHANC_USERNAME:-}" ] && [ -n "${ORTHANC_PASSWORD:-}" ]; then
    basic "$ORTHANC_USERNAME" "$ORTHANC_PASSWORD" > /etc/nginx/orthanc-auth-admin.conf          # Orthanc UI, orthanc-admin
  else
    echo 'proxy_set_header Authorization "";' > /etc/nginx/orthanc-auth-admin.conf
  fi
  if [ "${NIDAN_SSO_ENABLED:-false}" = "true" ] && [ -n "${ORTHANC_VIEWER_PASSWORD:-}" ]; then
    basic nidan-viewer "$ORTHANC_VIEWER_PASSWORD" > /etc/nginx/orthanc-auth-viewer.conf           # image viewer routes
  else
    echo 'proxy_set_header Authorization "";' > /etc/nginx/orthanc-auth-viewer.conf
  fi )

# Staff page jobs for this site (KC-21-09): generated by scripts/staff-config.py, mounted at /etc/nginx/staff-site.
# Without them the page shows no jobs and the gateway allows no job-group call.
mkdir -p /etc/nginx/staff
if [ -f /etc/nginx/staff-site/staff-groups.map ] && [ -f /etc/nginx/staff-site/staff-config.json ]; then
  cp /etc/nginx/staff-site/staff-groups.map /etc/nginx/staff/staff-groups.map
  cp /etc/nginx/staff-site/staff-config.json /usr/share/nginx/html/staff/staff-config.json
  echo "staff jobs: $(grep -c '/members' /etc/nginx/staff/staff-groups.map)"
else
  : > /etc/nginx/staff/staff-groups.map
  echo "staff jobs: none (run scripts/staff-config.py)"
fi

INDEX_TEMPLATE="${INDEX_HTML_TEMPLATE:-/etc/nginx/templates/index.html.template}"
# Launcher "Change password" goes to Keycloak (KC-55-05): only a plain http(s) URL may reach the page attribute.
case "${KEYCLOAK_PUBLIC_URL:-}" in
  "") ;;
  *[!A-Za-z0-9.:/_-]*|*//*//*) echo "KEYCLOAK_PUBLIC_URL is not a plain URL: ignored" >&2; KEYCLOAK_PUBLIC_URL="" ;;
  http://*|https://*) ;;
  *) echo "KEYCLOAK_PUBLIC_URL must start with http(s)://: ignored" >&2; KEYCLOAK_PUBLIC_URL="" ;;
esac
export KEYCLOAK_PUBLIC_URL="${KEYCLOAK_PUBLIC_URL:-}"
# Hospital name for page titles: plain text only (it lands in HTML), else the default
case "${NIDAN_BRAND_NAME:-}" in
  ''|*[\<\>\&\"\']*) NIDAN_BRAND_NAME='NidanEHR' ;;
esac
export NIDAN_BRAND_NAME
# Shared PCs: every app logout signs out of everything (default false: only when it was the person's only app)
[ "${NIDAN_APP_LOGOUT_ENDS_ALL:-}" = true ] || NIDAN_APP_LOGOUT_ENDS_ALL=false
export NIDAN_APP_LOGOUT_ENDS_ALL
envsubst '${DASHBOARD_ODOO_URL} ${GATEWAY_ANALYTICS_ID} ${KEYCLOAK_PUBLIC_URL} ${NIDAN_BRAND_NAME} ${NIDAN_APP_LOGOUT_ENDS_ALL}' < "${INDEX_TEMPLATE}" > /usr/share/nginx/html/index.html
# Outage page carries the site's IT support number (KC-16-03).
envsubst '${SUPPORT_PHONE} ${NIDAN_BRAND_NAME}' < /etc/nginx/templates/outage.html.template > /usr/share/nginx/html/outage.html
# Third-party analytics only when explicitly configured (public demo); hospitals get none.
if [ -z "${GATEWAY_ANALYTICS_ID:-}" ]; then
  sed -i '/<!-- ANALYTICS:BEGIN/,/<!-- ANALYTICS:END -->/d' /usr/share/nginx/html/index.html
else
  # Public demo with analytics: the launcher's strict CSP must also allow Google's tag script.
  sed -i "s|default-src 'self'; img-src 'self' data:|default-src 'self'; img-src 'self' data: https://www.google-analytics.com; script-src 'self' 'unsafe-inline' https://www.googletagmanager.com; connect-src 'self' https://*.google-analytics.com|" /etc/nginx/launcher-page-headers.conf
fi
# Demo login hints only on the public demo (KC-04-01).
if [ "${GATEWAY_SHOW_DEMO_CREDENTIALS:-false}" != "true" ]; then
  sed -i '/<!-- DEMO:BEGIN/,/<!-- DEMO:END -->/d' /usr/share/nginx/html/index.html
fi

# PHI-free access log on its own volume, 0600, rotated daily and kept 365 days (KC-13-03, A2/D53).
mkdir -p /var/log/nidan
chmod 700 /var/log/nidan
touch /var/log/nidan/access.log
chmod 600 /var/log/nidan/access.log
echo "15 0 * * * /usr/local/bin/rotate-access-log.sh /var/log/nidan 365" > /etc/crontabs/root
crond -b -l 8
umask 077   # files nginx creates itself (reopen after rotation) are 0600 too

# Execute the original entrypoint
exec "$@"
