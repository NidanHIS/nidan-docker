# syntax=docker/dockerfile:1
# Using orthancteam/orthanc which includes all plugins and supports env-var configuration
FROM orthancteam/orthanc:24.11.1

# Install envsubst for environment variable substitution
USER root
RUN apt-get update && apt-get install -y gettext-base && rm -rf /var/lib/apt/lists/*

# Create worklists directory
RUN mkdir -p /var/lib/orthanc/worklists && chown orthanc:orthanc /var/lib/orthanc/worklists

# Copy custom config template
COPY orthanc.json /etc/orthanc/orthanc.json.template

# Copy entrypoint script and set permissions
COPY entrypoint.sh /custom-entrypoint.sh
RUN chmod +x /custom-entrypoint.sh && chown orthanc:orthanc /custom-entrypoint.sh /etc/orthanc/orthanc.json.template && \
    chmod 755 /etc/orthanc && chown orthanc:orthanc /etc/orthanc

USER orthanc

ENV ORTHANC_NAME="NidanOrthanc"

EXPOSE 8042 4242

# Use our custom entrypoint which does envsubst then calls the original
ENTRYPOINT ["/custom-entrypoint.sh"]
CMD ["Orthanc", "/etc/orthanc/"]
