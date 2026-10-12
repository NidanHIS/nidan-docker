# Branding (launcher + Keycloak login page)

One folder brands both the gateway's launcher and the Keycloak login page.

| file | used for |
|---|---|
| `logo.svg` | logo on the launcher (top) and above the login form |
| `brand.css` | colours: `--brand-primary`, `--brand-bg`, `--brand-text` |
| `messages_en.json`, `messages_ne.json` | optional text overrides for the launcher, same keys as `assets/i18n/messages_*.json`, e.g. `{"launcher.title": "Welcome to Dolakha Hospital"}` |

To rebrand a hospital: copy this folder somewhere outside the repo, edit it, set `NIDAN_BRANDING_DIR=/path/to/it` in
`.env.local`, then `docker compose up -d gateway` (the folder is mounted read-only over the default). Keep the file
names; `logo.svg` may be replaced by any SVG. The hospital's name for page and login tab titles is `NIDAN_BRAND_NAME` (plain text; the login page
heading itself is a neutral "Sign in" under the logo).
