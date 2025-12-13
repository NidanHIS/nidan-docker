#!/bin/sh
set -e

# Template the nginx configuration with environment variables
envsubst '${GATEWAY_DOMAIN}' < /etc/nginx/nginx.conf.template > /etc/nginx/nginx.conf

# Execute the original entrypoint
exec "$@"
