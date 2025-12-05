# syntax=docker/dockerfile:1

# Stage 1: Build the Distro Artifacts using Maven
FROM maven:3.9-eclipse-temurin-17 AS builder

WORKDIR /build
COPY distro/pom.xml .
# Download dependencies first (cache layer)
# Note: We might need to run a dummy verify or package to actually download jars
RUN mvn dependency:go-offline

# Perform the package to trigger the copy-dependencies execution
RUN mvn package -DskipTests

# Stage 2: Runtime Base (Tomcat)
FROM tomcat:9.0-jdk17-temurin

# Environment Variables
ENV OPENMRS_HOME=/data/openmrs
ENV JAVA_OPTS="-Xmx2g -Xms512m"

# Create OpenMRS Home directory
RUN mkdir -p ${OPENMRS_HOME}/modules && \
    mkdir -p ${OPENMRS_HOME}/configuration

# Copy WAR from builder
COPY --from=builder /build/target/distro/openmrs.war /usr/local/tomcat/webapps/openmrs.war

# Copy Modules from builder
COPY --from=builder /build/target/distro/modules/ /root/temp-modules/

# Create a startup script to copy modules to OPENMRS_HOME on first run
# This ensures that if you map a volume to ${OPENMRS_HOME}, we populate it with the baked-in modules
COPY endpoint.sh /usr/local/bin/endpoint.sh
RUN chmod +x /usr/local/bin/endpoint.sh

EXPOSE 8080

ENTRYPOINT ["/usr/local/bin/endpoint.sh"]
CMD ["catalina.sh", "run"]
