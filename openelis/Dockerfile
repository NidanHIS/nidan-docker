# syntax=docker/dockerfile:1
FROM tomcat:9-jdk11-openjdk-slim

# OpenELIS typically comes as a WAR. 
# In a real distro, we would download the war from a repo or build from source.
# For now, we assume we fetch a specific version.
# RUN wget https://github.com/StartRemotely/OpenELIS-Global-2/releases/download/v2.6/OpenELIS.war -O /usr/local/tomcat/webapps/openelis.war

# Placeholder for scaffolding
RUN echo "OpenELIS 2.0" > /usr/local/tomcat/webapps/openelis.html

EXPOSE 8080
