# syntax=docker/dockerfile:1
FROM node:20-alpine AS builder

WORKDIR /app

# In a real distro, you would check out the openmrs-esm-core or a custom app shell repo
# For this scaffolding, we will simulate building/downloading the O3 RefApp shell
# using the standard npx openmrs tool to assemble the frontend.

# This is a placeholder for the actual O3 build process which can be complex
# We will create a basic 'spa' directory with an index.html ensuring we satisfy the requirement
RUN mkdir -p spa

# Perform the O3 Assembly
# Perform the O3 Assembly
COPY spa-build-config.json spa-assemble-config.json ./
# Make sure custom index and root-config are available
COPY index.html root-config.js ./

# We use the openmrs CLI to assemble the frontend based on the config
# This will download and assemble all the microfrontends specified in the config
RUN npx --legacy-peer-deps openmrs@next assemble --manifest --mode config --config spa-assemble-config.json --target ./spa

# Overwrite the default index.html with our custom one if strictly needed,
# or ensure it's placed where we want it.
RUN cp index.html ./spa/index.html && cp root-config.js ./spa/root-config.js

# Debug: Check if index.html exists
RUN ls -la ./spa

# The 'spa' directory now contains index.html, importmap.json, and all assets


FROM nginx:alpine

# Install envsubst (via gettext)
RUN apk update && \
    apk upgrade && \
    apk add --no-cache gettext

# Remove default nginx static assets
RUN rm -rf /usr/share/nginx/html/*

COPY --from=builder /app/spa /usr/share/nginx/html
# Copy the demo config as config-core_demo.json (nginx gateway will map /config.json to this)
COPY config-core_demo.json /usr/share/nginx/html/config-core_demo.json
# Custom nginx config for SPA fallback usually goes here
COPY nginx-spa.conf /etc/nginx/conf.d/default.conf

COPY startup.sh /usr/local/bin/startup.sh
RUN chmod +x /usr/local/bin/startup.sh

# Use the startup script to handle env substitutions
CMD ["/usr/local/bin/startup.sh"]
