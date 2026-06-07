# syntax=docker/dockerfile:1
FROM nginx:alpine

RUN apk add --no-cache openssl

# Copy custom configuration
COPY nginx.conf /etc/nginx/nginx.conf.template

# Script to template nginx config at runtime
COPY docker-entrypoint.sh /usr/local/bin/docker-entrypoint.sh
RUN chmod +x /usr/local/bin/docker-entrypoint.sh
COPY index.html.template /etc/nginx/templates/index.html.template
COPY 502.html /usr/share/nginx/html/502.html
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
