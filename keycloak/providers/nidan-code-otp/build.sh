#!/usr/bin/env bash
# Builds target/nidan-code-otp.jar without Maven downloads: compiles against the jars of a running Keycloak
# container of the SAME version as docker-compose.yml (26.8.0). CI with network can use pom.xml instead.
#   KC_CONTAINER=<name> ./build.sh        (default: the full test harness Keycloak)
set -euo pipefail
cd "$(dirname "$0")"
C=${KC_CONTAINER:-nidan-sso-openmrs-harness-keycloak-1}
LIB=target/kc-lib
mkdir -p "$LIB" target/classes target/test-classes
for j in main/org.keycloak.keycloak-server-spi-26.8.0.jar main/org.keycloak.keycloak-server-spi-private-26.8.0.jar \
         main/org.keycloak.keycloak-services-26.8.0.jar main/org.keycloak.keycloak-core-26.8.0.jar \
         main/org.keycloak.keycloak-common-26.8.0.jar main/jakarta.ws.rs.jakarta.ws.rs-api-3.1.0.jar \
         boot/org.jboss.logging.jboss-logging-3.6.3.Final.jar; do
  [ -f "$LIB/$(basename "$j")" ] || docker cp "$C:/opt/keycloak/lib/lib/$j" "$LIB/" >/dev/null
done
CP=$(ls "$LIB"/*.jar | tr '\n' ':')
javac --release 21 -Xlint:all,-processing,-serial -Werror -cp "$CP" -d target/classes $(find src/main/java -name '*.java')
cp -R src/main/resources/. target/classes/
# self-check (no JUnit needed): plain main() asserts on the code logic
javac --release 21 -cp "target/classes:$CP" -d target/test-classes $(find src/test/java -name '*.java')
java -ea -cp "target/classes:target/test-classes:$CP" org.nidan.keycloak.code.CodeLogicCheck
jar --create --file target/nidan-code-otp.jar -C target/classes .
mkdir -p ../dist && cp target/nidan-code-otp.jar ../dist/
echo "built target/nidan-code-otp.jar"
