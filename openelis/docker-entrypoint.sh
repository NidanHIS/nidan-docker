#!/bin/sh

ENV_SECRETS_DIR="/run/secrets"

file_env_secret() {
    secret_name="$1"
    secret_file="${ENV_SECRETS_DIR}/${secret_name}"
    if [ -f "${secret_file}" ]; then
        secret_val=$(cat "${secret_file}")
        export CATALINA_OPTS="${CATALINA_OPTS} -D${secret_name}=${secret_val}"
    else
        echo "Secret file does not exist! ${secret_file}"
    fi
}

#must stay the same as filename in docker-compose.yml
file_env_secret "datasource.password"

# Configure context path from environment variable
# Default to /api/OpenELIS-Global/ if not set (maintains backward compatibility)
SERVER_SERVLET_CONTEXT_PATH="${SERVER_SERVLET_CONTEXT_PATH:-/api/OpenELIS-Global}"

# Ensure context path starts with / and ends with /
CONTEXT_PATH=$(echo "$SERVER_SERVLET_CONTEXT_PATH" | sed 's|^/*|/|' | sed 's|/*$|/|')

# Also set the API path (for ROOT context)
API_PATH="/api"

# Replace context path in server.xml template
sed "s|path=\"/api/OpenELIS-Global/\"|path=\"${CONTEXT_PATH}\"|g" \
    /usr/local/tomcat/conf/server.xml.template > /usr/local/tomcat/conf/server.xml

# Replace API path for ROOT context
sed -i "s|path=\"/api\"|path=\"${API_PATH}\"|g" /usr/local/tomcat/conf/server.xml

echo "OpenELIS context path configured as: ${CONTEXT_PATH}"
echo "API context path configured as: ${API_PATH}"

$CATALINA_HOME/bin/catalina.sh run

