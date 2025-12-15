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
| **PostgreSQL** | Database storage backend |
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
| Orthanc Explorer 2 | `http://localhost:8042/ui/app/` |
| OHIF Viewer | `http://localhost:8042/ohif/` |
| DICOMweb API | `http://localhost:8042/dicom-web/` |
| Via Gateway | `https://localhost/orthanc-container/ui/app/` |

## Security Notes

> ⚠️ `RemoteAccessAllowed: true` is for development. In production, restrict via firewall/reverse proxy.
