import { test, expect } from '@playwright/test';
import { execFileSync } from 'node:child_process';
import { randomUUID } from 'node:crypto';

/**
 * CIS writing to the REAL OpenMRS with a Keycloak token (backlog KC-32): tests/e2e-auth/harness/sso-openmrs.yml
 * --profile cis (Kafka + CIS built from the working tree, OPENMRS_AUTH_MODE=jwt, client cis -> OpenMRS user
 * nidan-sync). Events go in on Kafka exactly as OIS publishes them (JSON + Spring's __TypeId__ header). Runs when
 * CIS_SYNC_FULL is set.
 */
const FULL = !!process.env.CIS_SYNC_FULL;
const P = 'nidan-sso-openmrs-harness';
const OPD = '287463d3-2233-4c69-9851-5841a1f5e109';         // harness visit type "OPD Visit"
const LOCATION = '44c3efb0-2583-4c80-a79e-1f756a03c0a1';    // "Outpatient (OPD)"

function publish(topic: string, type: string, event: object) {
  execFileSync('docker', ['exec', '-i', `${P}-kafka-1`, 'kafka-console-producer', '--bootstrap-server', 'localhost:9092',
    '--topic', topic, '--property', 'parse.headers=true'], { input: `__TypeId__:${type}\t${JSON.stringify(event)}\n` });
}

function sql(query: string) {
  return execFileSync('docker', ['exec', '-i', `${P}-openmrs-db-1`, 'sh', '-c', 'mariadb -N -uopenmrs -pharness-openmrs-db openmrs'],
    { input: query }).toString().trim();
}

const base = () => ({ sourceSystem: 'ODOO', occurredAt: new Date().toISOString(), eventId: randomUUID() });

function patientEvent(given: string) {
  const patientUuid = randomUUID();
  publish('patients.openmrs', 'org.nidan.integration.common.events.patient.PatientRegisteredEvent',
    { ...base(), patientUuid, givenName: given, familyName: 'Cis', gender: 'F', dateOfBirth: '1990-01-01' });
  return patientUuid;
}

const creatorOfPatient = (uuid: string) => sql(`select u.username from patient p join person pe on pe.person_id = p.patient_id
  join users u on u.user_id = p.creator where pe.uuid = '${uuid}';`);

test.describe('CIS -> OpenMRS with a Keycloak token', () => {
  test.skip(!FULL, 'needs tests/e2e-auth/harness/sso-openmrs.yml --profile cis (CIS_SYNC_FULL=1)');

  // KC-32-02: a patient and a visit for her, both written by CIS and created by nidan-sync in OpenMRS (CIS's
  // OpenMRS writes are patients, visits and catalog data; lab orders go to OpenELIS).
  test('CIS syncs a patient and a visit, created by nidan-sync @KC-32-02 @sso-on-only', async () => {
    const patientUuid = patientEvent('Sync');
    await expect.poll(() => creatorOfPatient(patientUuid), { timeout: 60_000 }).toBe('nidan-sync');

    const visitUuid = randomUUID();
    publish('visits', 'org.nidan.integration.common.events.visit.VisitEvent', { ...base(), nidanVisitUuid: visitUuid,
      patientUuid, visitType: OPD, locationCode: LOCATION, startedAt: new Date().toISOString(), changeType: 'CREATED' });
    await expect.poll(() => sql(`select u.username from visit v join users u on u.user_id = v.creator
      join person pe on pe.person_id = v.patient_id where pe.uuid = '${patientUuid}';`), { timeout: 60_000 }).toBe('nidan-sync');
  });

  // KC-32-03 (disruptive): Keycloak goes away while CIS has no cached token; events published meanwhile are retried
  // (not dead-lettered) and all land once Keycloak is back: count in = count out. The harness Keycloak is dev-mem, so
  // its restart drops the realm; it is re-imported from the repo files exactly as at harness setup.
  test('Keycloak down: CIS retries and every event lands after the restart @KC-32-03 @slow @sso-on-only', async () => {
    test.skip(!process.env.HARNESS_DISRUPTIVE, 'stops Keycloak (HARNESS_DISRUPTIVE=1)');
    test.setTimeout(15 * 60_000);
    const compose = (...args: string[]) => execFileSync(`${__dirname}/../harness/compose.sh`, args, { stdio: 'pipe' }).toString();
    const started = () => Number(execFileSync('sh', ['-c', `docker logs ${P}-cis-1 2>&1 | grep -c 'Started CisApplication'; true`]).toString().trim());
    let uuids: string[] = [];
    execFileSync('docker', ['stop', `${P}-keycloak-1`]);
    try {
      const before = started();
      execFileSync('docker', ['restart', `${P}-cis-1`]);              // no cached token from here on
      await expect.poll(started, { timeout: 8 * 60_000, intervals: [5_000] }).toBeGreaterThan(before);
      uuids = ['Outage1', 'Outage2', 'Outage3'].map(patientEvent);
      await new Promise((r) => setTimeout(r, 20_000));
      for (const u of uuids) expect(creatorOfPatient(u), 'nothing lands while Keycloak is down').toBe('');
    } finally {
      execFileSync('docker', ['start', `${P}-keycloak-1`]);
      await expect.poll(() => execFileSync('docker', ['inspect', '-f', '{{.State.Health.Status}}', `${P}-keycloak-1`]).toString().trim(),
        { timeout: 5 * 60_000, intervals: [5_000] }).toBe('healthy');
      compose('up', '--no-deps', 'config-cli');                       // dev-mem: the realm comes back from the repo files
    }
    await expect.poll(() => uuids.filter((u) => creatorOfPatient(u) === 'nidan-sync').length,
      { timeout: 6 * 60_000, intervals: [10_000] }).toBe(uuids.length);   // count in = count out
    const dlt = execFileSync('docker', ['exec', `${P}-kafka-1`, 'sh', '-c', 'kafka-console-consumer --bootstrap-server localhost:9092 ' +
      '--topic patients.openmrs.cis.DLT --from-beginning --timeout-ms 8000 2>/dev/null; true']).toString();
    expect(dlt, 'no outage event was dead-lettered').not.toMatch(/Outage[123]/);
  });
});

