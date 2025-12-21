# syntax=docker/dockerfile:1
##
# Build Stage
# Note: Build context should be set to the parent directory (nidanhis) to access OpenELIS-Global-2
#
FROM --platform=$BUILDPLATFORM maven:3-eclipse-temurin-21 AS build

ARG TARGETPLATFORM
ARG BUILDPLATFORM

RUN --mount=target=/var/lib/apt/lists,type=cache,sharing=locked \
    --mount=target=/var/cache/apt,type=cache,sharing=locked \
    rm -f /etc/apt/apt.conf.d/docker-clean \
    && apt-get -y update \
    && apt-get -y --no-install-recommends install \
    git apache2-utils nodejs npm

# OE Default Password
ARG DEFAULT_PW="adminADMIN!"
COPY OpenELIS-Global-2/install/createDefaultPassword.sh /build/install/createDefaultPassword.sh
WORKDIR /build
RUN ./install/createDefaultPassword.sh -c -p ${DEFAULT_PW}

##
# Build DataExport
#
COPY OpenELIS-Global-2/dataexport /build/dataexport
WORKDIR /build/dataexport/dataexport-core
RUN --mount=type=cache,target=/root/.m2,sharing=locked \
    mvn dependency:go-offline 
RUN --mount=type=cache,target=/root/.m2,sharing=locked \
    mvn clean install -DskipTests
WORKDIR /build/dataexport/
RUN --mount=type=cache,target=/root/.m2,sharing=locked \
    mvn dependency:go-offline 
RUN --mount=type=cache,target=/root/.m2,sharing=locked \
    mvn clean install -DskipTests

##
# Build the Project
#  
WORKDIR /build

COPY OpenELIS-Global-2/pom.xml /build/pom.xml
RUN --mount=type=cache,target=/root/.m2,sharing=locked \
    mvn dependency:go-offline 

ARG SKIP_SPOTLESS="false"
COPY OpenELIS-Global-2/src /build/src
RUN --mount=type=cache,target=/root/.m2,sharing=locked \
    mvn clean install -Dmaven.test.skip=true -DskipITs=true -Dspotless.check.skip=${SKIP_SPOTLESS}

##
# Run Stage
#
FROM tomcat:10-jre21

# Install sed for context path replacement
RUN apt-get update && apt-get install -y sed && rm -rf /var/lib/apt/lists/*

COPY OpenELIS-Global-2/install/createDefaultPassword.sh ./

#Clean out unneccessary files from tomcat (especially pre-existing applications) 
RUN rm -rf /usr/local/tomcat/webapps/* \ 
    /usr/local/tomcat/conf/Catalina/localhost/manager.xml

#Deploy the war into tomcat image
COPY --from=build /build/target/OpenELIS-Global.war /usr/local/tomcat/webapps/OpenELIS-Global.war

# Copy tomcat configuration files
COPY OpenELIS-Global-2/install/tomcat-resources/catalina.properties /usr/local/tomcat/conf/catalina.properties
COPY OpenELIS-Global-2/install/tomcat-resources/logging.properties /usr/local/tomcat/conf/logging.properties

#replace ServerInfo.properties with a less informative one
RUN mkdir -p /usr/local/tomcat/lib/org/apache/catalina/util
COPY OpenELIS-Global-2/install/tomcat-resources/ServerInfo.properties /usr/local/tomcat/lib/org/apache/catalina/util/ServerInfo.properties 

# Copy server.xml template (will be modified by entrypoint)
COPY OpenELIS-Global-2/tomcat/oe_server.xml /usr/local/tomcat/conf/server.xml.template

#restrict files
#GID AND UID must be kept the same as setupTomcat.sh (if using default certificate group)
RUN groupadd tomcat; \
    groupadd tomcat-ssl-cert -g 8443; \ 
    useradd -M -s /bin/bash -u 8443 tomcat_admin; \
    usermod -a -G tomcat,tomcat-ssl-cert tomcat_admin; \
    chown -R tomcat_admin:tomcat $CATALINA_HOME; \
    chmod g-w,o-rwx $CATALINA_HOME; \
    chmod g-w,o-rwx $CATALINA_HOME/conf; \
    chmod o-rwx $CATALINA_HOME/logs; \
    chmod o-rwx $CATALINA_HOME/temp; \
    chmod g-w,o-rwx $CATALINA_HOME/bin; \
    chmod g-w,o-rwx $CATALINA_HOME/webapps; \
    chmod 770 $CATALINA_HOME/conf/catalina.policy; \
    chmod g-w,o-rwx $CATALINA_HOME/conf/catalina.properties; \
    chmod g-w,o-rwx $CATALINA_HOME/conf/context.xml; \
    chmod g-w,o-rwx $CATALINA_HOME/conf/logging.properties; \
    chmod g-w,o-rwx $CATALINA_HOME/conf/server.xml.template; \
    chmod g-w,o-rwx $CATALINA_HOME/conf/tomcat-users.xml; \
    chmod g-w,o-rwx $CATALINA_HOME/conf/web.xml; \
    mkdir -p /var/lib/openelis-global/logs/; \
    chown -R tomcat_admin:tomcat /var/lib/openelis-global/logs/;\
    mkdir -p /var/lib/openelis-global/properties/; \
    chown -R tomcat_admin:tomcat /var/lib/openelis-global/properties/; \
    mkdir -p /var/lib/openelis-global/configuration/; \
    chown -R tomcat_admin:tomcat /var/lib/openelis-global/configuration/;

COPY OpenELIS-Global-2/install/openelis_healthcheck.sh /healthcheck.sh
RUN chown tomcat_admin:tomcat /healthcheck.sh; \
    chmod 770 /healthcheck.sh;  

# Copy custom entrypoint that handles context path configuration
COPY nidan-docker/openelis/docker-entrypoint.sh /docker-entrypoint.sh
RUN chown tomcat_admin:tomcat /docker-entrypoint.sh; \
    chmod 770 /docker-entrypoint.sh;

RUN mkdir -p /var/lib/lucene_index; \
    chown -R tomcat_admin:tomcat /var/lib/lucene_index; \
    chmod -R 770 /var/lib/lucene_index;

USER tomcat_admin

ENTRYPOINT [ "/docker-entrypoint.sh" ]
