# Orthanc Configuration for Nidan EHR

This directory contains Orthanc PACS server configuration for the Nidan EHR system.

## Directory Structure

```
orthanc/
├── orthanc.json    # Static Orthanc configuration
├── worklists/      # MWL worklist files directory
│   └── .gitkeep
└── README.md       # This file
```

## Enabled Plugins

| Plugin | Purpose |
|--------|---------|
| **Worklists** | Modality Worklist SCP - responds to C-FIND MWL queries |
| **DICOMweb** | WADO-RS, QIDO-RS, STOW-RS APIs |
| **OHIF** | Integrated OHIF viewer |
| **PostgreSQL** | Index/metadata backend (`ENABLE_INDEX=true`). DICOM files are **not** in PG — they live on the filesystem (`ENABLE_STORAGE=false`), default `StorageDirectory` `/var/lib/orthanc/db`, backed by the `orthanc-data` volume |
| **Orthanc Explorer 2** | Modern web UI |

## Configuration Approach

- **Static settings** → `orthanc.json` (server ports, plugin settings, DICOM AET)
- **Dynamic/secrets** → Environment variables in `docker-compose.yml` (DB credentials, user auth)

## Modality Worklist (MWL)

External services (e.g., Nidan Integration Service) create `.wl` files in the `worklists/` directory. DICOM modalities query Orthanc via C-FIND to retrieve scheduled procedures.

### Creating Worklist Files (DCMTK)

```bash
# Convert XML to DICOM worklist format
xml2dcm worklist.xml output.wl

# Copy to worklists directory
cp output.wl ./orthanc/worklists/
```

### Testing MWL with findscu

```bash
# Query all worklists
findscu -W localhost 4242

# Query CT modality only
findscu -W -k "ScheduledProcedureStepSequence[0].Modality=CT" localhost 4242
```

## Access URLs

| Service | URL |
|---------|-----|
| Orthanc Explorer 2 | `https://<host>/orthanc-container/ui/app/` (orthanc-admin) |
| OHIF Viewer | `https://<host>/ohif/` (app-orthanc) |
| DICOMweb API | `https://<host>/dicom-web/` (app-orthanc, read only) |
| Via Gateway | `https://localhost/orthanc-container/ui/app/` |

## Security Notes

> ⚠️ `RemoteAccessAllowed: true` is for development. In production, restrict via firewall/reverse proxy.

## Accounts and the read-only viewer (backlog KC-30)

- `ORTHANC_USERNAME` / `ORTHANC_PASSWORD`: the admin account (CIS, and the Orthanc UI for `orthanc-admin` users).
- `nidan-viewer` / `ORTHANC_VIEWER_PASSWORD`: the account the gateway uses on the image viewer routes
  (`/ohif/`, `/dicom-web/`, `/wado/`). `viewer-readonly.lua` refuses it anything but GET/HEAD, whoever sends it.
- Nobody may call `/tools/execute-script`; `ExecuteLuaEnabled` and `RestApiWriteToFileSystemEnabled` are false.
- With central login on, browsers never see either password: the gateway adds them behind its role checks.
- Checked by `tests/e2e-auth/specs/orthanc.spec.ts` against the real image (`tests/e2e-auth/harness/orthanc.yml`).
