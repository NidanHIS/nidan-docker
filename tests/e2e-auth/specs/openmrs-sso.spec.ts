import { test, expect, APIRequestContext, Browser, Page } from '@playwright/test';
import { execFileSync } from 'node:child_process';

/**
 * Central login into the REAL OpenMRS (backlog KC-34, KC-35, KC-10-12) on tests/e2e-auth/harness/sso-openmrs.yml:
 * gateway on https://localhost (port 443), oauth2-proxy, Keycloak on :18180, OpenMRS with the oauth2login module.
 * Runs when OPENMRS_SSO_FULL is set.
 */
const FULL = !!process.env.OPENMRS_SSO_FULL;
const KC = process.env.KEYCLOAK_URL ?? 'http://localhost:18180/auth';
const GW = 'https://localhost';
// the harness OpenMRS admin's real local password (sso-openmrs.yml OPENMRS_ADMIN_PASSWORD; nidancore set it at start)
const ADMIN_PW = process.env.OPENMRS_ADMIN_PASSWORD ?? 'Harness-Admin-Pass-0123456789';
const SECRET = process.env.KC_HARNESS_CLIENT_SECRET ?? 'harness-only-not-a-real-secret-0123456789';
const DB = ['exec', 'nidan-sso-openmrs-harness-openmrs-db-1', 'mariadb', '-uopenmrs', '-pharness-openmrs-db', 'openmrs', '-N', '-e'];
const sql = (q: string) => execFileSync('docker', [...DB, q]).toString().trim();

async function admin(request: APIRequestContext, path: string, method = 'GET', data?: unknown) {
  const t = await request.post(`${KC}/realms/master/protocol/openid-connect/token`, {
    form: { grant_type: 'password', client_id: 'admin-cli', username: 'admin', password: 'harness-admin-password' } });
  return request.fetch(`${KC}/admin/realms/nidan${path}`, { method, data, headers: { Authorization: `Bearer ${(await t.json()).access_token}` } });
}

async function nurse(request: APIRequestContext, username = `omrs${Date.now()}${Math.floor(Math.random() * 10000)}`) {
  const r = await admin(request, '/users', 'POST', { username, firstName: 'Sita', lastName: 'Rai', enabled: true,
    attributes: { gender: ['F'] }, groups: ['/staff/Nurse'] });
  const id = r.headers()['location'].split('/').pop()!;
  await admin(request, `/users/${id}/reset-password`, 'PUT', { type: 'password', value: 'Harness-Pass-123', temporary: false });
  const gw = (await (await admin(request, '/clients?clientId=nidan-gateway')).json())[0].id;
  const role = (await (await admin(request, `/clients/${gw}/roles/app-openmrs`)).json());
  await admin(request, `/users/${id}/role-mappings/clients/${gw}`, 'POST', [role]);
  return { username, id };
}

async function gatewayLogin(page: Page, username: string) {
  await page.goto(`${GW}/openmrs/ws/rest/v1/session`);
  await page.fill('#username', username);
  await page.fill('#password', 'Harness-Pass-123');
  await page.click('#kc-login');
  await expect(page).toHaveURL(`${GW}/openmrs/ws/rest/v1/session`);
}

const sessionOf = async (page: Page, headers: Record<string, string> = {}) =>
  (await (await page.request.get(`${GW}/openmrs/ws/rest/v1/session`, { headers: { Accept: 'application/json', ...headers } })).json());

test.describe('central login into OpenMRS', () => {
  test.skip(!FULL, 'needs tests/e2e-auth/harness/sso-openmrs.yml (OPENMRS_SSO_FULL=1)');

  test('one Keycloak login reaches OpenMRS; the user is created from the token @KC-35-01 @KC-34-02 @KC-10-12 @KC-06-02 @security @sso-on-only', async ({ page, request }) => {
    const { username, id } = await nurse(request);
    const authRequests: string[] = [];
    page.on('request', (r) => { if (r.url().includes('/protocol/openid-connect/auth')) authRequests.push(r.url()); });
    await page.goto(`${GW}/openmrs/ws/rest/v1/session`);
    await page.fill('#username', username);
    await page.fill('#password', 'Harness-Pass-123');
    await page.click('#kc-login');
    await expect(page).toHaveURL(`${GW}/openmrs/ws/rest/v1/session`);   // through the gateway; OpenMRS not yet logged in
    await page.goto(`${GW}/openmrs/oauth2login`);                      // OpenMRS's own login: Keycloak SSO, no prompt
    await page.waitForLoadState();
    await expect(page.locator('#username')).toHaveCount(0);
    const omrsAuth = authRequests.find((u) => u.includes('client_id=openmrs'));
    expect(omrsAuth, 'OpenMRS sent the browser to Keycloak').toBeTruthy();
    expect(new URL(omrsAuth!).searchParams.get('redirect_uri'), 'KC-10-12').toBe(`${GW}/openmrs/oauth2login`);
    // the session resource has its own representation; a new nurse has no privilege to read person records, so
    // gender is checked in the database on the harness (progress log), not here
    const s = await (await page.request.get(`${GW}/openmrs/ws/rest/v1/session`, { headers: { Accept: 'application/json' } })).json();
    expect(s.authenticated).toBe(true);
    expect(s.user.username).toBe(username);
    expect(s.user.systemId, 'linked by Keycloak sub').toBe(id);
    expect(s.user.person.display).toBe('Sita Rai');
  });

  test('Basic credentials through the gateway authenticate nobody @KC-34-04 @security @sso-on-only', async ({ page, request }) => {
    const { username } = await nurse(request);
    await page.goto(`${GW}/openmrs/ws/rest/v1/session`);
    await page.fill('#username', username);
    await page.fill('#password', 'Harness-Pass-123');
    await page.click('#kc-login');
    await expect(page).toHaveURL(`${GW}/openmrs/ws/rest/v1/session`);
    const r = await page.request.get(`${GW}/openmrs/ws/rest/v1/session`, {
      headers: { Accept: 'application/json', Authorization: 'Basic ' + Buffer.from(`admin:${ADMIN_PW}`).toString('base64') } });
    expect((await r.json()).authenticated, 'the admin\'s valid local password sent by a browser').toBe(false);
  });

  // O3 itself (esm-login provider oauth2, config-sso.json): the SPA logs in through oauth2login and comes back to the
  // SPA; its log out ends OpenMRS, the gateway and Keycloak, so the next person at this computer gets the login form.
  // The user menu's Logout button navigates to ${openmrsSpaBase}/logout; the test opens that route directly.
  test('O3 logs in and out through Keycloak; after log out the next visit asks for a password @KC-35-01 @KC-35-02 @security @sso-on-only', async ({ page, request }) => {
    const { username, id } = await nurse(request);
    await page.goto(`${GW}/openmrs/spa/home`);
    await page.fill('#username', username);
    await page.fill('#password', 'Harness-Pass-123');
    await page.click('#kc-login');
    // the SPA finds no OpenMRS session, goes to /openmrs/oauth2login (Keycloak SSO, no prompt) and comes back
    await expect.poll(async () => (await (await page.request.get(`${GW}/openmrs/ws/rest/v1/session`,
      { headers: { Accept: 'application/json' } })).json()).user?.username, { timeout: 60_000 }).toBe(username);
    await page.waitForURL(/\/openmrs\/spa\/(home|login\/location)/, { timeout: 60_000 });
    await expect(page.locator('#username')).toHaveCount(0);

    await page.goto(`${GW}/openmrs/spa/logout`);
    // the end state is the gateway's own login (after /oauth2/sign_out); the SPA may bounce through Keycloak for its
    // openmrs client on the way, which must not count as "logged out"
    await page.waitForURL(/\/protocol\/openid-connect\/auth\?.*client_id=nidan-gateway/);
    await expect(page.locator('#username'), 'Keycloak asks for a password').toBeVisible();
    expect(await (await admin(request, `/users/${id}/sessions`)).json(), 'Keycloak session ended').toEqual([]);
    const after = await (await page.request.get(`${GW}/openmrs/ws/rest/v1/session`, { headers: { Accept: 'application/json' }, maxRedirects: 0 }));
    expect([302, 401], 'the gateway session ended too (API calls get 401, pages a login redirect)').toContain(after.status());
    await page.goto(`${GW}/openmrs/spa/home`);
    await expect(page.locator('#username'), 'the next visit is not signed in as the previous user').toBeVisible();
  });

  // KC-19-02 (OpenMRS part): the app session cookie of a logged-out user, replayed straight to OpenMRS (no gateway, as
  // anyone on the hospital network could), is no longer a session. Other apps are not on this harness.
  test('after O3 logout the old OpenMRS session cookie, replayed directly to OpenMRS, is not logged in @KC-19-02 @security @sso-on-only', async ({ page, request }) => {
    const { username } = await nurse(request);
    await page.goto(`${GW}/openmrs/spa/home`);
    await page.fill('#username', username);
    await page.fill('#password', 'Harness-Pass-123');
    await page.click('#kc-login');
    await expect.poll(async () => (await sessionOf(page)).user?.username, { timeout: 60_000 }).toBe(username);
    const jsession = (await page.context().cookies(`${GW}/openmrs/`)).find((c) => c.name === 'JSESSIONID')?.value;
    expect(jsession, 'OpenMRS session cookie').toBeTruthy();
    const direct = () => {
      const out = execFileSync('docker', ['exec', 'nidan-sso-openmrs-harness-gateway-1', 'sh', '-c',
        `wget -qSO- --header "Cookie: JSESSIONID=${jsession}" --header "Accept: application/json" ` +
        "'http://openmrs-backend:8080/openmrs/ws/rest/v1/session' 2>&1; true"]).toString();
      const status = Number(out.match(/HTTP\/1\.1 (\d{3})/)?.[1] ?? 0);
      const body = out.slice(out.indexOf('{'));
      return { status, session: status === 200 ? JSON.parse(body) : null };
    };
    expect(direct().session?.user?.username, 'before logout the cookie is the session').toBe(username);

    await page.goto(`${GW}/openmrs/spa/logout`);
    await page.waitForURL(/\/protocol\/openid-connect\/auth\?.*client_id=nidan-gateway/);
    const after = direct();
    // refused outright (401), or answered as nobody
    if (after.status !== 401) {
      expect(after.status).toBe(200);
      expect(after.session.authenticated, 'replayed after logout').toBe(false);
    }
    expect(after.session?.user ?? null).toBeNull();
  });

  test('a machine token sent by a browser through the gateway never becomes nidan-sync @KC-56-02 @security @sso-on-only', async ({ page, request }) => {
    const cis = (await (await request.post(`${KC}/realms/nidan/protocol/openid-connect/token`, {
      form: { grant_type: 'client_credentials', client_id: 'cis', client_secret: SECRET } })).json()).access_token;
    expect(cis, 'harness cis token').toBeTruthy();
    const { username } = await nurse(request);
    await gatewayLogin(page, username);
    for (const headers of [{ Authorization: `Bearer ${cis}` }, { 'X-JWT-Assertion': cis }]) {
      const s = await sessionOf(page, headers);       // nurse's gateway cookie, no OpenMRS session yet
      expect(s.user?.username ?? null, JSON.stringify(Object.keys(headers))).not.toBe('nidan-sync');
      expect(s.authenticated).toBe(false);
    }
    await page.goto(`${GW}/openmrs/oauth2login`);
    for (const headers of [{ Authorization: `Bearer ${cis}` }, { 'X-JWT-Assertion': cis }]) {
      expect((await sessionOf(page, headers)).user?.username, 'with an OpenMRS session: still the nurse').toBe(username);
    }
  });

  test("the openmrs token carries roles and gender; no user's token carries openmrs_user @KC-08-08 @security @sso-on-only", async ({ request }) => {
    const { id } = await nurse(request);
    const om = (await (await admin(request, '/clients?clientId=openmrs')).json())[0].id;
    await admin(request, `/clients/${om}/roles`, 'POST', { name: 'Organizational: Nurse' });     // 409 if it exists: fine
    const role = await (await admin(request, `/clients/${om}/roles/${encodeURIComponent('Organizational: Nurse')}`)).json();
    await admin(request, `/users/${id}/role-mappings/clients/${om}`, 'POST', [role]);
    const example = async (client: string) =>
      (await admin(request, `/clients/${client}/evaluate-scopes/generate-example-access-token?scope=openid&userId=${id}`)).json();
    const t = await example(om);
    expect(t.roles).toEqual(['Organizational: Nurse']);
    expect(t.gender).toBe('F');
    // every client a person can log in through (code flow); the machine clients have no user login at all
    const clients = (await (await admin(request, '/clients')).json()) as Array<{ id: string; clientId: string; standardFlowEnabled: boolean; directAccessGrantsEnabled: boolean }>;
    const userFacing = clients.filter((c) => c.standardFlowEnabled || c.directAccessGrantsEnabled);
    expect(userFacing.map((c) => c.clientId)).toContain('nidan-gateway');
    for (const c of userFacing) expect(await example(c.id), c.clientId).not.toHaveProperty('openmrs_user');
  });

  // KC-36-01: an account that already exists in OpenMRS (local password, own user_id) is linked at cutover with
  // apply-links.py's own statement (users.system_id = Keycloak sub) and is then the account Keycloak logins use.
  test('a linked existing OpenMRS account is the one Keycloak logins use (same user_id) @KC-36-01 @sso-on-only', async ({ page, request }) => {
    test.skip(!process.env.OPENMRS_DB_EXEC, 'set OPENMRS_DB_EXEC=1 (the test creates an account in the harness database)');
    const username = `linked${Date.now()}`;
    sql(`insert into person (gender, creator, date_created, uuid) values ('F', 1, now(), uuid());
         insert into person_name (person_id, given_name, family_name, preferred, creator, date_created, uuid)
           values (last_insert_id(), 'Old', 'Name', 1, 1, now(), uuid());
         insert into users (system_id, username, password, salt, creator, date_created, person_id, uuid)
           select concat('L-', '${username}'), '${username}', sha2('x', 512), 'x', 1, now(), max(person_id), uuid()
           from person_name where given_name = 'Old' and family_name = 'Name';`);
    const [userId, uuid] = sql(`select user_id, uuid from users where username = '${username}'`).split('\t');
    const { id } = await nurse(request, username);
    const link = execFileSync('python3', ['-I', '-c', [
      'import importlib.util, sys',
      "spec = importlib.util.spec_from_file_location('al', sys.argv[1]); al = importlib.util.module_from_spec(spec); spec.loader.exec_module(al)",
      "print(al.sql_for('openmrs', int(sys.argv[2]), sys.argv[3], sys.argv[4]))"].join('\n'),
      `${__dirname}/../../../scripts/apply-links.py`, userId, username, id]).toString();
    sql(link);
    await gatewayLogin(page, username);
    await page.goto(`${GW}/openmrs/oauth2login`);
    const s = await sessionOf(page);
    expect(s.user?.uuid, 'the existing account, not a new one').toBe(uuid);
    expect(sql(`select count(*), max(user_id) from users where username = '${username}' or system_id = '${id}'`)).toBe(`1\t${userId}`);
    // names and gender now come from Keycloak (written by the login's update; this first session may still show the
    // old name, roles are the part that must be right at once: KC-58-06)
    expect(sql(`select concat(pn.given_name, ' ', pn.family_name, ' ', p.gender) from users u join person p using (person_id)
      join person_name pn on pn.person_id = p.person_id and pn.voided = 0 where u.user_id = ${userId}`)).toBe('Sita Rai F');
  });

  // KC-59-02: machine callers inside the network (portal-sync, CIS) reach openmrs-backend:8080 directly, not via the
  // gateway; with central login on, Basic is refused there too, for nidan-sync and for a known-good local password.
  test('Basic to the OpenMRS FHIR API from inside the network gets 401 @KC-59-02 @security @sso-on-only', async () => {
    for (const cred of ['nidan-sync:anything', `admin:${ADMIN_PW}`, 'admin:Admin123']) {
      const out = execFileSync('docker', ['exec', 'nidan-sso-openmrs-harness-gateway-1', 'sh', '-c',
        `wget -qSO /dev/null --header "Authorization: Basic ${Buffer.from(cred).toString('base64')}" ` +
        'http://openmrs-backend:8080/openmrs/ws/fhir2/R4/Patient 2>&1 | head -1; true']).toString();
      expect(out, cred.split(':')[0]).toContain('401');
    }
  });

  // KC-32-05 / KC-59-01: each machine client is its own least-privilege OpenMRS account (nidancore ServiceAccounts):
  // cis -> nidan-sync (Nidan Sync, writes), odoo-sync -> nidan-odoo (read only), portal-sync -> nidan-portal (reads +
  // appointments). Called from inside the network, as the callers do; status codes from OpenMRS itself.
  test('each machine client acts as its own least-privilege OpenMRS account @KC-32-05 @KC-59-01 @security @sso-on-only', async ({ request }) => {
    const token = async (client: string) => (await (await request.post(`${KC}/realms/nidan/protocol/openid-connect/token`, {
      form: { grant_type: 'client_credentials', client_id: client, client_secret: SECRET } })).json()).access_token as string;
    const call = (t: string, method: 'GET' | 'POST', path: string, body = '{}') => {
      const post = method === 'POST' ? `--header "Content-Type: application/json" --post-data '${body}' ` : '';
      const out = execFileSync('docker', ['exec', 'nidan-sso-openmrs-harness-gateway-1', 'sh', '-c',
        `wget -qSO /tmp/claude-body --header "Authorization: Bearer ${t}" --header "Accept: application/json" ${post}` +
        `'http://openmrs-backend:8080/openmrs${path}' 2>&1 | grep -m1 "HTTP/"; cat /tmp/claude-body 2>/dev/null; true`]).toString();
      return { status: Number(out.match(/HTTP\/1\.1 (\d{3})/)?.[1] ?? 0), body: out.slice(out.indexOf('\n') + 1) };
    };
    const who = (t: string) => {
      const u = JSON.parse(call(t, 'GET', '/ws/rest/v1/session').body).user;
      return { username: u.username, roles: (u.roles as Array<{ display: string }>).map((r) => r.display) };
    };
    const [cis, odoo, portal] = [await token('cis'), await token('odoo-sync'), await token('portal-sync')];
    expect(who(cis)).toEqual({ username: 'nidan-sync', roles: ['Nidan Sync'] });
    expect(who(odoo)).toEqual({ username: 'nidan-odoo', roles: ['Nidan Odoo Read'] });
    expect(who(portal)).toEqual({ username: 'nidan-portal', roles: ['Nidan Portal'] });

    // portal-sync: reads the patient record (FHIR), may not write patients or read user accounts
    expect(call(portal, 'GET', '/ws/fhir2/R4/Patient?_count=1').status).toBe(200);
    const person = '{"gender":"F","names":[{"givenName":"Not","familyName":"Allowed"}]}';
    expect(call(portal, 'POST', '/ws/rest/v1/person', person).status, 'portal-sync creates no people').toBe(403);
    expect(call(portal, 'GET', '/ws/rest/v1/user?q=admin').status).toBe(403);
    // odoo-sync: reads concepts, writes nothing
    expect(call(odoo, 'GET', '/ws/rest/v1/concept?q=a&limit=1').status).toBe(200);
    expect(call(odoo, 'POST', '/ws/rest/v1/location', '{"name":"odoo-may-not"}').status).toBe(403);
    // cis: writes what it syncs (a location here), but no role or module administration
    expect(call(cis, 'POST', '/ws/rest/v1/location', `{"name":"cis-sync-${Date.now()}"}`).status).toBe(201);
    expect(call(cis, 'GET', '/ws/rest/v1/role').status).toBe(403);
    // (listing modules is open to any logged-in user in OpenMRS; acting on them needs Manage Modules)
    expect(call(cis, 'POST', '/ws/rest/v1/moduleaction', '{"action":"stop","modules":["queue"]}').status).toBe(403);
  });

  // KC-10-11: one app down is one app down. Its route answers the gateway's own error page, every other route keeps
  // working, and a gateway restart while it is down still comes up (nginx resolves upstreams per request).
  test('a stopped upstream does not stop the gateway, also across a gateway restart @KC-10-11 @sso-on-only', async ({ page, request }) => {
    // restarts the gateway: other tests running at the same time lose their connections, so run it on its own:
    // HARNESS_DISRUPTIVE=1 OPENMRS_SSO_FULL=1 npx playwright test openmrs-sso -g KC-10-11 --project=sso-on --workers=1
    test.skip(!process.env.HARNESS_DISRUPTIVE, 'disruptive (restarts the gateway): set HARNESS_DISRUPTIVE=1 and run it alone');
    test.setTimeout(180_000);
    const dockerDo = (...a: string[]) => execFileSync('docker', a).toString().trim();
    const SPA = 'nidan-sso-openmrs-harness-openmrs-frontend-1', GATEWAY = 'nidan-sso-openmrs-harness-gateway-1';
    const { username } = await nurse(request);
    await gatewayLogin(page, username);
    const status = async (path: string) => (await page.request.get(`${GW}${path}`, { headers: { Accept: 'application/json' } })).status();
    expect(await status('/openmrs/spa/'), 'SPA up').toBe(200);
    dockerDo('stop', SPA);
    try {
      for (const phase of ['running gateway', 'gateway restarted while the SPA is down']) {
        if (phase.startsWith('gateway restarted')) {
          dockerDo('restart', GATEWAY);
          await expect.poll(() => dockerDo('inspect', '-f', '{{.State.Health.Status}}', GATEWAY), { timeout: 60_000 }).toBe('healthy');
        }
        // the gateway's own page, fast: 504 while nginx still holds the stopped container's address (connect times
        // out after 5 s), 502 once Docker's DNS answer has expired
        const t0 = Date.now();
        const down = await page.request.get(`${GW}/openmrs/spa/`);
        expect([502, 504], `${phase}: the stopped app`).toContain(down.status());
        expect(Date.now() - t0, `${phase}: error page within 10 s`).toBeLessThan(10_000);
        expect(await down.text(), `${phase}: the gateway's error page, not a raw error`).toContain('nidan-logo');
        expect(await status('/openmrs/ws/rest/v1/session'), `${phase}: OpenMRS itself still answers`).toBe(200);
        expect(await status('/'), `${phase}: launcher`).toBe(200);
      }
    } finally {
      dockerDo('start', SPA);
    }
    await expect.poll(() => status('/openmrs/spa/'), { timeout: 60_000 }).toBe(200);
  });

  // KC-14-04: the paywall is answered by Odoo over the internal network (odoo:8069 + shared secret), never through
  // the person's own Odoo access: a nurse with no Odoo role gets a real BLOCK for an unpaid patient, not an outage.
  // Needs the odoo profile and the paywall test data (PAYWALL_PATIENT: an Odoo patient with an unpaid POS cart).
  test('paywall: a nurse without Odoo access gets a real block for an unpaid patient @KC-14-04 @security @sso-on-only', async ({ page, request }) => {
    test.skip(!process.env.PAYWALL_PATIENT, 'set PAYWALL_PATIENT to an Odoo patient uuid with an unpaid cart (harness odoo profile)');
    const { username } = await nurse(request);
    await gatewayLogin(page, username);
    await page.goto(`${GW}/openmrs/oauth2login`);
    const r = await page.request.get(`${GW}/openmrs/ws/rest/v1/nidan/paywall/check?patientUuid=${process.env.PAYWALL_PATIENT}`,
      { headers: { Accept: 'application/json' } });
    expect(r.status(), await r.text()).toBe(200);
    const d = await r.json();
    expect(d.decision, JSON.stringify(d)).not.toBe('outage');
    expect(d.blocked, JSON.stringify(d)).toBe(true);
    expect(d.cartAmount).toBe(500);
  });

  // KC-14-07: with Keycloak down, signed-in staff still get real paywall answers (the check never touches Keycloak:
  // OpenMRS session -> nidancore -> odoo:8069 with the shared secret). Stops the harness Keycloak, whose in-memory
  // realm is then re-imported: disruptive, run alone with HARNESS_DISRUPTIVE=1.
  test('paywall keeps answering while Keycloak is down @KC-14-07 @security @sso-on-only', async ({ page, request }) => {
    test.skip(!process.env.PAYWALL_PATIENT || !process.env.HARNESS_DISRUPTIVE, 'needs PAYWALL_PATIENT and HARNESS_DISRUPTIVE=1 (stops Keycloak)');
    test.setTimeout(240_000);
    const KEYCLOAK = 'nidan-sso-openmrs-harness-keycloak-1';
    const check = async () => (await page.request.get(`${GW}/openmrs/ws/rest/v1/nidan/paywall/check?patientUuid=${process.env.PAYWALL_PATIENT}`,
      { headers: { Accept: 'application/json' } })).json();
    const { username } = await nurse(request);
    await gatewayLogin(page, username);
    await page.goto(`${GW}/openmrs/oauth2login`);
    expect((await check()).blocked).toBe(true);
    execFileSync('docker', ['stop', KEYCLOAK]);
    try {
      expect(execFileSync('docker', ['inspect', '-f', '{{.State.Running}}', KEYCLOAK]).toString().trim()).toBe('false');
      const d = await check();
      expect(d.decision, JSON.stringify(d)).not.toBe('outage');
      expect(d.blocked, 'a real answer with Keycloak down').toBe(true);
    } finally {
      execFileSync('docker', ['start', KEYCLOAK]);
      await expect.poll(() => execFileSync('docker', ['inspect', '-f', '{{.State.Health.Status}}', KEYCLOAK]).toString().trim(),
        { timeout: 120_000 }).toBe('healthy');
      const pattern = execFileSync('python3', ['-I', `${__dirname}/../../../scripts/reserved-usernames.py`]).toString()
        .split('\n').find((l) => l.startsWith('KC_USERNAME_PATTERN='))!.slice('KC_USERNAME_PATTERN='.length);
      execFileSync('docker', ['compose', '-f', `${__dirname}/../harness/sso-openmrs.yml`, 'up', '--no-deps', 'config-cli'],
        { env: { ...process.env, KC_USERNAME_PATTERN: pattern }, stdio: 'ignore' });
    }
  });

  // KC-11-07 (gateway part): when the login service is down, people get the outage page (pages) or 503 (APIs),
  // never a raw 502, and carry on with the same session when it is back. Keycloak itself down: a newcomer is sent
  // to Keycloak's own origin, which the gateway cannot answer for until it serves that origin (decision D1).
  // Stops oauth2-proxy: disruptive, HARNESS_DISRUPTIVE=1, run alone.
  test('login service down: outage page / 503, never a raw 502; same session works when it is back @KC-11-07 @sso-on-only', async ({ page, request }) => {
    test.skip(!process.env.HARNESS_DISRUPTIVE, 'disruptive (stops oauth2-proxy): set HARNESS_DISRUPTIVE=1 and run it alone');
    test.setTimeout(180_000);
    const PROXY = 'nidan-sso-openmrs-harness-oauth2-proxy-1';
    const { username } = await nurse(request);
    await gatewayLogin(page, username);
    execFileSync('docker', ['stop', PROXY]);
    try {
      await page.goto(`${GW}/openmrs/ws/rest/v1/session`);
      expect(new URL(page.url()).pathname, 'a page goes to the outage page').toBe('/outage.html');
      await expect(page.locator('body')).not.toContainText('502 Bad Gateway');
      const api = await page.request.get(`${GW}/openmrs/ws/rest/v1/session`, { headers: { Accept: 'application/json' }, maxRedirects: 0 });
      expect(api.status(), 'an API call gets 503').toBe(503);
    } finally {
      execFileSync('docker', ['start', PROXY]);
    }
    await expect.poll(async () => (await page.request.get(`${GW}/openmrs/ws/rest/v1/session`,
      { headers: { Accept: 'application/json' } })).status(), { timeout: 60_000 }).toBe(200);
    await page.goto(`${GW}/openmrs/ws/rest/v1/session`);
    await expect(page.locator('#username'), 'no new login needed').toHaveCount(0);
  });

  // KC-10-10: someone in every job group (the backlog's multi1) carries the biggest token and session cookie; every app
  // must still load (no 400 / 431 "request header too large" anywhere on the way).
  test('a person in every job loads every app with no 400/431 @KC-10-10 @sso-on-only', async ({ page, request }) => {
    const username = `multi${Date.now()}`;
    const staff = (await (await admin(request, '/groups?search=staff&briefRepresentation=false')).json())[0];
    const jobs = (await (await admin(request, `/groups/${staff.id}/children?max=100`)).json()) as Array<{ path: string }>;
    expect(jobs.length).toBeGreaterThan(3);
    const r = await admin(request, '/users', 'POST', { username, firstName: 'Multi', lastName: 'Job', enabled: true,
      attributes: { gender: ['O'], nidan_shops: ['Harness Shop A', 'Harness Shop B'], nidan_locations: ['Ward A', 'Ward B', 'OPD'] },
      groups: jobs.map((j) => j.path) });
    const id = r.headers()['location'].split('/').pop()!;
    await admin(request, `/users/${id}/reset-password`, 'PUT', { type: 'password', value: 'Harness-Pass-123', temporary: false });
    // every app the gateway gates (in the harness realm the job groups carry no client roles; nurse() does the same)
    const gw = (await (await admin(request, '/clients?clientId=nidan-gateway')).json())[0].id;
    const appRoles = ((await (await admin(request, `/clients/${gw}/roles`)).json()) as Array<{ name: string }>).filter((x) => x.name.startsWith('app-'));
    await admin(request, `/users/${id}/role-mappings/clients/${gw}`, 'POST', appRoles);
    const bad: string[] = [];
    page.on('response', (x) => { if ([400, 431].includes(x.status())) bad.push(`${x.status()} ${x.url().slice(0, 120)}`); });
    await gatewayLogin(page, username);
    const cookieBytes = (await page.context().cookies()).filter((c) => c.name.startsWith('_oauth2_proxy')).reduce((n, c) => n + c.value.length, 0);
    for (const path of ['/', '/oauth2/userinfo', '/openmrs/ws/rest/v1/session', '/openmrs/spa/home', '/superset/welcome/']) {
      const resp = await page.goto(`${GW}${path}`);
      expect([400, 431], `${path}`).not.toContain(resp?.status());
    }
    if (process.env.ODOO_SSO_FULL) {                         // Odoo (:8069) with every role in the token
      await page.goto('https://localhost:8069/web/login');   // through the gateway's :8069 (session already there)
      await page.locator('a[href*="/protocol/openid-connect/auth"]').first().click();
      await page.waitForURL(/localhost:8069\/(odoo|web)(\/|\?|#|$)/, { timeout: 60_000 });
    }
    console.log(`KC-10-10: ${jobs.length} jobs, gateway session cookie ${cookieBytes} bytes`);
    expect(bad, 'no 400/431 on any request, including assets and redirects').toEqual([]);
  });

  // KC-34-03 / KC-58-06: OpenMRS roles come from the token at every login; a role added in OpenMRS itself (here
  // "System Developer" written to the database, as a pre-cutover or local grant would be) is gone at the next login.
  test('roles come from the token at every login; a locally added role does not survive @KC-34-03 @KC-58-06 @security @sso-on-only', async ({ browser, request }) => {
    test.skip(!process.env.OPENMRS_DB_EXEC, 'set OPENMRS_DB_EXEC=1 (the test writes a role in the harness database)');
    // Regression (found 2026-10-09 on this harness): the role update commits in a daemon transaction while the login
    // transaction (MariaDB REPEATABLE READ) still saw the old user_role rows, so the first session kept the local role.
    const { username, id } = await nurse(request);
    const om = (await (await admin(request, '/clients?clientId=openmrs')).json())[0].id;
    await admin(request, `/clients/${om}/roles`, 'POST', { name: 'Organizational: Nurse' });     // 409 if it exists: fine
    const role = await (await admin(request, `/clients/${om}/roles/${encodeURIComponent('Organizational: Nurse')}`)).json();
    await admin(request, `/users/${id}/role-mappings/clients/${om}`, 'POST', [role]);
    const roles = async (b: Browser) => {
      const ctx = await b.newContext({ ignoreHTTPSErrors: true });
      const page = await ctx.newPage();
      await page.goto(`${GW}/openmrs/ws/rest/v1/session`);
      await page.fill('#username', username);
      await page.fill('#password', 'Harness-Pass-123');
      await page.click('#kc-login');
      await page.goto(`${GW}/openmrs/oauth2login`);
      await page.waitForLoadState();
      const s = await (await page.request.get(`${GW}/openmrs/ws/rest/v1/session`, { headers: { Accept: 'application/json' } })).json();
      await ctx.close();
      expect(s.authenticated).toBe(true);
      expect(s.user.person.display, 'the session user is still complete after the role swap').toBe('Sita Rai');
      return (s.user.roles as Array<{ display: string }>).map((r) => r.display).sort();
    };
    expect(await roles(browser), 'first login').toEqual(['Organizational: Nurse']);
    const sql = `insert into user_role (user_id, role) select user_id, 'System Developer' from users where system_id = '${id}'`;
    execFileSync('docker', ['exec', 'nidan-sso-openmrs-harness-openmrs-db-1', 'mariadb', '-uopenmrs', '-pharness-openmrs-db', 'openmrs', '-e', sql]);
    expect(await roles(browser), 'after a local System Developer grant and a new login').toEqual(['Organizational: Nurse']);
  });
});
