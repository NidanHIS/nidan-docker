# syntax=docker/dockerfile:1
FROM nginx:alpine

# Copy custom configuration
COPY nginx.conf /etc/nginx/nginx.conf
COPY index.html /usr/share/nginx/html/index.html
COPY nidan-logo.png /usr/share/nginx/html/nidan-logo.png
COPY trigonal-logo.png /usr/share/nginx/html/trigonal-logo.png

# Expose ports
EXPOSE 80 443
