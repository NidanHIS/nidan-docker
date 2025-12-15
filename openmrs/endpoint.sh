#!/bin/bash
set -e

echo "Starting OpenMRS Container..."

# Ensure modules directory exists
mkdir -p ${OPENMRS_HOME}/modules

# Copy baked-in modules to the runtime directory
# We use -n (no clobber) to avoid overwriting newer modules if volume is persisted
# OR we use -u (update) to overwrite if the image has newer built modules
echo "Initializing modules..."
cp -u /root/temp-modules/*.jar ${OPENMRS_HOME}/modules/

# Check if an OpenMRS runtime properties file exists, if not, maybe create a default one?
# For now, we rely on environment variables or volume mounts

exec "$@"
