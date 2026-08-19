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

INDEX_TEMPLATE="${INDEX_HTML_TEMPLATE:-/etc/nginx/templates/index.html.template}"
envsubst '${DASHBOARD_ODOO_URL}' < "${INDEX_TEMPLATE}" > /usr/share/nginx/html/index.html

# Execute the original entrypoint
exec "$@"
