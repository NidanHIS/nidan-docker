# syntax=docker/dockerfile:1
#
# Nidan OpenMRS Production Build (Option B)
# Full custom build from source: distro/ controls modules, custom_modules/ provides Nidan OMODs.
#
# Build: docker-compose build openmrs-backend
# Prerequisites: Populate custom_modules/ with .omod files (see custom_modules/README.md)
#
### Content clone stage
FROM alpine:3.19 AS content-clone
ARG CONTENT_REPO_URL="https://github.com/Bhalubang-Hospital/openmrs-content-referenceapplication-demo.git"
ARG CONTENT_REPO_REF="main"
ARG CACHE_BUST
RUN apk add --no-cache git && \
    echo "CACHE_BUST=${CACHE_BUST}" >/dev/null && \
    git clone --depth 1 --branch "${CONTENT_REPO_REF}" "${CONTENT_REPO_URL}" /content

### Dev stage (build)
FROM openmrs/openmrs-core:2.8.x-dev-amazoncorretto-21 AS dev
WORKDIR /openmrs_distro

ARG OMRS_PROFILE="distro"
ARG MVN_ARGS_SETTINGS="-B -P distro"
ARG CONTENT_VERSION="1.8.0-nidan-SNAPSHOT"
ARG CACHE_BUST

# Copy build files
COPY pom.xml ./
COPY distro ./distro/
COPY custom_modules /openmrs_distro/custom_modules/

# Copy cloned content
COPY --from=content-clone /content /tmp/openmrs-content-referenceapplication-demo

# Build: install custom OMODs → build content → build distro → copy artifacts → overlay config
RUN \
    mkdir -p /root/.m2 && \
    echo "statsEnabled=false" > /root/.m2/sdk-stats.properties && \
    echo "statsEnabled=false" > sdk-stats.properties && \
    echo "CACHE_BUST=${CACHE_BUST}" >/dev/null && \
    \
    sed -i "s|<version>1.8.0-SNAPSHOT</version>|<version>${CONTENT_VERSION}</version>|" /tmp/openmrs-content-referenceapplication-demo/pom.xml && \
    mvn $MVN_ARGS_SETTINGS -f /tmp/openmrs-content-referenceapplication-demo/pom.xml install -DskipTests -q && \
    \
    sed -i "s|<reference-demo-content.version>.*</reference-demo-content.version>|<reference-demo-content.version>${CONTENT_VERSION}</reference-demo-content.version>|" distro/pom.xml && \
    mvn $MVN_ARGS_SETTINGS clean install -Dmaven.deploy.skip=true && \
    \
    cp /openmrs_distro/distro/target/sdk-distro/web/openmrs_core/openmrs.war /openmrs/distribution/openmrs_core/ && \
    cp /openmrs_distro/distro/target/sdk-distro/web/openmrs-distro.properties /openmrs/distribution/ && \
    cp -R /openmrs_distro/distro/target/sdk-distro/web/openmrs_modules /openmrs/distribution/openmrs_modules/ && \
    cp -R /openmrs_distro/distro/target/sdk-distro/web/openmrs_owas /openmrs/distribution/openmrs_owas/ && \
    cp -R /openmrs_distro/distro/target/sdk-distro/web/openmrs_config /openmrs/distribution/openmrs_config/ && \
    cp -R /tmp/openmrs-content-referenceapplication-demo/configuration/* /openmrs/distribution/openmrs_config/ && \
    rm -rf /tmp/openmrs-content-referenceapplication-demo && \
    \
    mvn $MVN_ARGS_SETTINGS clean

### Run stage (production)
FROM openmrs/openmrs-core:2.8.x-amazoncorretto-21

# Copy built artifacts from dev stage
COPY --from=dev /openmrs/distribution/openmrs_core/openmrs.war /openmrs/distribution/openmrs_core/
COPY --from=dev /openmrs/distribution/openmrs-distro.properties /openmrs/distribution/
COPY --from=dev /openmrs/distribution/openmrs_modules /openmrs/distribution/openmrs_modules
COPY --from=dev /openmrs/distribution/openmrs_owas /openmrs/distribution/openmrs_owas
COPY --from=dev /openmrs/distribution/openmrs_config /openmrs/distribution/openmrs_config

# Overlay custom modules (bahmni-ipd, attachments, appointments, etc. — runtime-only or not in distro)
COPY custom_modules/ /openmrs/distribution/openmrs_modules/

# Property files for modules (appointment.properties, etc.) — copied to data dir at startup
COPY properties/ /openmrs/distribution/openmrs_properties/

# Diagnostic script for Initializer troubleshooting
COPY --chmod=755 check-initializer.sh /openmrs/check-initializer.sh

# Nidan startup (Initializer sync, JDBC URL cleanup, custom OMOD overlay, no-ES).
# Baked into the image — do not bind-mount over /openmrs/startup.sh (breaks on Windows).
COPY --chmod=755 startup-custom.sh /openmrs/startup.sh
