# syntax=docker/dockerfile:1
FROM osimis/orthanc:23.6.1

# Copy custom config
COPY orthanc.json /etc/orthanc/orthanc.json

# Add any plugins if needed
ENV ORTHANC_NAME="NidanOrthanc"

EXPOSE 8042 4242
