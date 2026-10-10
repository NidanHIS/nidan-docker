# syntax=docker/dockerfile:1
FROM nginx:alpine

RUN apk add --no-cache openssl

# Copy custom configuration
COPY nginx.conf /etc/nginx/nginx.conf.template
COPY client-identity-headers.conf /etc/nginx/client-identity-headers.conf
COPY odoo-public-proxy.conf /etc/nginx/odoo-public-proxy.conf
COPY gateway-page-headers.conf /etc/nginx/gateway-page-headers.conf
COPY launcher-page-headers.conf /etc/nginx/launcher-page-headers.conf
# Central-login snippets; the entrypoint activates them only with NIDAN_SSO_ENABLED=true (KC-10)
COPY auth/ /etc/nginx/auth-templates/

# Script to template nginx config at runtime
COPY docker-entrypoint.sh /usr/local/bin/docker-entrypoint.sh
# Daily access-log rotation, 365-day retention (KC-13-03); busybox crond runs it
COPY --chmod=755 rotate-access-log.sh /usr/local/bin/rotate-access-log.sh
COPY --chmod=755 hsts-decide.sh /usr/local/bin/hsts-decide.sh
RUN chmod +x /usr/local/bin/docker-entrypoint.sh
COPY index.html.template /etc/nginx/templates/index.html.template
COPY 502.html /usr/share/nginx/html/502.html
COPY not-allowed.html /usr/share/nginx/html/not-allowed.html
COPY staff/ /usr/share/nginx/html/staff/
COPY outage.html.template /etc/nginx/templates/outage.html.template
COPY assets/ /usr/share/nginx/html/assets/
COPY nidan-logo.png /usr/share/nginx/html/nidan-logo.png
COPY trigonal-logo.png /usr/share/nginx/html/trigonal-logo.png

# Generate self-signed cert if none provided in /etc/letsencrypt/live/$GATEWAY_DOMAIN
COPY generate-selfsigned.sh /docker-entrypoint.d/00-generate-selfsigned.sh
RUN chmod +x /docker-entrypoint.d/00-generate-selfsigned.sh

# Set the entrypoint to our script
ENTRYPOINT ["/usr/local/bin/docker-entrypoint.sh"]

# Default command (nginx)
CMD ["nginx", "-g", "daemon off;"]

# Expose ports
EXPOSE 80 443
