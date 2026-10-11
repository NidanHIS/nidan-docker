# Harness only: the published OpenMRS image + this repo's startup-custom.sh, sso/ template, sso_modules/ and the
# server.xml step of openmrs/Dockerfile (RemoteIpValve, access log without query strings).
FROM trigonaltechnology/openmrs-backend:latest
USER root
RUN sed -i -e 's#suffix=".txt"#suffix=".txt" maxDays="30"#' -e 's#&quot;%r&quot;#\&quot;%m %U %H\&quot;#' \
        -e 's#</Host>#  <Valve className="org.apache.catalina.valves.RemoteIpValve" protocolHeader="X-Forwarded-Proto" portHeader="X-Forwarded-Port"/>\n      </Host>#' \
        /usr/local/tomcat/conf/server.xml \
 && grep -q '%m %U %H' /usr/local/tomcat/conf/server.xml && ! grep -q '%r' /usr/local/tomcat/conf/server.xml \
 && [ "$(grep -c RemoteIpValve /usr/local/tomcat/conf/server.xml)" = 1 ]
COPY --chmod=755 startup-custom.sh /openmrs/startup.sh
COPY sso/ /openmrs/distribution/sso/
COPY sso_modules/ /openmrs/distribution/sso_modules/
USER 1001
