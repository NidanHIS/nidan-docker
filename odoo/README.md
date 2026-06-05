# Odoo service

The Odoo 19 image is a two-layer stack:

| Layer | Image | Built from | Contains |
|---|---|---|---|
| Base (open-source) | `trigonaltechnology/nidan_odoo:stable` | `odoo_19_addons/packages/Dockerfile` | open-source addons at `/mnt/extra-addons` + entrypoint |
| Pro (private) | `trigonaltechnology/nidan_odoo_pro:stable` | `nidan_odoo_extra_addons/packages/Dockerfile` (`FROM` the base) | closed-source addons at `/mnt/private-addons` |

- **Open-source build:** see `odoo_19_addons/packages/BUILD.md`.
- **Private build:** see `nidan_odoo_extra_addons/packages/BUILD.md`.
- **Build order:** publish the base first, then build the Pro image on top of it
  (the Pro image's first-run auto-install relies on the base entrypoint's
  `ADDONS_INIT_DIRS` support).

## Which image runs

Production `docker-compose.yml` pulls `${ODOO_IMAGE:-trigonaltechnology/nidan_odoo:stable}`.
Hospitals that need the proprietary modules set in `.env`:

```
ODOO_IMAGE=trigonaltechnology/nidan_odoo_pro:stable
```

Addons are **baked into the image** in production — no host source is mounted, so
`/mnt/extra-addons` and `/mnt/private-addons` come straight from the image. The
runtime `odoo.conf` (`./odoo/odoo.conf`, mounted read-only) sets `addons_path` to
include both. For live addon editing, use `dev.docker-compose.yml`, which
bind-mounts `odoo_19_addons` and `nidan_odoo_extra_addons` into those paths.

This directory is kept for compose wiring (`odoo.conf` template, `odoo-db` init);
the app images come from the addons repos.
