# Branding (launcher + Keycloak login page)

One folder brands both the gateway's launcher and the Keycloak login page.

| file | used for |
|---|---|
| `logo.svg` | logo on the launcher (top) and above the login form |
| `brand.css` | colours: `--brand-primary`, `--brand-bg`, `--brand-text` |
| `favicon.ico` | browser-tab icon of the launcher, status pages, Staff page and the login pages |
| `background.svg` | sign-in page background (default: original digital-health line art: stethoscope, capsules, crosses) |
| `background.jpg` | optional photo for the sign-in page; drawn over `background.svg` when present |
| `messages_en.json`, `messages_ne.json` | optional text overrides for the launcher, same keys as `assets/i18n/messages_*.json`, e.g. `{"launcher.title": "Welcome to Dolakha Hospital"}` |

To rebrand a hospital: copy this folder somewhere outside the repo, edit it, set `NIDAN_BRANDING_DIR=/path/to/it` in
`.env.local`, then `docker compose up -d gateway` (the folder is mounted read-only over the default). Keep the file
names; `logo.svg` may be replaced by any SVG. The hospital's name is `NIDAN_BRAND_NAME` (plain text): page and login tab titles, the line under the logo on the
sign-in page and its copyright line ("© <year> <name>. All rights reserved."). The sign-in page also has English /
नेपाली links at the top right.
