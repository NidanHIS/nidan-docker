#!/bin/bash
set -e

# Substitute environment variables in orthanc.json
# Write to temp file first, then move (to handle permissions)
envsubst < /etc/orthanc/orthanc.json.template > /tmp/orthanc.json
mv /tmp/orthanc.json /etc/orthanc/orthanc.json

# Call the original orthancteam/orthanc entrypoint which handles plugin activation
exec /docker-entrypoint.sh "$@"
