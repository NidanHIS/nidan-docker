#!/bin/bash
set -e

# Substitute environment variables in orthanc.json
# Write to temp file first, then move (to handle permissions)
envsubst < /etc/orthanc/orthanc.json.template > /tmp/orthanc.json
mv /tmp/orthanc.json /etc/orthanc/orthanc.json

# Start Orthanc (pass through to original entrypoint/CMD)
exec "$@"

