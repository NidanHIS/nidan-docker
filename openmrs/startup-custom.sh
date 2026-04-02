#!/bin/bash -aeu
#       Custom startup script for Nidan (PostgreSQL URL fix; no custom dialect)

source /openmrs/startup-init.sh

DATA_DIR="${OMRS_DATA_DIR:-/openmrs/data}"
mkdir -p "$DATA_DIR"

# Ensure Initializer configuration exists (required for metadata loading)
if [ -d /openmrs/distribution/openmrs_config ]; then
  CONFIG_DEST="$DATA_DIR/configuration"
  mkdir -p "$CONFIG_DEST"
  # Skip overwrite when configuration is a mount point (local config for debugging)
  if grep -q " $CONFIG_DEST " /proc/mounts 2>/dev/null; then
    echo "Configuration is a mount point - using existing config (not overwriting)"
    # Force OCL reload: when using local config, clear OCL checksums so Initializer
    # reloads concepts (avoids skip after DB reset or stale checksums)
    if [ -d "$DATA_DIR/configuration_checksums/ocl" ]; then
      echo "Clearing OCL checksums to force concept reload (mounted config)"
      rm -rf "$DATA_DIR/configuration_checksums/ocl"
    fi
  else
    echo "Ensuring Initializer configuration at $CONFIG_DEST"
    if command -v rsync >/dev/null 2>&1; then
      rsync -a --delete /openmrs/distribution/openmrs_config/ "$CONFIG_DEST/"
    else
      find "$CONFIG_DEST" -mindepth 1 -delete 2>/dev/null || true
      cp -R /openmrs/distribution/openmrs_config/* "$CONFIG_DEST/"
    fi
  fi
  # Copy OCL zip files from /ocl-zips mount (if present) into configuration/ocl/
  if [ -d /ocl-zips ] && ls /ocl-zips/*.zip 1>/dev/null 2>&1; then
    echo "Copying OCL zip files from /ocl-zips into configuration/ocl/"
    mkdir -p "$CONFIG_DEST/ocl/referenceapplication-demo"
    cp -v /ocl-zips/*.zip "$CONFIG_DEST/ocl/referenceapplication-demo/" 2>/dev/null || true
  fi
fi

# Copy property files to data dir (appointment.properties, etc.)
if [ -d /openmrs/distribution/openmrs_properties ]; then
  cp -n /openmrs/distribution/openmrs_properties/* "$DATA_DIR/" 2>/dev/null || true
fi

echo "Waiting for database to initialize..."

/openmrs/wait-for-it.sh -t 3600 -h "${OMRS_DB_HOSTNAME}" -p "${OMRS_DB_PORT}"

wait_for_es()
{
        IFS=',' read -a es_uris <<< "${OMRS_SEARCH_ES_URIS}"
    
    EXIT_STATUS=1  
    while [ $EXIT_STATUS -ne 0 ]
    do
                for es_uri in "${es_uris[@]}"; do
                        echo "Waiting for ES node ${es_uri} to initialize..."
                        ELASTIC_SEARCH_HOST_PORT=(${es_uri//// })
                        /openmrs/wait-for-it.sh -t 15 "${ELASTIC_SEARCH_HOST_PORT[1]}" 
                        EXIT_STATUS=$?
                        if [ $EXIT_STATUS -eq 0 ]; then
                break 
            fi
            
                done
    done
    
    return 0
}

if [ "${OMRS_SEARCH}" = "elasticsearch" ]; then
        set +e
        echo "Waiting for ElasticSearch ${OMRS_SEARCH_ES_URIS} to initialize..."
        wait_for_es
        set -e
fi

TOMCAT_DIR="/usr/local/tomcat"
TOMCAT_WEBAPPS_DIR="$TOMCAT_DIR/webapps"
TOMCAT_WORK_DIR="$TOMCAT_DIR/work"
TOMCAT_TEMP_DIR="$TOMCAT_DIR/temp"
TOMCAT_SETENV_FILE="$TOMCAT_DIR/bin/setenv.sh"

echo "Clearing out Tomcat directories"
rm -fR "${TOMCAT_WEBAPPS_DIR:?}/*"
rm -fR "${TOMCAT_WORK_DIR:?}/*"
rm -fR "${TOMCAT_TEMP_DIR:?}/*"

echo "Loading WAR into appropriate location"
cp -r "${OMRS_DISTRO_CORE}/." "${TOMCAT_WEBAPPS_DIR}"

# Overlay locally built/custom modules (if mounted) into OpenMRS module directory.
# This keeps custom module updates (e.g. bahmni-ipd) active across container restarts.
if [ -d /openmrs/custom_modules ] && ls /openmrs/custom_modules/*.omod 1>/dev/null 2>&1; then
  echo "Copying custom OMODs from /openmrs/custom_modules -> ${DATA_DIR}/modules"
  mkdir -p "${DATA_DIR}/modules"
  cp -f /openmrs/custom_modules/*.omod "${DATA_DIR}/modules/"
fi

if [ -f "${OMRS_RUNTIME_PROPERTIES_FILE}" ]; then
  echo "Force updating ${OMRS_RUNTIME_PROPERTIES_FILE} with database connection"
  # Update connection.url and remove potential backslash escapes
  # We'll use a more direct approach to make sure the URL is correct
  # Some OpenMRS images write escaped values (jdbc\:postgresql\://...) and we have
  # seen cases where the URL is split across lines, leaving a stray "/openmrs?..." line.
  # This breaks properties parsing and can also lead to CLOB handling issues.
  if grep -q '^/openmrs\?' "${OMRS_RUNTIME_PROPERTIES_FILE}"; then
    sed -i '/^\/openmrs\?/d' "${OMRS_RUNTIME_PROPERTIES_FILE}"
  fi

  if [ -n "${OMRS_DB_URL-}" ]; then
    # Force a clean, unescaped JDBC URL from the container environment.
    # PostgreSQL: add stringtype=unspecified for CLOB handling. MySQL: no change.
    url="${OMRS_DB_URL}"
    if [[ "$url" == *"postgresql"* ]] && [[ ! "$url" =~ "stringtype=unspecified" ]]; then
      if [[ "$url" == *"?"* ]]; then
        url="${url}&stringtype=unspecified"
      else
        url="${url}?stringtype=unspecified"
      fi
    fi
    # Escape & for sed replacement (sed treats & as "matched text" placeholder)
    url_sed="${url//&/\\&}"
    if grep -q '^connection\.url=' "${OMRS_RUNTIME_PROPERTIES_FILE}"; then
      sed -i "s|^connection\.url=.*|connection.url=${url_sed}|" "${OMRS_RUNTIME_PROPERTIES_FILE}"
    else
      echo "connection.url=${url}" >> "${OMRS_RUNTIME_PROPERTIES_FILE}"
    fi
  fi

  # Disable Elasticsearch discovery so context refresh doesn't block on es:9200 (no ES in stack)
  sed -i.bak 's|^hibernate\.search\.backend\.uris=.*|hibernate.search.backend.uris=|' "${OMRS_RUNTIME_PROPERTIES_FILE}" 2>/dev/null || true
  sed -i.bak 's|^hibernate\.search\.backend\.discovery\.enabled=.*|hibernate.search.backend.discovery.enabled=false|' "${OMRS_RUNTIME_PROPERTIES_FILE}" 2>/dev/null || true
  grep -q '^hibernate\.search\.backend\.uris=' "${OMRS_RUNTIME_PROPERTIES_FILE}" || echo "hibernate.search.backend.uris=" >> "${OMRS_RUNTIME_PROPERTIES_FILE}"
  grep -q '^hibernate\.search\.backend\.discovery\.enabled=' "${OMRS_RUNTIME_PROPERTIES_FILE}" || echo "hibernate.search.backend.discovery.enabled=false" >> "${OMRS_RUNTIME_PROPERTIES_FILE}"
  rm -f "${OMRS_RUNTIME_PROPERTIES_FILE}.bak" 2>/dev/null || true

  cat "${OMRS_RUNTIME_PROPERTIES_FILE}"
fi

echo "Writing out $TOMCAT_SETENV_FILE file"

JAVA_OPTS="$OMRS_JAVA_SERVER_OPTS"
# Override Hibernate Search to avoid blocking on es:9200 (no ES in stack) - JVM -D takes precedence
CATALINA_OPTS="${OMRS_JAVA_MEMORY_OPTS} -DOPENMRS_INSTALLATION_SCRIPT=${OMRS_SERVER_PROPERTIES_FILE} -DOPENMRS_APPLICATION_DATA_DIRECTORY=${OMRS_DATA_DIR}/ -Dhibernate.search.backend.uris= -Dhibernate.search.backend.discovery.enabled=false"

if [ -n "${OMRS_DEV_DEBUG_PORT-}" ]; then
  echo "Enabling debugging on port ${OMRS_DEV_DEBUG_PORT}"
  
  JAVA_VERSION=$(java -version 2>&1 | awk -F '"' '/version/ {print $2}' | awk -F '.' '/.*/ {print $1}')
  
  if [[ "$JAVA_VERSION" -gt "8" ]]; then
        CATALINA_OPTS="$CATALINA_OPTS -agentlib:jdwp=transport=dt_socket,server=y,suspend=n,address=*:${OMRS_DEV_DEBUG_PORT}"
  else
        CATALINA_OPTS="$CATALINA_OPTS -agentlib:jdwp=transport=dt_socket,server=y,suspend=n,address=${OMRS_DEV_DEBUG_PORT}"
  fi
fi

cat > $TOMCAT_SETENV_FILE << EOF
export JAVA_OPTS="$JAVA_OPTS"
export CATALINA_OPTS="$CATALINA_OPTS"
EOF

echo "Starting up OpenMRS..."

# Heartbeat: Spring context refresh produces no logs for 15-25 min; echo progress so docker logs shows activity
(
  while true; do
    sleep 120
    echo "[$(date +%H:%M:%S)] OpenMRS still starting... (context refresh can take 15-25 min with many modules)"
  done
) &
HEARTBEAT_PID=$!
trap "kill $HEARTBEAT_PID 2>/dev/null || true" EXIT

/usr/local/tomcat/bin/catalina.sh run &

# Trigger first filter to start data import (Initializer runs on first request)
echo "Waiting for Tomcat to initialize (60s)..."
sleep 60
for i in 1 2 3 4 5 6 7 8 9 10; do
  if curl -sf "http://localhost:8080/${OMRS_WEBAPP_NAME}/" > /dev/null; then
    kill $HEARTBEAT_PID 2>/dev/null || true
    echo "OpenMRS responded; Initializer will load metadata."
    break
  fi
  echo "Attempt $i/10: waiting 15s..."
  sleep 15
done

# Bring tomcat process to foreground again
wait ${!}
