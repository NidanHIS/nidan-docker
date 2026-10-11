# Harness only: the published OpenELIS image with a WAR built from the working tree.
FROM --platform=linux/amd64 trigonaltechnology/openelis-global-2:latest
COPY --chown=tomcat_admin:tomcat OpenELIS-Global.war /usr/local/tomcat/webapps/OpenELIS-Global.war
