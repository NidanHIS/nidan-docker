import { test, expect, APIRequestContext, Browser } from '@playwright/test';
import { execFileSync } from 'node:child_process';

/**
 * Central login into the REAL OpenELIS (backlog KC-38) on tests/e2e-auth/harness/sso-openmrs.yml --profile openelis
 * (OpenELIS built from the working tree; gateway on https://localhost, :8443 published as 18444). Runs when
 * OPENELIS_SSO_FULL is set. The frontend's entry point is /api/OpenELIS-Global/oauth2/authorization/nidan-keycloak.
 */
const FULL = !!process.env.OPENELIS_SSO_FULL;
const KC = process.env.KEYCLOAK_URL ?? 'http://localhost:18180/auth';
const API = 'https://localhost/api/OpenELIS-Global';
const P8443 = 'https://localhost:18444/OpenELIS-Global';
const LAB_TECH = ['Results', 'Validation', 'Reception'];

async function admin(request: APIRequestContext, path: string, method = 'GET', data?: unknown) {
  const t = await request.post(`${KC}/realms/master/protocol/openid-connect/token`, {
    form: { grant_type: 'password', client_id: 'admin-cli', username: 'admin', password: 'harness-admin-password' } });
  return request.fetch(`${KC}/admin/realms/nidan${path}`, { method, data, headers: { Authorization: `Bearer ${(await t.json()).access_token}` } });
}

/** A central account with the gateway's app-openelis role and the given OpenELIS client roles (no app-odoo). */
async function labStaff(request: APIRequestContext, openelisRoles: string[], firstName = 'Sita', lastName = 'Shrestha') {
  const username = `lab${Date.now()}${Math.floor(Math.random() * 1000)}`;
  const r = await admin(request, '/users', 'POST', { username, firstName, lastName, enabled: true,
    attributes: { gender: ['F'] }, groups: ['/staff/Lab Tech'] });
  const id = r.headers()['location'].split('/').pop()!;
  await admin(request, `/users/${id}/reset-password`, 'PUT', { type: 'password', value: 'Harness-Pass-123', temporary: false });
  const gw = (await (await admin(request, '/clients?clientId=nidan-gateway')).json())[0].id;
  await admin(request, `/users/${id}/role-mappings/clients/${gw}`, 'POST', [await (await admin(request, `/clients/${gw}/roles/app-openelis`)).json()]);
  const oe = (await (await admin(request, '/clients?clientId=openelis')).json())[0].id;
  const roles = await Promise.all(openelisRoles.map(async (n) => (await admin(request, `/clients/${oe}/roles/${encodeURIComponent(n)}`)).json()));
  await admin(request, `/users/${id}/role-mappings/clients/${oe}`, 'POST', roles);
  return { username, id };
}

/** Gateway login, then the frontend's SSO button; returns the page (signed in to OpenELIS) and its context. */
async function openelisLogin(browser: Browser, username: string) {
  const ctx = await browser.newContext({ ignoreHTTPSErrors: true });
  const page = await ctx.newPage();
  await page.goto('https://localhost/openelis/');
  await page.fill('#username', username);
  await page.fill('#password', 'Harness-Pass-123');
  await page.click('#kc-login');
  // the SPA's own /session calls create an OpenELIS session cookie; let them finish before the SSO button's
  // request, or the two race for the cookie and the callback finds no saved authorization request
  await page.waitForLoadState('networkidle');
  await page.goto(`${API}/oauth2/authorization/nidan-keycloak`);   // Keycloak session exists: no second prompt
  await page.waitForURL(/localhost\/openelis\/(?!login)/, { timeout: 60_000 });
  return { page, ctx };
}

async function session(request: APIRequestContext) {
  return (await request.get(`${API}/session`, { headers: { Accept: 'application/json' } })).json();
}

function sql(query: string) {
  return execFileSync('docker', ['exec', '-i', 'nidan-sso-openmrs-harness-openelis-db-1', 'sh', '-c',
    'psql -tA -v ON_ERROR_STOP=1 -U "$POSTGRES_USER" -d "$POSTGRES_DB"'], { input: query }).toString().trim();
}

test.describe('OpenELIS central login', () => {
  test.skip(!FULL, 'needs tests/e2e-auth/harness/sso-openmrs.yml --profile openelis (OPENELIS_SSO_FULL=1)');
  test.describe.configure({ mode: 'serial' });

  // KC-38-01: the first central login creates the OpenELIS user, linked by the Keycloak sub, and she arrives in the
  // React app with her Lab Tech roles for every lab unit (what its pages are gated on). A colleague with the same
  // name gets his own account (found live: the hand-entry name rule refused him with a 500).
  test('first login provisions the user by sub with the Lab Tech roles; a same-name colleague also gets in @KC-38-01 @sso-on-only', async ({ browser, request }) => {
    const sita = await labStaff(request, LAB_TECH);
    const { page, ctx } = await openelisLogin(browser, sita.username);
    const s = await session(page.request);
    expect(s.authenticated).toBe(true);
    expect(s.loginName).toBe(sita.username);
    expect([...s.roles].sort()).toEqual([...LAB_TECH].sort());
    expect([...s.userLabRolesMap.AllLabUnits].sort()).toEqual([...LAB_TECH].sort());
    expect(sql(`SELECT external_id FROM clinlims.system_user WHERE login_name = '${sita.username}';`)).toBe(sita.id);
    await ctx.close();

    const twin = await labStaff(request, ['Results'], 'Sita', 'Shrestha');
    const second = await openelisLogin(browser, twin.username);
    const t = await session(second.page.request);
    expect(t.authenticated).toBe(true);
    expect(t.userId).not.toBe(s.userId);
    expect(t.roles).toEqual(['Results']);
    await second.ctx.close();
  });

  // KC-38-03: the API the logged-in frontend calls works; without a gateway session both entry points answer 401.
  test('API works with a session; :8443 and /api answer 401 without one @KC-38-03 @security @sso-on-only', async ({ browser, request, playwright }) => {
    const u = await labStaff(request, ['Results']);
    const { page, ctx } = await openelisLogin(browser, u.username);
    const menu = await page.request.get(`${API}/rest/menu`, { headers: { Accept: 'application/json' } });
    expect(menu.status()).toBe(200);
    expect(Array.isArray(await menu.json())).toBe(true);
    // with the gateway session :8443 reaches OpenELIS (its own redirect), not the gateway's 401
    expect((await page.request.get(`${P8443}/session`, { maxRedirects: 0 })).status()).not.toBe(401);
    await ctx.close();

    const anon = await playwright.request.newContext({ ignoreHTTPSErrors: true });
    for (const url of [`${P8443}/session`, `${P8443}/rest/menu`, `${API}/session`, `${API}/rest/menu`]) {
      expect((await anon.get(url, { headers: { Accept: 'application/json' }, maxRedirects: 0 })).status(), url).toBe(401);
    }
    await anon.dispose();
  });

  // KC-14-05: a lab tech without app-odoo runs the OpenELIS paywall check for the unpaid patient: a real Odoo answer
  // (block, Odoo's payment status), never the outage allow.
  test('the paywall check for an unpaid patient blocks with Odoo reached @KC-14-05 @security @sso-on-only', async ({ browser, request }) => {
    test.skip(!process.env.PAYWALL_PATIENT, 'set PAYWALL_PATIENT to the harness patient with an unpaid Odoo bill');
    const u = await labStaff(request, ['Results']);
    const { page, ctx } = await openelisLogin(browser, u.username);
    const r = await page.request.get(`${API}/rest/nidan/paywall/check?patientUuid=${process.env.PAYWALL_PATIENT}`,
      { headers: { Accept: 'application/json' } });
    expect(r.status()).toBe(200);
    const body = await r.json();
    expect(body.decision).toBe('block');
    expect(body.blocked).toBe(true);
    expect(body.paymentStatus, 'Odoo answered (an outage allow carries no payment status)').toBeTruthy();
    await ctx.close();
  });

  // Keycloak allows usernames up to 40 characters and names up to 255; OpenELIS's system_user columns were 20/20/30, so
  // such a person could never sign in (found live with a 25-character username; widened by a Nidan changeset).
  test('a 40-character username and long names still provision @KC-38-01 @sso-on-only', async ({ browser, request }) => {
    const username = `lab${Date.now()}`.padEnd(40, 'x');
    const first = 'Bishnuprasad Kumari Rajbhandari';             // 31 characters
    const last = 'Shrestha-Maharjan-Bajracharya-Tuladhar';       // 38 characters
    const r = await admin(request, '/users', 'POST', { username, firstName: first, lastName: last, enabled: true,
      attributes: { gender: ['F'] }, groups: ['/staff/Lab Tech'] });
    expect(r.status(), 'Keycloak accepts the 40-character username').toBe(201);
    const id = r.headers()['location'].split('/').pop()!;
    await admin(request, `/users/${id}/reset-password`, 'PUT', { type: 'password', value: 'Harness-Pass-123', temporary: false });
    const gw = (await (await admin(request, '/clients?clientId=nidan-gateway')).json())[0].id;
    await admin(request, `/users/${id}/role-mappings/clients/${gw}`, 'POST', [await (await admin(request, `/clients/${gw}/roles/app-openelis`)).json()]);
    const { page, ctx } = await openelisLogin(browser, username);
    const s = await session(page.request);
    expect(s.authenticated).toBe(true);
    expect(s.loginName).toBe(username);
    expect([s.firstName, s.lastName]).toEqual([first, last]);
    await ctx.close();
  });

  // KC-38-05 in the UI: with central login in charge the login page (shown only when the automatic redirect is held
  // back, e.g. right after a failed sign-in) offers the single sign-on button and no password form, which the gateway
  // refuses anyway (/ValidateLogin -> 403).
  test('the OpenELIS login page shows no password form under central login, only single sign-on @KC-38-05 @security @sso-on-only', async ({ browser, request }) => {
    const { username } = await labStaff(request, LAB_TECH);
    const ctx = await browser.newContext({ ignoreHTTPSErrors: true });
    // hold back the automatic redirect, as after a failed sign-in (Login.js: at most once per 30 s)
    await ctx.addInitScript(() => { try { sessionStorage.setItem('oauthAutoRedirect', String(Date.now())); } catch (e) { /* none */ } });
    const page = await ctx.newPage();
    await page.goto('https://localhost/openelis/');
    await page.fill('#username', username);
    await page.fill('#password', 'Harness-Pass-123');
    await page.click('#kc-login');
    await page.waitForURL(/localhost\/openelis\/login/, { timeout: 60_000 });
    await expect(page.getByRole('button', { name: /Single Sign On/i })).toBeVisible();
    await expect(page.locator('#loginName'), 'no username field').toHaveCount(0);
    await expect(page.locator('input[type="password"]'), 'no password field').toHaveCount(0);
    await ctx.close();
  });
});

