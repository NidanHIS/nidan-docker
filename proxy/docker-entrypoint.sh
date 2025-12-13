#!/bin/sh
set -e

# Generate self-signed certificate if needed (before nginx starts)
DOMAIN="${GATEWAY_DOMAIN:-localhost}"
LIVE_DIR="/etc/letsencrypt/live/${DOMAIN}"

if [ ! -f "${LIVE_DIR}/fullchain.pem" ] || [ ! -f "${LIVE_DIR}/privkey.pem" ]; then
    echo "No cert found for ${DOMAIN}, generating self-signed certificate..."
    mkdir -p "${LIVE_DIR}"
    openssl req -x509 -nodes -days 365 \
        -subj "/CN=${DOMAIN}" \
        -newkey rsa:2048 \
        -keyout "${LIVE_DIR}/privkey.pem" \
        -out "${LIVE_DIR}/fullchain.pem" 2>/dev/null
    echo "Certificate generated for ${DOMAIN}"
fi

# Template the nginx configuration with environment variables
envsubst '${GATEWAY_DOMAIN}' < /etc/nginx/nginx.conf.template > /etc/nginx/nginx.conf

# Execute the original entrypoint
exec "$@"
