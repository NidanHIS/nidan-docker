import { test, expect, APIRequestContext, Browser } from '@playwright/test';
import { execFileSync, spawnSync } from 'node:child_process';

/**
 * Central login into the REAL Odoo (backlog KC-37) on tests/e2e-auth/harness/sso-openmrs.yml --profile odoo:
 * Odoo 19 + nidan_keycloak_sso (+ OCA auth_oidc) behind the gateway on https://localhost:8069 (app-odoo gate), Keycloak's
 * login on the gateway's :9443 (18445). Runs when ODOO_SSO_FULL is set. ODOO_DIRECT (127.0.0.1:18069) is a harness-only
 * back door to Odoo itself: test setup over JSON-RPC, and checks of what Odoo answers on its own (production: from
 * inside the Docker network only).
 */
const FULL = !!process.env.ODOO_SSO_FULL;
const KC = process.env.KEYCLOAK_URL ?? 'http://localhost:18180/auth';
const ODOO = 'https://localhost:8069';
const ODOO_DIRECT = 'http://localhost:18069';
const ADMIN_PW = process.env.ODOO_ADMIN_PASSWORD ?? 'Harness-Odoo-Admin-0123456789';

async function admin(request: APIRequestContext, path: string, method = 'GET', data?: unknown) {
  const t = await request.post(`${KC}/realms/master/protocol/openid-connect/token`, {
    form: { grant_type: 'password', client_id: 'admin-cli', username: 'admin', password: 'harness-admin-password' } });
  return request.fetch(`${KC}/admin/realms/nidan${path}`, { method, data, headers: { Authorization: `Bearer ${(await t.json()).access_token}` } });
}

/** Odoo JSON-RPC as the local admin (setup and checks only; the people under test never use a password here). */
async function rpc(request: APIRequestContext, model: string, method: string, args: unknown[], kwargs: object = {}) {
  const r = await request.post(`${ODOO_DIRECT}/jsonrpc`, { data: { jsonrpc: '2.0', method: 'call', id: 1, params: {
    service: 'object', method: 'execute_kw', args: ['nidan', 2, ADMIN_PW, model, method, args, kwargs] } } });
  const body = await r.json();
  if (body.error) throw new Error(JSON.stringify(body.error).slice(0, 400));
  return body.result;
}

async function staff(request: APIRequestContext, odooRoles: string[], attributes: Record<string, string[]> = {}) {
  const username = `odoo${Date.now()}${Math.floor(Math.random() * 1000)}`;
  const r = await admin(request, '/users', 'POST', { username, firstName: 'Ram', lastName: 'Thapa', enabled: true,
    email: `${username}@example.org`, emailVerified: true, attributes: { gender: ['M'], ...attributes }, groups: ['/staff/Nurse'] });
  const id = r.headers()['location'].split('/').pop()!;
  await admin(request, `/users/${id}/reset-password`, 'PUT', { type: 'password', value: 'Harness-Pass-123', temporary: false });
  const odoo = (await (await admin(request, '/clients?clientId=odoo')).json())[0].id;
  for (const n of odooRoles) await admin(request, `/clients/${odoo}/roles`, 'POST', { name: n });   // 409 if it exists: fine
  const roles = await Promise.all(odooRoles.map(async (n) => (await admin(request, `/clients/${odoo}/roles/${encodeURIComponent(n)}`)).json()));
  await admin(request, `/users/${id}/role-mappings/clients/${odoo}`, 'POST', roles);
  const gw = (await (await admin(request, '/clients?clientId=nidan-gateway')).json())[0].id;   // the gateway's :8069 gate
  await admin(request, `/users/${id}/role-mappings/clients/${gw}`, 'POST', [await (await admin(request, `/clients/${gw}/roles/app-odoo`)).json()]);
  return { username, id };
}

/** One full central login into Odoo in a fresh browser context; returns the Odoo user id. */
async function odooLogin(browser: Browser, username: string, keep?: (page: import('@playwright/test').Page) => Promise<void>) {
  const ctx = await browser.newContext();
  const page = await ctx.newPage();
  await page.goto(`${ODOO}/web/login`);              // the gateway sends a person without a session to Keycloak first
  await page.fill('#username', username);
  await page.fill('#password', 'Harness-Pass-123');
  await page.click('#kc-login');
  // Odoo's login page goes on to Keycloak by itself (session there already: no prompt) and back signed in
  await page.waitForURL(/localhost:8069\/(odoo|web)(\/|\?|#|$)/, { timeout: 60_000 });
  const info = await (await page.request.post(`${ODOO}/web/session/get_session_info`, { data: { jsonrpc: '2.0', method: 'call', params: {} } })).json();
  if (keep) await keep(page);
  await ctx.close();
  return info.result.uid as number;
}

async function groupsOf(request: APIRequestContext, uid: number) {
  const [u] = await rpc(request, 'res.users', 'read', [[uid], ['group_ids']]);
  const xmlIds = await rpc(request, 'res.groups', 'get_external_id', [u.group_ids]);
  return Object.values(xmlIds as Record<string, string>).filter(Boolean).sort();
}

test.describe('central login into Odoo', () => {
  test.skip(!FULL, 'needs tests/e2e-auth/harness/sso-openmrs.yml --profile odoo (ODOO_SSO_FULL=1)');
  // KC-37-04 switches the provider off for a moment: the others must not log in meanwhile
  test.describe.configure({ mode: 'serial' });

  test('groups come from the token at every login; a locally added group is gone, base.group_user stays @KC-37-02 @KC-06-02 @security @sso-on-only', async ({ browser, request }) => {
    const { username } = await staff(request, ['base.group_partner_manager']);
    const uid = await odooLogin(browser, username);
    expect(uid, 'Odoo user created on the first central login').toBeGreaterThan(2);
    const first = await groupsOf(request, uid);
    expect(first).toContain('base.group_user');
    expect(first).toContain('base.group_partner_manager');
    expect(first).not.toContain('base.group_system');

    // an Odoo admin gives the person Settings locally, outside Keycloak
    const [system] = await rpc(request, 'ir.model.data', 'search_read', [[['module', '=', 'base'], ['name', '=', 'group_system']]], { fields: ['res_id'] });
    await rpc(request, 'res.users', 'write', [[uid], { group_ids: [[4, system.res_id]] }]);
    expect(await groupsOf(request, uid)).toContain('base.group_system');

    expect(await odooLogin(browser, username), 'same Odoo account').toBe(uid);
    const after = await groupsOf(request, uid);
    expect(after, 'the local grant does not survive the next login').not.toContain('base.group_system');
    expect(after).toContain('base.group_user');
    expect(after).toContain('base.group_partner_manager');
  });

  // KC-37-07 (Odoo itself, ODOO_DIRECT; through the gateway these routes are 404, KC-53-02): a person linked to Keycloak cannot log in to Odoo
  // with a password on any route, even one an Odoo admin set for them. The local admin keeps its own login.
  test('a Keycloak-linked person has no password login on /web/login, session/authenticate, xmlrpc or jsonrpc @KC-37-07 @security @sso-on-only', async ({ browser, request, playwright }) => {
    const { username } = await staff(request, ['base.group_partner_manager']);
    const uid = await odooLogin(browser, username);
    const [linked] = await rpc(request, 'res.users', 'read', [[uid], ['login', 'oauth_uid']]);
    expect(linked.oauth_uid, 'linked by Keycloak sub').toBeTruthy();
    const local = 'Local-Password-Set-By-Admin-42';
    await rpc(request, 'res.users', 'write', [[uid], { password: local }]);
    const login = linked.login as string;
    const anon = await playwright.request.newContext();

    const xml = (path: string, method: string, params: string) => anon.post(`${ODOO_DIRECT}${path}`, { headers: { 'Content-Type': 'text/xml' },
      data: `<?xml version="1.0"?><methodCall><methodName>${method}</methodName><params>${params}</params></methodCall>` });
    const s = (v: string) => `<param><value><string>${v}</string></value></param>`;
    const xr = await (await xml('/xmlrpc/2/common', 'authenticate', s('nidan') + s(login) + s(local) + '<param><value><struct></struct></value></param>')).text();
    expect(xr, 'xmlrpc authenticate').toMatch(/<boolean>0<\/boolean>|<fault>/);

    const jr = await (await anon.post(`${ODOO_DIRECT}/jsonrpc`, { data: { jsonrpc: '2.0', method: 'call', id: 1,
      params: { service: 'common', method: 'login', args: ['nidan', login, local] } } })).json();
    expect(jr.result ?? false, 'jsonrpc login').toBeFalsy();

    const sa = await (await anon.post(`${ODOO_DIRECT}/web/session/authenticate`, { data: { jsonrpc: '2.0', method: 'call',
      params: { db: 'nidan', login, password: local } } })).json();
    expect(sa.result?.uid ?? null, 'web/session/authenticate').toBeNull();

    const ctx = await browser.newContext();
    const page = await ctx.newPage();
    await page.goto(`${ODOO_DIRECT}/web/login?local=1`);   // the password form (a plain GET goes to Keycloak)
    await page.fill('input[name="login"]', login);
    await page.fill('input[name="password"]', local);
    await page.locator('form[action*="/web/login"] button[type="submit"]').first().click();
    await page.waitForLoadState();
    expect(page.url(), '/web/login form').toContain('/web/login');
    await ctx.close();

    // control: the local admin still logs in (operators, central login off)
    const ok = await (await anon.post(`${ODOO_DIRECT}/jsonrpc`, { data: { jsonrpc: '2.0', method: 'call', id: 1,
      params: { service: 'common', method: 'login', args: ['nidan', 'admin', ADMIN_PW] } } })).json();
    expect(ok.result).toBe(2);
    await anon.dispose();
  });

  // Found by review 2026-10-12: Odoo refuses passwords only for Keycloak-linked users, so a signed-in person could post
  // a local account's password (admin, the OIS login) through the gateway and work as that account. The gateway now
  // refuses every password POST on :8069 (auth/http.conf); a plain GET /web/login still goes to central login.
  test('through the gateway nobody signs in to Odoo with a password, not even as the local admin @KC-37-07 @security @sso-on-only', async ({ browser, request }) => {
    const { username } = await staff(request, ['base.group_user']);
    let me = 0;
    const uid = await odooLogin(browser, username, async (page) => {
      for (const path of ['/web/login', '/ne/web/login', '/web/login/']) {
        const r = await page.request.post(`${ODOO}${path}`, { form: { login: 'admin', password: ADMIN_PW, redirect: '/odoo' }, maxRedirects: 0 });
        expect(r.status(), `POST ${path}`).toBe(403);
      }
      const sa = await page.request.post(`${ODOO}/web/session/authenticate`, { data: { jsonrpc: '2.0', method: 'call',
        params: { db: 'nidan', login: 'admin', password: ADMIN_PW } } });
      expect(sa.status(), 'session/authenticate').toBe(403);
      const info = await (await page.request.post(`${ODOO}/web/session/get_session_info`, { data: { jsonrpc: '2.0', method: 'call', params: {} } })).json();
      me = info.result.uid;
    });
    expect(me, 'still the person, never admin').toBe(uid);
    expect(me).not.toBe(2);
  });

  // Open redirect check (review 2026-10-12): Odoo's login and the central-login round trip carry a return address
  // (/web/login?redirect=, the OIDC state). Signed in or not, it must never take the browser off this site.
  test('the login return address never leaves the site @KC-37-07 @security @sso-on-only', async ({ browser, request }) => {
    const { username } = await staff(request, ['base.group_user']);
    const evil = ['https://evil.example/x', '//evil.example/x', '/\\evil.example/x', 'https:evil.example', '%2F%2Fevil.example'];
    // not signed in: through the gateway gate, Keycloak, Odoo's provider and back
    for (const r of evil.slice(0, 3)) {
      const ctx = await browser.newContext();
      const page = await ctx.newPage();
      const hosts: string[] = [];
      page.on('request', (q) => hosts.push(new URL(q.url()).hostname));
      await page.goto(`${ODOO}/web/login?redirect=${encodeURIComponent(r)}`);
      await page.fill('#username', username);
      await page.fill('#password', 'Harness-Pass-123');
      await page.click('#kc-login');
      await page.waitForURL(/localhost:8069\//, { timeout: 60_000 });
      await page.waitForLoadState();
      expect(hosts.filter((h) => h.includes('evil')), `no request to evil for ${r}`).toEqual([]);
      expect(new URL(page.url()).hostname).toBe('localhost');
      await ctx.close();
    }
    // signed in: Odoo answers /web/login?redirect= with a redirect of its own; it must stay on this site
    await odooLogin(browser, username, async (page) => {
      for (const r of evil) {
        const res = await page.request.get(`${ODOO}/web/login?redirect=${encodeURIComponent(r)}`, { maxRedirects: 0 });
        const loc = res.headers()['location'] ?? '';
        expect(new URL(loc || '/', ODOO).hostname, `${r} -> ${res.status()} ${loc}`).toBe('localhost');   // a path here is fine
      }
    });
  });

  // KC-37-04: with central login off (the Nidan provider disabled, as the addon does at install/upgrade when
  // NIDAN_SSO_ENABLED is false) the login page says so, offers no central-login button, and local login works.
  test('central login off: the login page says so and local login works @KC-37-04 @sso-on-only', async ({ page, request }) => {
    const [provider] = await rpc(request, 'auth.oauth.provider', 'search_read', [[['nidan_keycloak', '=', true]]], { fields: ['enabled'] });
    expect(provider.enabled, 'harness starts with central login on').toBe(true);
    await page.goto(`${ODOO_DIRECT}/web/login?local=1`);
    await expect(page.locator('.nidan-central-login-off')).toHaveCount(0);
    await rpc(request, 'auth.oauth.provider', 'write', [[provider.id], { enabled: false }]);
    try {
      await page.goto(`${ODOO_DIRECT}/web/login`);
      await expect(page.locator('.nidan-central-login-off')).toContainText('Central login (Nidan) is switched off');
      await expect(page.locator('a[href*="/protocol/openid-connect/auth"]'), 'no central-login button').toHaveCount(0);
      await page.fill('input[name="login"]', 'admin');
      await page.fill('input[name="password"]', ADMIN_PW);
      await page.locator('form[action*="/web/login"] button[type="submit"]').first().click();
      await page.waitForURL(/localhost:18069\/(odoo|web)(\/|\?|#|$)/);
      expect(page.url()).not.toContain('/web/login');
    } finally {
      await rpc(request, 'auth.oauth.provider', 'write', [[provider.id], { enabled: true }]);
    }
  });

  // KC-53-03 (through the gateway's :8069): the QR payment callbacks are public by necessity
  // (an anonymous phone) and gated by a signed token; a bad token is 403 from Odoo, and the rest of Odoo needs a session.
  test('QR payment callbacks answer without a session, refuse a bad token, everything else needs a login @KC-53-03 @security @sso-on-only', async ({ request, playwright }) => {
    const mk = async (name: string, terminal: string) => {
      const found = await rpc(request, 'pos.payment.method', 'search', [[['name', '=', name]]]);
      return found.length ? found[0] : rpc(request, 'pos.payment.method', 'create', [{ name, use_payment_terminal: terminal }]);
    };
    const esewa = await mk('Harness eSewa QR', 'esewa');
    const khalti = await mk('Harness Khalti QR', 'khalti');
    const anon = await playwright.request.newContext({ maxRedirects: 0 });
    expect((await anon.get(`${ODOO}/pos_esewa_qr/pay?pm=${esewa}&tx=T1&amt=100&token=forged`)).status(), 'eSewa pay, bad token').toBe(403);
    expect((await anon.get(`${ODOO}/pos_khalti_qr/return/${khalti}/forged`)).status(), 'Khalti return, bad token').toBe(403);
    expect((await anon.get(`${ODOO}/pos_khalti_qr/return/${esewa}/forged`)).status(), 'Khalti return on a non-Khalti method').toBe(404);
    expect((await anon.get(`${ODOO}/pos_esewa_qr/return`)).status(), 'eSewa return: informational page').toBe(200);
    for (const path of ['/odoo', '/odoo/action-point_of_sale.action_pos_config_kanban', '/pos/ui']) {
      expect([302, 303, 401], `${path} without a session`).toContain((await anon.get(`${ODOO}${path}`)).status());
    }
    await anon.dispose();
  });

  // KC-53-04: /api/tenant/create (trigonal_ird) would create a company as superuser for anyone; in both copies of the
  // module the controller is never imported (no controllers/__init__.py), so it is not served. Inside Docker too.
  test('/api/tenant/create is not served and creates no company @KC-53-04 @security @sso-on-only', async ({ request }) => {
    const before = await rpc(request, 'res.company', 'search_count', [[]]);
    const probe = (method: string) => execFileSync('docker', ['exec', 'nidan-sso-openmrs-harness-gateway-1', 'sh', '-c',
      `wget -qSO /dev/null ${method === 'POST' ? "--header 'Content-Type: application/json' --post-data '{\"params\":{\"name\":\"x\"}}' " : ''}` +
      "'http://odoo:8069/api/tenant/create?name=x' 2>&1 | grep -m1 HTTP/; true"]).toString();
    for (const m of ['GET', 'POST']) expect(probe(m), m).toMatch(/HTTP\/1\.1 (401|404)/);
    expect(await rpc(request, 'res.company', 'search_count', [[]]), 'no company created').toBe(before);
  });

  // KC-37-01: a cashier opens Odoo, signs in centrally (no Odoo password prompt) and sees only her shops (nidan_shops).
  test('a cashier signs in centrally and sees only the POS shops in her token @KC-37-01 @sso-on-only', async ({ browser, request }) => {
    for (const name of ['Harness Shop A', 'Harness Shop B']) {
      if (!(await rpc(request, 'pos.config', 'search', [[['name', '=', name]]])).length) await rpc(request, 'pos.config', 'create', [{ name }]);
    }
    const { username } = await staff(request, ['point_of_sale.group_pos_user'], { nidan_shops: ['Harness Shop A'] });
    let visible: string[] = [];
    const uid = await odooLogin(browser, username, async (page) => {
      const r = await (await page.request.post(`${ODOO}/web/dataset/call_kw/pos.config/search_read`, { data: { jsonrpc: '2.0', method: 'call',
        params: { model: 'pos.config', method: 'search_read', args: [[]], kwargs: { fields: ['name'] } } } })).json();
      if (r.error) throw new Error(JSON.stringify(r.error).slice(0, 300));
      visible = (r.result as Array<{ name: string }>).map((x) => x.name).sort();
    });
    const groups = await groupsOf(request, uid);
    expect(groups).toContain('point_of_sale.group_pos_user');
    const [emp] = await rpc(request, 'hr.employee', 'search_read', [[['user_id', '=', uid]]], { fields: ['nidan_allowed_pos_ids'] });
    expect(emp, 'the cashier has an employee record').toBeTruthy();
    expect(visible, 'POS shops the cashier can see').toEqual(['Harness Shop A']);
  });

  // KC-14-08: portal-sync (and the middleware) reach Odoo's /api/nidan/* over the internal network with their shared
  // secret (X-Nidan-Webhook-Secret = NIDAN_OIS_SECRET); a wrong secret is refused. Called from inside Docker.
  test('internal /api/nidan/* answers with the shared secret and refuses a wrong one @KC-14-08 @security @sso-on-only', async () => {
    const OIS = process.env.NIDAN_OIS_SECRET ?? 'harness-ois-secret-0123456789';
    const call = (path: string, secret: string, params: object) => JSON.parse(execFileSync('docker', ['exec',
      'nidan-sso-openmrs-harness-gateway-1', 'sh', '-c', `wget -qO- --header 'Content-Type: application/json' ` +
      `--header 'X-Nidan-Webhook-Secret: ${secret}' --post-data '${JSON.stringify({ jsonrpc: '2.0', method: 'call', params })}' ` +
      `'http://odoo:8069${path}'`]).toString());
    const patient = { patient_uuid: '00000000-0000-4000-8000-000000000000' };
    expect(call('/api/nidan/billing/snapshot', OIS, patient).result, 'billing snapshot, right secret').toMatchObject({ status: 'ok' });
    expect(call('/api/nidan/billing/snapshot', 'wrong', patient).result).toEqual({ error: 'unauthorized' });
    const ok = call('/api/nidan/portal-appointments/assigned', OIS, {}).result;
    expect(ok?.error, `portal appointments, right secret: ${JSON.stringify(ok)}`).not.toBe('unauthorized');
    expect(call('/api/nidan/portal-appointments/assigned', 'wrong', {}).result?.error ?? JSON.stringify(call('/api/nidan/portal-appointments/assigned', 'wrong', {})))
      .toMatch(/unauthori[sz]ed/i);
  });

  // KC-53-02: a signed-in cashier (app-odoo) still gets 404 from the gateway for Odoo's service APIs, the database
  // manager and RPC; those are for services inside the network only.
  test('service APIs, DB manager and RPC are 404 on :8069 even for a signed-in cashier @KC-53-02 @security @sso-on-only', async ({ browser, request }) => {
    const { username } = await staff(request, ['point_of_sale.group_pos_user']);
    await odooLogin(browser, username, async (page) => {
      for (const path of ['/api/tenant/create', '/api/nidan/orders', '/api/nidan/patient/payment-status?patient_uuid=x',
        '/web/database/manager', '/web/database/backup', '/jsonrpc', '/xmlrpc/2/common']) {
        expect((await page.request.get(`${ODOO}${path}`, { maxRedirects: 0 })).status(), `GET ${path}`).toBe(404);
        expect((await page.request.post(`${ODOO}${path}`, { data: {}, maxRedirects: 0 })).status(), `POST ${path}`).toBe(404);
      }
      expect((await page.request.get(`${ODOO}/odoo`)).status(), 'control: the app itself works').toBe(200);
    });
  });

  // KC-10-08: the POS/bus websocket opens for a signed-in cashier; without a gateway session the upgrade gets 401.
  test('the POS websocket connects with a session; without one the upgrade is 401 @KC-10-08 @security @sso-on-only', async ({ browser, request }) => {
    const { username } = await staff(request, ['point_of_sale.group_pos_user']);
    let opened = '';
    await odooLogin(browser, username, async (page) => {
      opened = await page.evaluate(() => new Promise<string>((resolve) => {
        const ws = new WebSocket(`wss://${location.host}/websocket?version=19.0`);
        ws.onopen = () => { ws.close(); resolve('open'); };
        ws.onerror = () => resolve('error');
        setTimeout(() => resolve('timeout'), 15_000);
      }));
    });
    expect(opened).toBe('open');
    const code = execFileSync('curl', ['-sk', '-o', '/dev/null', '-w', '%{http_code}', '--http1.1',
      '-H', 'Connection: Upgrade', '-H', 'Upgrade: websocket', '-H', 'Sec-WebSocket-Version: 13',
      '-H', 'Sec-WebSocket-Key: dGhlIHNhbXBsZSBub25jZQ==', '-H', 'Origin: https://localhost:8069',
      `${ODOO}/websocket?version=19.0`]).toString();
    expect(code).toBe('401');
  });

  // KC-33-02: Odoo reads OpenMRS with an odoo-sync token (OPENMRS_AUTH_MODE=jwt): it is the read-only nidan-odoo
  // account, the visit fetch works, and the attachment (document) list for that visit answers.
  test('Odoo fetches a visit and its documents from OpenMRS with a Bearer token, as nidan-odoo @KC-33-02 @sso-on-only', async ({ request }) => {
    const sqlOmrs = (q: string) => execFileSync('docker', ['exec', '-i', 'nidan-sso-openmrs-harness-openmrs-db-1', 'sh', '-c',
      'mariadb -N -uopenmrs -pharness-openmrs-db openmrs'], { input: q }).toString().trim();
    const [visitUuid, patientUuid] = sqlOmrs('select v.uuid, pe.uuid from visit v join person pe on pe.person_id = v.patient_id ' +
      'where v.voided = 0 order by v.visit_id desc limit 1;').split('\t');
    expect(visitUuid, 'OpenMRS has a visit (cis-sync.spec creates one)').toBeTruthy();
    const icp = (key: string, value: string) => rpc(request, 'ir.config_parameter', 'set_param', [key, value]);
    await icp('nidan.openmrs.enabled', 'True');
    await icp('nidan.openmrs.base_url', 'http://openmrs-backend:8080/openmrs');
    await icp('nidan.openmrs.username', '');
    await icp('nidan.openmrs.password', '');
    const get = (path: string, params: object = {}) => rpc(request, 'nidan.openmrs.client', 'get_json', [path, params]);

    const session = await get('/ws/rest/v1/session');
    expect(session.ok, JSON.stringify(session).slice(0, 300)).toBe(true);
    expect(session.body.user.username, 'the read-only machine account').toBe('nidan-odoo');
    const visit = await get(`/ws/rest/v1/visit/${visitUuid}`);
    expect(visit.ok, JSON.stringify(visit).slice(0, 300)).toBe(true);
    expect(visit.body.uuid).toBe(visitUuid);
    const docs = await get('/ws/rest/v1/attachment', { patient: patientUuid, visit: visitUuid, includeEncounterless: 'true' });
    expect(docs.ok, JSON.stringify(docs).slice(0, 300)).toBe(true);
    expect(Array.isArray(docs.body.results)).toBe(true);
  });

  // KC-11-02: a cashier's open POS/bus websocket closes once she is disabled the way the Staff page does it (sign out
  // everywhere -> Keycloak's back-channel logout to Odoo, then disable), and a reconnect gets 401 within 6 min.
  // The realm import sets Odoo's back-channel to each stack's gateway (GATEWAY_AUTH_IP; this harness 172.31.252.10).
  test('disabling a cashier closes her open POS websocket; reconnects get 401 within 6 min @KC-11-02 @security @slow @sso-on-only', async ({ browser, request }) => {
    test.setTimeout(10 * 60_000);
    const oc = (await (await admin(request, '/clients?clientId=odoo')).json())[0];
    expect(oc.attributes['backchannel.logout.url'], 'the realm points at this gateway, not production\'s address')
      .toBe('http://172.31.252.10:8099/backchannel-logout/odoo');
    const { username, id } = await staff(request, ['point_of_sale.group_pos_user']);
    let state = { log: [] as string[] };
    await odooLogin(browser, username, async (page) => {
      await page.evaluate(() => {
        const w = window as unknown as { wsLog: string[] };
        w.wsLog = [];
        const ws = new WebSocket(`wss://${location.host}/websocket?version=19.0-2`);   // = Odoo's worker version
        ws.onopen = () => { w.wsLog.push('open'); ws.send(JSON.stringify({ event_name: 'subscribe', data: { channels: [], last: 0 } })); };
        ws.onclose = (e) => w.wsLog.push(`close ${e.code}`);
      });
      await expect.poll(() => page.evaluate(() => (window as unknown as { wsLog: string[] }).wsLog)).toEqual(['open']);
      const t0 = Date.now();
      expect((await admin(request, `/users/${id}/logout`, 'POST')).status()).toBe(204);    // Staff page: sign out first
      expect((await admin(request, `/users/${id}`, 'PUT', { enabled: false })).status()).toBe(204);
      // the next bus message to her (any POS or chatter activity) makes Odoo re-check the session
      execFileSync('docker', ['exec', '-i', 'nidan-sso-openmrs-harness-odoo-1', 'sh', '-c',
        'odoo shell -c /etc/odoo/odoo.conf -d nidan --no-http 2>/dev/null'], { input:
        `u = env['res.users'].search([('login', '=', '${username}')])\nenv['bus.bus']._sendone(u.partner_id, 'nidan-test', {})\nenv.cr.commit()\n` });
      await expect.poll(() => page.evaluate(() => (window as unknown as { wsLog: string[] }).wsLog), { timeout: 60_000 })
        .toEqual(['open', 'close 4001']);                                                   // 4001 = session expired
      // reconnecting through the gateway: refused once oauth2-proxy re-checks Keycloak (cookie_refresh 1m)
      const upgrade = async () => {
        const cookie = (await page.context().cookies(ODOO)).map((c) => `${c.name}=${c.value}`).join('; ');
        // a 101 keeps the connection open until --max-time (curl exits 28); the status code is printed either way
        return spawnSync('curl', ['-sk', '-o', '/dev/null', '-w', '%{http_code}', '--http1.1', '--max-time', '3', '-H', `Cookie: ${cookie}`,
          '-H', 'Connection: Upgrade', '-H', 'Upgrade: websocket', '-H', 'Sec-WebSocket-Version: 13',
          '-H', 'Sec-WebSocket-Key: dGhlIHNhbXBsZSBub25jZQ==', '-H', 'Origin: https://localhost:8069',
          `${ODOO}/websocket?version=19.0-2`]).stdout.toString();
      };
      await expect.poll(upgrade, { timeout: 6 * 60_000, intervals: [15_000] }).toBe('401');
      state = { log: [`refused after ${Math.round((Date.now() - t0) / 1000)} s`] };
    });
    console.log(`KC-11-02: ${state.log.join(' ')}`);
  });

  // bug 2026-10-11: the launcher's Odoo card stopped on Odoo's own login page (password form, "Sign in with Nidan",
  // "Don't have an account?"). With central login on, a plain visit goes straight to Keycloak; nobody can sign up.
  test('the login page goes straight to central login and offers no sign-up @KC-37-07 @KC-10-05 @security @sso-on-only', async ({ request }) => {
    const r = await request.get(`${ODOO_DIRECT}/web/login?redirect=%2Fodoo%3F`, { maxRedirects: 0 });
    expect(r.status(), 'redirect, not the login page').toBe(303);
    const loc = new URL(r.headers()['location']);
    expect(loc.pathname).toMatch(/\/realms\/nidan\/protocol\/openid-connect\/auth$/);
    expect(decodeURIComponent(JSON.parse(loc.searchParams.get('state')!).r), 'keeps where the person was going').toContain('/odoo');
    const failed = await request.get(`${ODOO_DIRECT}/web/login?oauth_error=3`, { maxRedirects: 0 });
    expect(failed.status(), 'after a refused sign-in the page shows (no loop)').toBe(200);
    expect(await failed.text()).toContain('/protocol/openid-connect/auth');
    const signup = await request.get(`${ODOO_DIRECT}/web/signup`, { maxRedirects: 0 });
    expect(signup.status() === 200 && (await signup.text()).includes('name="password"'), 'no self sign-up form').toBe(false);
  });
});

