# Keycloak extensions

`dist/` is mounted into Keycloak as `/opt/keycloak/providers` (docker-compose.yml and the test harness). The realm's
login flow uses `nidan-code-otp`, so build it before starting Keycloak:

```bash
cd keycloak/providers/nidan-code-otp
./build.sh            # compiles against a running Keycloak 26.8.0 container (KC_CONTAINER=<name>), runs the self-check
# or, with network: mvn -q package
cp target/nidan-code-otp.jar ../dist/
```

`nidan-code-otp`: email and SMS sign-in codes for the optional second step (see env.template, NIDAN_SMTP_* and
NIDAN_SMS_*). Codes are 6 digits, live 5 minutes, allow 5 tries, at most one new code a minute and three per
sign-in; only a salted hash is kept, in the login session. They go to the email / phone a hospital admin entered;
a person turns a channel on from the launcher after typing back a code sent there.
SMS providers: `sparrow` and `aakash` follow their published APIs and are **not yet tested against a live
account**; `webhook` is what the test harness uses.
