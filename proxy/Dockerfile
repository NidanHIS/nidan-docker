# syntax=docker/dockerfile:1
FROM nginx:alpine

RUN apk add --no-cache openssl

# Copy custom configuration
COPY nginx.conf /etc/nginx/nginx.conf
COPY index.html /usr/share/nginx/html/index.html
COPY nidan-logo.png /usr/share/nginx/html/nidan-logo.png
COPY trigonal-logo.png /usr/share/nginx/html/trigonal-logo.png

# Generate self-signed cert if none provided in /etc/letsencrypt/live/$GATEWAY_DOMAIN
COPY generate-selfsigned.sh /docker-entrypoint.d/00-generate-selfsigned.sh
RUN chmod +x /docker-entrypoint.d/00-generate-selfsigned.sh

# Expose ports
EXPOSE 80 443
