import { test, expect, APIRequestContext } from '@playwright/test';
import { spawnSync } from 'node:child_process';
import * as path from 'node:path';

/**
 * Role sync (keycloak/role-sync/role_sync.py) on the REAL apps (full harness): the openmrs / odoo / openelis client
 * roles come from the apps' own role lists, not from files. Runs when OPENMRS_SSO_FULL, ODOO_SSO_FULL and
 * OPENELIS_SSO_FULL are set (run-keycloak-suite.sh full stage).
 */
const FULL = !!(process.env.OPENMRS_SSO_FULL && process.env.ODOO_SSO_FULL && process.env.OPENELIS_SSO_FULL);
const KC = process.env.KEYCLOAK_URL ?? 'http://localhost:18180/auth';
const COMPOSE = path.resolve(__dirname, '../harness/compose.sh');
const MARK = 'nidan_source';

test.skip(!FULL, 'full harness only (OPENMRS_SSO_FULL, ODOO_SSO_FULL, OPENELIS_SSO_FULL)');
test.describe.configure({ mode: 'serial' });

async function admin(request: APIRequestContext, p: string, method = 'GET', data?: unknown) {
  const t = await request.post(`${KC}/realms/master/protocol/openid-connect/token`, {
    form: { grant_type: 'password', client_id: 'admin-cli', username: 'admin', password: 'harness-admin-password' } });
  return request.fetch(`${KC}/admin/realms/nidan${p}`, { method, data, headers: { Authorization: `Bearer ${(await t.json()).access_token}` } });
}

async function cid(request: APIRequestContext, client: string) {
  return (await (await admin(request, `/clients?clientId=${client}`)).json())[0].id as string;
}

async function role(request: APIRequestContext, client: string, name: string) {
  const r = await admin(request, `/clients/${await cid(request, client)}/roles/${encodeURIComponent(name)}`);
  return r.status() === 404 ? null : r.json();
}

/** One sync run, as `docker compose exec keycloak-role-sync ... --once` does on a site. */
function sync() {
  const r = spawnSync(COMPOSE, ['--profile', 'rolesync', 'up', '--no-deps', '--abort-on-container-exit',
    '--exit-code-from', 'role-sync', 'role-sync'], { encoding: 'utf8', timeout: 300_000 });
  return { code: r.status, out: (r.stdout ?? '') + (r.stderr ?? '') };
}

test('the apps\' own roles reach Keycloak, superuser roles flagged; realm-file roles untouched @KC-24-01 @KC-57-01 @security @sso-on-only', async ({ request }) => {
  test.setTimeout(360_000);
  const run = sync();
  expect(run.code, run.out).toBe(0);
  expect(run.out).toMatch(/openmrs: \d+ roles in the app/);
  expect(run.out).toMatch(/odoo: \d+ roles in the app/);
  expect(run.out).toMatch(/openelis: \d+ roles in the app/);
  // OpenMRS: a core role, and System Developer flagged superuser; the machine accounts' roles never offered
  expect((await role(request, 'openmrs', 'Provider'))?.attributes?.[MARK]).toEqual(['openmrs-sync']);
  expect((await role(request, 'openmrs', 'System Developer'))?.attributes?.nidan_superuser).toEqual(['true']);
  expect(await role(request, 'openmrs', 'Nidan Sync')).toBeNull();
  expect(await role(request, 'openmrs', 'Nidan Role Sync')).toBeNull();
  // Odoo: groups by XML id (what nidan_keycloak_sso maps), the settings group flagged superuser
  expect((await role(request, 'odoo', 'base.group_user'))?.attributes?.[MARK]).toEqual(['odoo-sync']);
  expect((await role(request, 'odoo', 'base.group_system'))?.attributes?.nidan_superuser).toEqual(['true']);
  // OpenELIS: a role from the realm file keeps no sync mark (config-cli owns it)
  expect((await role(request, 'openelis', 'Results'))?.attributes?.[MARK]).toBeUndefined();
  expect(sync().out, 'a second run changes nothing').toMatch(/openmrs: \d+ roles in the app; created 0, updated 0, marked missing 0, deleted 0/);
});

test('a synced role the app no longer has is deleted when unused and kept while assigned; other roles never @KC-24-01 @security @sso-on-only', async ({ request }) => {
  test.setTimeout(360_000);
  const id = await cid(request, 'openmrs');
  const stamp = Date.now();
  const gone = `Gone Role ${stamp}`, held = `Held Role ${stamp}`, manual = `Manual Role ${stamp}`, fresh = `Fresh Gone ${stamp}`;
  // missing since long ago (epoch 0) = past the one-day grace; "fresh" was never seen missing before this run
  const old = { [MARK]: ['openmrs-sync'], nidan_missing_since: ['0'] };
  for (const [name, attributes] of [[gone, old], [held, old], [manual, {}], [fresh, { [MARK]: ['openmrs-sync'] }]] as const)
    expect((await admin(request, `/clients/${id}/roles`, 'POST', { name, attributes })).status()).toBe(201);
  const username = `rsync${stamp}`;
  const u = await admin(request, '/users', 'POST', { username, enabled: true, firstName: 'Role', lastName: 'Sync',
    email: `${username}@example.org`, attributes: { gender: ['F'] } });
  const uid = u.headers()['location'].split('/').pop()!;
  const heldRep = await role(request, 'openmrs', held);
  await admin(request, `/users/${uid}/role-mappings/clients/${id}`, 'POST', [heldRep]);

  const run = sync();
  expect(run.code, run.out).toBe(0);
  expect(await role(request, 'openmrs', gone), 'unused and gone from the app: deleted').toBeNull();
  expect(await role(request, 'openmrs', held), 'still assigned: kept').not.toBeNull();
  expect(run.out).toContain(`KEPT (gone from the app but still assigned, remove the assignments first): ['${held}']`);
  expect(await role(request, 'openmrs', manual), 'no sync mark: never touched').not.toBeNull();
  const f = await role(request, 'openmrs', fresh);
  expect(f, 'gone for the first time: kept a day, only marked').not.toBeNull();
  expect(f.attributes.nidan_missing_since?.[0]).toMatch(/^\d+$/);

  // tidy up what this test made (the user and both remaining roles)
  await admin(request, `/users/${uid}`, 'DELETE');
  await admin(request, `/clients/${id}/roles/${encodeURIComponent(held)}`, 'DELETE');
  await admin(request, `/clients/${id}/roles/${encodeURIComponent(manual)}`, 'DELETE');
  await admin(request, `/clients/${id}/roles/${encodeURIComponent(fresh)}`, 'DELETE');
});

test('a staff job holding a superuser role is reported and the run fails (KC-57) @KC-57-01 @security @sso-on-only', async ({ request }) => {
  test.setTimeout(360_000);
  const id = await cid(request, 'openmrs');
  const groups = await (await admin(request, '/groups?max=1000')).json();
  const staff = groups.find((g: { path: string }) => g.path === '/staff');
  const nurse = (await (await admin(request, `/groups/${staff.id}/children?max=1000`)).json())
    .find((g: { name: string }) => g.name === 'Nurse');
  const dev = await role(request, 'openmrs', 'System Developer');
  await admin(request, `/groups/${nurse.id}/role-mappings/clients/${id}`, 'POST', [dev]);
  try {
    const run = sync();
    expect(run.code, run.out).not.toBe(0);
    expect(run.out).toContain("ERROR /staff/Nurse holds the openmrs superuser role 'System Developer' (KC-57)");
  } finally {
    await admin(request, `/groups/${nurse.id}/role-mappings/clients/${id}`, 'DELETE', [dev]);
  }
});

test('the role lists answer only the internal network with the secret @KC-53-02 @security @sso-on-only', async ({ request }) => {
  // the gateway never forwards them
  for (const url of ['https://localhost:8069/api/nidan/role-catalog',
    'https://localhost/api/OpenELIS-Global/rest/nidan/role-catalog',
    'https://localhost/OpenELIS-Global/rest/nidan/role-catalog',
    'https://localhost:18444/OpenELIS-Global/rest/nidan/role-catalog']) {
    const r = await request.get(url, { headers: { 'X-Nidan-Role-Sync-Secret': 'harness-role-sync-secret-0123456789abc' }, maxRedirects: 0 });
    expect(r.status(), url).toBe(404);
  }
  // Odoo's harness back door (127.0.0.1:18069 = the internal network): the secret is required
  const odoo = 'http://127.0.0.1:18069/api/nidan/role-catalog';
  expect((await request.get(odoo)).status()).toBe(401);
  expect((await request.get(odoo, { headers: { 'X-Nidan-Role-Sync-Secret': 'wrong-wrong-wrong-wrong-wrong-wrong-wr' } })).status()).toBe(401);
  const ok = await request.get(odoo, { headers: { 'X-Nidan-Role-Sync-Secret': 'harness-role-sync-secret-0123456789abc' } });
  expect(ok.status()).toBe(200);
  expect((await ok.json()).roles.length).toBeGreaterThan(10);
});
