import { test, expect, APIRequestContext, Page } from '@playwright/test';
import { execFileSync } from 'node:child_process';

/**
 * Real central login end to end (backlog KC-10, KC-11, KC-19, KC-56) on tests/e2e-auth/harness/sso.yml: the
 * gateway, the real oauth2-proxy and the real Keycloak, with stub apps that echo what they receive.
 * Runs when SSO_FULL is set. Users are created per test through the admin API and given gateway roles directly
 * (the base realm's job groups carry none; sites map them).
 */
const FULL = !!process.env.SSO_FULL;
const KC = process.env.KEYCLOAK_URL ?? 'http://localhost:18180/auth';                 // admin API
const KC_PUBLIC = process.env.KEYCLOAK_PUBLIC_URL ?? 'https://localhost:18445/auth';  // login pages: the gateway's :9443 (D1)
const GW = process.env.GATEWAY_URL ?? 'https://localhost:18443';

async function adminToken(request: APIRequestContext) {
  const r = await request.post(`${KC}/realms/master/protocol/openid-connect/token`, {
    form: { grant_type: 'password', client_id: 'admin-cli', username: 'admin', password: 'harness-admin-password' } });
  return (await r.json()).access_token as string;
}
async function admin(request: APIRequestContext, path: string, method = 'GET', data?: unknown) {
  return request.fetch(`${KC}/admin/realms/nidan${path}`, { method, data, headers: { Authorization: `Bearer ${await adminToken(request)}` } });
}

/** A staff user with the given nidan-gateway roles; returns the username. */
async function staff(request: APIRequestContext, roles: string[]) {
  const username = `sso${Date.now()}${Math.floor(Math.random() * 1000)}`;
  const r = await admin(request, '/users', 'POST', { username, firstName: 'Sita', lastName: 'Rai', enabled: true,
    attributes: { gender: ['F'] }, groups: ['/staff/Nurse'] });
  expect(r.status()).toBe(201);
  const id = r.headers()['location'].split('/').pop()!;
  await admin(request, `/users/${id}/reset-password`, 'PUT', { type: 'password', value: 'Harness-Pass-123', temporary: false });
  const gw = (await (await admin(request, '/clients?clientId=nidan-gateway')).json())[0].id;
  const all: Array<{ id: string; name: string }> = await (await admin(request, `/clients/${gw}/roles`)).json();
  await admin(request, `/users/${id}/role-mappings/clients/${gw}`, 'POST', all.filter((x) => roles.includes(x.name)));
  return username;
}

async function signIn(page: Page, username: string) {
  await expect(page).toHaveURL(new RegExp('^' + KC_PUBLIC.replace(/[.*+?^${}()|[\]\\]/g, '\\$&') + '/realms/nidan/'));
  await page.fill('#username', username);
  await page.fill('#password', 'Harness-Pass-123');
  await page.click('#kc-login');
}

const echoed = async (page: Page) => JSON.parse(await page.locator('body').innerText());

test.describe('central login, end to end', () => {
  test.skip(!FULL, 'needs tests/e2e-auth/harness/sso.yml (SSO_FULL=1)');

  test('a page without a session goes to Keycloak and comes back to it; the next app needs no second login @KC-10-04 @KC-10-05 @security @sso-on-only', async ({ page, request }) => {
    const user = await staff(request, ['app-openmrs', 'app-openelis']);
    await page.goto(`${GW}/openmrs/spa/home?x=1`);
    await signIn(page, user);
    await expect(page).toHaveURL(`${GW}/openmrs/spa/home?x=1`);
    expect((await echoed(page)).path).toBe('/home?x=1');                // the SPA upstream sees its own path, query kept
    await page.goto(`${GW}/openmrs/ws/rest/v1/session`);
    await expect(page).toHaveURL(`${GW}/openmrs/ws/rest/v1/session`);   // no Keycloak page in between
    expect((await echoed(page)).path).toBe('/openmrs/ws/rest/v1/session');
  });

  test('an app the person has no role for shows not-allowed, with a real session @KC-10-06 @security @sso-on-only', async ({ page, request }) => {
    const user = await staff(request, ['app-odoo']);
    await page.goto(`${GW}/ohif/viewer?StudyInstanceUIDs=1`);
    await signIn(page, user);
    await expect(page).toHaveURL(/\/not-allowed\.html\?app=orthanc$/);
  });

  test('apps receive no token and no client-sent identity, only the gateway-set user @KC-56-01 @security @sso-on-only', async ({ page, request }) => {
    const user = await staff(request, ['app-openmrs']);
    await page.setExtraHTTPHeaders({ 'X-Forwarded-User': 'admin', 'X-Auth-Request-User': 'admin', 'X-Auth-Request-Access-Token': 'forged' });
    await page.goto(`${GW}/openmrs/ws/rest/v1/session`);
    await signIn(page, user);
    const h = (await echoed(page)).headers as Record<string, string>;
    for (const k of ['authorization', 'x-auth-request-access-token', 'x-forwarded-access-token', 'x-forwarded-user', 'x-jwt-assertion']) {
      expect(h[k] ?? '', k).toBe('');
    }
    expect(h['x-auth-request-user'] ?? '', 'forged user header').not.toBe('admin');
    expect(JSON.stringify(h)).not.toMatch(/eyJ[A-Za-z0-9_-]{10,}\./);   // no JWT anywhere in what the app sees
  });

  test('after login, rd can only send the browser back to the gateway @KC-56-07 @security @sso-on-only', async ({ page, request }) => {
    const user = await staff(request, ['app-openmrs']);
    await page.goto(`${GW}/openmrs/spa/home`);
    await signIn(page, user);
    await expect(page).toHaveURL(`${GW}/openmrs/spa/home`);
    for (const rd of ['https://evil.example/', '//evil.example/', '/\\evil.example/', 'https://localhost.evil.example/',
      'https://localhost:18443@evil.example/', 'https://localhost:9999/']) {
      await page.goto(`${GW}/oauth2/start?rd=${encodeURIComponent(rd)}`);
      await page.waitForLoadState();
      expect(new URL(page.url()).host, rd).toBe('localhost:18443');
    }
  });

  test('signing out ends the Keycloak session too; the next person gets her own gateway session @KC-11-04 @KC-11-06 @security @sso-on-only', async ({ page, request }) => {
    // Gateway part of KC-11-04: apps get no identity from the gateway (each runs its own OIDC login), so "every app
    // shows B" needs the real apps; here the gateway session itself must belong to B.
    const a = await staff(request, ['app-openmrs']);
    await page.goto(`${GW}/openmrs/spa/home`);
    await signIn(page, a);
    await expect(page).toHaveURL(`${GW}/openmrs/spa/home`);
    await page.goto(`${GW}/oauth2/sign_out`);
    await page.waitForLoadState();
    await page.goto(`${GW}/openmrs/spa/home`);
    await expect(page.locator('#username'), 'Keycloak login form again (no silent SSO)').toBeVisible();
    const id = (await (await admin(request, `/users?username=${a}&exact=true`)).json())[0].id;
    expect(await (await admin(request, `/users/${id}/sessions`)).json(), 'no Keycloak session left for A').toEqual([]);
    const b = await staff(request, ['app-openmrs']);
    await signIn(page, b);
    await expect(page).toHaveURL(`${GW}/openmrs/spa/home`);
    const who = await page.request.get(`${GW}/oauth2/userinfo`);
    expect((await who.json()).preferredUsername ?? (await who.json()).user).toBe(b);
  });

  for (const [app, role, logout, reopen] of [
    ['Superset', 'app-superset', '/superset/logout/', '/superset/welcome/'],
    ['DHIS2', 'app-dhis2', '/dhis2/auth/logout', '/dhis2/'],
    ['OpenELIS', 'app-openelis', '/api/OpenELIS-Global/Logout', '/api/OpenELIS-Global/rest/x'],
  ] as const) {
    test(`${app} as the person's only app: its own logout signs out of central login; reopening it needs a login @KC-19-01 @security @sso-on-only`, async ({ page, request }) => {
      const user = await staff(request, [role]);
      await page.goto(GW + reopen);
      await signIn(page, user);
      await expect(page).toHaveURL(GW + reopen);
      if (app === 'OpenELIS') {                         // its button POSTs a form; only that runs the chain
        const get = await page.request.get(GW + logout, { maxRedirects: 0 });   // logs nobody out: no false "signed out"
        expect(get.headers()['location'] ?? '', 'GET logout: no logout chain').not.toContain('signed_out');
        await page.evaluate((url) => {
          const f = document.createElement('form'); f.method = 'POST'; f.action = url; document.body.append(f); f.submit();
        }, GW + logout);
        await page.waitForURL((u) => !u.pathname.endsWith('/Logout'));
      } else {
        await page.goto(GW + logout);                   // the app redirects; the gateway -> launcher -> sign_out (one app)
      }
      await page.waitForLoadState();
      const id = (await (await admin(request, `/users?username=${user}&exact=true`)).json())[0].id;
      await expect.poll(async () => (await (await admin(request, `/users/${id}/sessions`)).json()).length,
        'Keycloak session ended').toBe(0);
      await page.goto(GW + reopen);
      await expect(page.locator('#username'), 'login form again').toBeVisible();
    });
  }

  // Rule 7 as changed 2026-10-12: with several apps, one app's logout returns to the launcher (signed out of that app,
  // a notice, "sign out of everything"); the central session and the other apps stay.
  test('with several apps, an app\'s own logout returns to the launcher and the other apps stay open @KC-19-01 @security @sso-on-only', async ({ page, request }) => {
    const user = await staff(request, ['app-superset', 'app-dhis2']);
    await page.goto(GW + '/superset/welcome/');
    await signIn(page, user);
    await expect(page).toHaveURL(GW + '/superset/welcome/');
    await page.goto(GW + '/superset/logout/');
    await expect(page).toHaveURL(GW + '/');              // ?signed_out= is dropped from the address bar
    await expect(page.locator('#signed-out')).toBeVisible();
    await expect(page.locator('#signed-out a[href="/oauth2/sign_out"]'), 'sign out of everything').toBeVisible();
    await page.goto(GW + '/dhis2/');
    await expect(page, 'the other app opens with no new login').toHaveURL(GW + '/dhis2/');
  });

  test('the session cookie is refreshed after a minute, and a disabled user is out at the next refresh @KC-10-07 @KC-11-01 @security @slow @sso-on-only', async ({ page, request }) => {
    test.setTimeout(240_000);
    const user = await staff(request, ['app-openmrs']);
    await page.goto(`${GW}/openmrs/spa/home`);
    await signIn(page, user);
    await expect(page).toHaveURL(`${GW}/openmrs/spa/home`);
    const api = () => page.request.get(`${GW}/openmrs/ws/rest/v1/session`, { headers: { Accept: 'application/json' }, maxRedirects: 0 });
    await page.waitForTimeout(65_000);                          // past cookie_refresh = 1m
    const refreshed = await api();
    expect(refreshed.status()).toBe(200);
    expect(refreshed.headersArray().some((h) => h.name.toLowerCase() === 'set-cookie' && /^_oauth2_proxy=/.test(h.value)),
      'refreshed _oauth2_proxy cookie (KC-10-07)').toBe(true);
    const id = (await (await admin(request, `/users?username=${user}&exact=true`)).json())[0].id;
    expect((await admin(request, `/users/${id}`, 'PUT', { enabled: false })).status()).toBe(204);
    const disabledAt = Date.now();
    await page.waitForTimeout(65_000);                          // the next refresh asks Keycloak again
    const after = await api();
    expect([401, 403], `disabled user still served ${Math.round((Date.now() - disabledAt) / 1000)} s later`).toContain(after.status());
    console.log(`KC-11-01 gateway: refused ${Math.round((Date.now() - disabledAt) / 1000)} s after disabling`);
  });

  test('sign-out expires every app session cookie on its own path @KC-11-05 @security @sso-on-only', async ({ page, request }) => {
    const user = await staff(request, ['app-openmrs']);
    await page.goto(`${GW}/openmrs/spa/home`);
    await signIn(page, user);
    await expect(page).toHaveURL(`${GW}/openmrs/spa/home`);
    const r = await page.request.get(`${GW}/oauth2/sign_out`, { maxRedirects: 0 });
    const expired = r.headersArray().filter((h) => h.name.toLowerCase() === 'set-cookie')
      .filter((h) => /max-age=0/i.test(h.value) || /expires=thu, 01 jan 1970/i.test(h.value))
      .map((h) => `${h.value.split('=')[0]} ${h.value.match(/path=([^;]+)/i)?.[1] ?? '?'}`);
    for (const want of ['_oauth2_proxy /', 'session_id /', 'JSESSIONID /openmrs', 'JSESSIONID /OpenELIS-Global',
      'JSESSIONID /api/OpenELIS-Global', 'superset_session /superset', 'nidan_session /', 'nidan_session /dhis2']) {
      expect(expired, want).toContain(want);
    }
  });

  test('with Keycloak down for 4 minutes, open sessions keep working and nobody is asked to log in again @KC-11-03 @slow @sso-on-only', async ({ page, request }) => {
    // Run alone and last (e2e-gateway.yml): other tests fail while Keycloak is down, and the dev-mem realm is lost
    // when the container restarts.
    test.skip(!process.env.SSO_DOCKER, 'stops and starts the harness Keycloak (SSO_DOCKER=1)');
    test.setTimeout(420_000);
    const user = await staff(request, ['app-openmrs']);
    await page.goto(`${GW}/openmrs/spa/home`);
    await signIn(page, user);
    await expect(page).toHaveURL(`${GW}/openmrs/spa/home`);
    const api = () => page.request.get(`${GW}/openmrs/ws/rest/v1/session`, { headers: { Accept: 'application/json' }, maxRedirects: 0 });
    const kc = 'nidan-sso-harness-keycloak-1';
    execFileSync('docker', ['stop', kc]);
    const statuses: number[] = [];
    try {
      for (let t = 0; t < 4; t++) {                       // 4 x 60 s, each past cookie_refresh (1 m)
        await page.waitForTimeout(60_000);
        statuses.push((await api()).status());
      }
    } finally {
      execFileSync('docker', ['start', kc]);
    }
    console.log(`KC-11-03 statuses while Keycloak was down: ${statuses.join(' ')}`);
    expect(statuses, 'every request during the outage served').toEqual([200, 200, 200, 200]);
  });

  test('"Change password" on the launcher runs Keycloak\'s update-password and returns to the launcher @KC-55-05 @security @sso-on-only', async ({ page, request, browser }) => {
    const user = await staff(request, ['app-openmrs', 'app-openelis']);   // two apps: one-app users skip the launcher
    await page.goto(`${GW}/openmrs/spa/home`);
    await signIn(page, user);
    await expect(page).toHaveURL(`${GW}/openmrs/spa/home`);
    await page.goto(`${GW}/`);
    await page.locator('#change-password').click();
    await expect(page.locator('#password-new')).toBeVisible();          // Keycloak's UPDATE_PASSWORD, no login form
    await page.fill('#password-new', 'Brand-New-Pass-77');
    await page.fill('#password-confirm', 'Brand-New-Pass-77');
    await page.locator('[type=submit]').first().click();
    await expect(page).toHaveURL(`${GW}/`);                              // back on the launcher, the unused code gone
    await expect(page.locator('#identity-line')).toBeVisible();
    // a fresh browser: the old password fails, the new one works
    const fresh = await (await browser.newContext({ ignoreHTTPSErrors: true })).newPage();
    await fresh.goto(`${GW}/openmrs/spa/home`);
    await signIn(fresh, user);                                           // signIn types Harness-Pass-123 (the old one)
    await expect(fresh.locator('#username')).toBeVisible();
    await fresh.fill('#password', 'Brand-New-Pass-77');
    await fresh.click('#kc-login');
    await expect(fresh).toHaveURL(`${GW}/openmrs/spa/home`);
  });
});
