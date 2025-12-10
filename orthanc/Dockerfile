# syntax=docker/dockerfile:1
FROM osimis/orthanc:23.6.1

# Install envsubst for environment variable substitution
USER root
RUN apt-get update && apt-get install -y gettext-base && rm -rf /var/lib/apt/lists/*

# Copy custom config template
COPY orthanc.json /etc/orthanc/orthanc.json.template

# Copy entrypoint script and set permissions
COPY entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh && chown orthanc:orthanc /entrypoint.sh /etc/orthanc/orthanc.json.template && \
    chmod 755 /etc/orthanc && chown orthanc:orthanc /etc/orthanc

USER orthanc

# Add any plugins if needed
ENV ORTHANC_NAME="NidanOrthanc"

EXPOSE 8042 4242

ENTRYPOINT ["/entrypoint.sh"]
CMD ["Orthanc", "/etc/orthanc/"]
