# syntax=docker/dockerfile:1
FROM node:18-alpine AS builder

WORKDIR /app

# In a real distro, you would check out the openmrs-esm-core or a custom app shell repo
# For this scaffolding, we will simulate building/downloading the O3 RefApp shell
# using the standard npx openmrs tool to assemble the frontend.

# This is a placeholder for the actual O3 build process which can be complex
# We will create a basic 'spa' directory with an index.html ensuring we satisfy the requirement
RUN mkdir -p spa

# Perform the O3 Assembly
COPY spa-build-config.json .

# We use the openmrs CLI to assemble the frontend based on the config
# Using 'latest' for stability. Removing --manifest might ensure full build.
RUN npx --legacy-peer-deps openmrs@latest assemble --manifest --mode config --config spa-build-config.json --target ./spa

# Debug: Check if index.html exists
RUN ls -la ./spa

# The 'spa' directory now contains index.html, importmap.json, and all assets

FROM nginx:alpine
# Remove default nginx static assets
RUN rm -rf /usr/share/nginx/html/*

COPY --from=builder /app/spa /usr/share/nginx/html
# Copy the demo config as the main config.json
COPY config-core_demo.json /usr/share/nginx/html/config.json
# Custom nginx config for SPA fallback usually goes here
COPY nginx-spa.conf /etc/nginx/conf.d/default.conf
