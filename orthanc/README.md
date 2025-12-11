# Orthanc Modality Worklist (MWL) Configuration

This directory contains Orthanc PACS server configuration for the Nidan EHR system.

## Directory Structure

```
orthanc/
├── Dockerfile           # Docker build configuration
├── entrypoint.sh        # Container entrypoint script
├── orthanc.json         # Orthanc configuration file
├── worklists/           # MWL worklist files directory
│   └── .gitkeep
└── README.md            # This file
```

## Modality Worklist (MWL) Plugin

The Modality Worklist plugin is enabled and configured to allow DICOM modalities (X-ray, CT, MRI, US, etc.) to query Orthanc for scheduled procedures via C-FIND.

### How It Works

1. **External services** (e.g., Nidan Integration Service - Java/Spring) create DICOM worklist files (`.wl`) and write them to the `worklists/` directory.
2. **Orthanc** watches this directory and responds to C-FIND MWL queries with matching worklists.
3. **DICOM modalities** query Orthanc on port 4242 to retrieve their scheduled procedures.

### Configuration

The MWL plugin is configured in `orthanc.json`:

```json
"Worklists": {
    "Enable": true,
    "Database": "/var/lib/orthanc/worklists",
    "FilterIssuerAet": false,
    "LimitAnswers": 0
}
```

- **Database**: Path inside the container where worklist files are stored
- **FilterIssuerAet**: If `true`, only worklists matching the querying AET are returned
- **LimitAnswers**: Maximum number of results (0 = unlimited)

### Volume Mount

In `docker-compose.yml`, the worklists directory is mounted:

```yaml
volumes:
  - ./orthanc/worklists:/var/lib/orthanc/worklists
```

## Creating Worklist Files

### Using DCMTK (xml2dcm)

1. Create a worklist XML file (e.g., `worklist.xml`):

```xml
<?xml version="1.0" encoding="UTF-8"?>
<meta-header xfer="1.2.840.10008.1.2.1" name="Little Endian Explicit">
</meta-header>
<data-set xfer="1.2.840.10008.1.2.1" name="Little Endian Explicit">
  <element tag="0008,0050" vr="SH" name="AccessionNumber">A12345</element>
  <element tag="0010,0010" vr="PN" name="PatientName">DOE^JOHN</element>
  <element tag="0010,0020" vr="LO" name="PatientID">P123456</element>
  <element tag="0010,0030" vr="DA" name="PatientBirthDate">19700101</element>
  <element tag="0010,0040" vr="CS" name="PatientSex">M</element>
  <sequence tag="0040,0100" name="ScheduledProcedureStepSequence">
    <item>
      <element tag="0008,0060" vr="CS" name="Modality">CT</element>
      <element tag="0040,0001" vr="AE" name="ScheduledStationAETitle">CT_SCANNER</element>
      <element tag="0040,0002" vr="DA" name="ScheduledProcedureStepStartDate">20231211</element>
      <element tag="0040,0003" vr="TM" name="ScheduledProcedureStepStartTime">100000</element>
      <element tag="0040,0006" vr="PN" name="ScheduledPerformingPhysicianName">SMITH^JANE^DR</element>
      <element tag="0040,0007" vr="LO" name="ScheduledProcedureStepDescription">CT Chest</element>
      <element tag="0040,0009" vr="SH" name="ScheduledProcedureStepID">SPS001</element>
    </item>
  </sequence>
  <element tag="0040,1001" vr="SH" name="RequestedProcedureID">RP001</element>
</data-set>
```

2. Convert to DICOM worklist file:

```bash
xml2dcm worklist.xml worklist.wl
```

3. Copy to the worklists directory:

```bash
cp worklist.wl ./orthanc/worklists/
```

### Using Java/Spring (dcm4che)

```java
Attributes attrs = new Attributes();
attrs.setString(Tag.AccessionNumber, VR.SH, "A12345");
attrs.setString(Tag.PatientName, VR.PN, "DOE^JOHN");
attrs.setString(Tag.PatientID, VR.LO, "P123456");
// ... add more attributes

Sequence spsSeq = attrs.newSequence(Tag.ScheduledProcedureStepSequence, 1);
Attributes sps = new Attributes();
sps.setString(Tag.Modality, VR.CS, "CT");
sps.setString(Tag.ScheduledProcedureStepStartDate, VR.DA, "20231211");
// ... add more SPS attributes
spsSeq.add(sps);

// Write to file
DicomOutputStream dos = new DicomOutputStream(new File("/path/to/worklists/A12345.wl"));
dos.writeDataset(null, attrs);
dos.close();
```

## Verification

### Check Plugin is Loaded

```bash
docker compose logs orthanc | grep -i worklist
```

Expected output should include:
```
Loading plugin(s) from: /usr/share/orthanc/plugins-available/libModalityWorklists.so
```

### Download Sample Worklist

```bash
curl -o orthanc/worklists/sample.wl \
  "https://orthanc.uclouvain.be/hg/orthanc/raw-file/default/OrthancServer/Plugins/Samples/ModalityWorklists/WorklistsDatabase/wklist1.wl"
```

### Query MWL with findscu (DCMTK)

```bash
# Query all worklists
findscu -W 127.0.0.1 4242

# Query CT modality worklists
findscu -W -k "ScheduledProcedureStepSequence[0].Modality=CT" 127.0.0.1 4242

# Query with specific Accession Number
findscu -W -k "AccessionNumber=A12345" 127.0.0.1 4242
```

## Required DICOM Attributes

At minimum, each worklist file should contain:

| Tag | Name | VR |
|-----|------|-----|
| (0008,0050) | Accession Number | SH |
| (0010,0010) | Patient Name | PN |
| (0010,0020) | Patient ID | LO |
| (0040,0100) | Scheduled Procedure Step Sequence | SQ |
| (0040,0001) | → Scheduled Station AE Title | AE |
| (0040,0002) | → Scheduled Procedure Step Start Date | DA |
| (0040,0003) | → Scheduled Procedure Step Start Time | TM |
| (0008,0060) | → Modality | CS |
| (0040,0007) | → Scheduled Procedure Step Description | LO |
| (0040,0009) | → Scheduled Procedure Step ID | SH |
| (0040,1001) | Requested Procedure ID | SH |

## Security Notes

> ⚠️ **WARNING**: The current configuration has `RemoteAccessAllowed: true` which is suitable for development only.
> 
> For production:
> - Restrict access via network/firewall
> - Use a reverse proxy (nginx) with TLS
> - Configure DICOM AET restrictions in `DicomModalities`
> - Change default credentials
