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

Behaviour worth knowing (security review 2026-10-12):
- A channel once turned on stays required. If its settings disappear (SMTP host / SMS provider or token removed),
  the sign-in stops with "not set up for this hospital" instead of letting the person in with the password alone;
  a Keycloak admin turns the person's `nidan_2fa_*` attribute off to let them in.
- The "on" flag holds a hash of the email / phone it was confirmed for: changing the address by any route turns the
  channel off until the person confirms the new one.
- The SMS token is read from the Keycloak container's `NIDAN_SMS_TOKEN`, not stored in the realm (realm attributes
  are shown unmasked to realm admins and in exports).
- Accepted limits: guesses during set-up are not counted by Keycloak's brute-force lockout (each set-up allows
  3 sends x 5 tries; a wrong set-up only leaves the channel off), and parallel requests on one login session can try
  a code more than 5 times (every wrong sign-in code still counts towards the lockout).

