import { test, expect, APIRequestContext } from '@playwright/test';
import { execFileSync } from 'node:child_process';

/**
 * Keycloak's browser-facing origin = the gateway's :9443 (decision D1; proxy/auth/keycloak-public.conf), on
 * tests/e2e-auth/harness/sso-openmrs.yml: 443 (main), 18444 (:8443), 18445 (:9443), 8069 (Odoo). Runs when
 * OPENMRS_SSO_FULL is set. 18180 is the admin API (production: SSH tunnel to 127.0.0.1:8180).
 */
const FULL = !!process.env.OPENMRS_SSO_FULL;
const KC_ADMIN = process.env.KEYCLOAK_URL ?? 'http://localhost:18180/auth';
const KC_ORIGIN = 'https://localhost:18445';
const ORIGINS = { main: 'https://localhost', oeApi: 'https://localhost:18444', odoo: 'https://localhost:8069', keycloak: KC_ORIGIN };

async function admin(request: APIRequestContext, path: string, method = 'GET', data?: unknown) {
  const t = await request.post(`${KC_ADMIN}/realms/master/protocol/openid-connect/token`, {
    form: { grant_type: 'password', client_id: 'admin-cli', username: 'admin', password: 'harness-admin-password' } });
  return request.fetch(`${KC_ADMIN}/admin/realms/nidan${path}`, { method, data, headers: { Authorization: `Bearer ${(await t.json()).access_token}` } });
}

/** HTTP status codes for GETs made from inside a container (python3 urllib; TLS not verified, Host given). */
function statusesFrom(container: string, urls: string[], host = 'localhost', method = 'GET') {
  const py = 'import ssl, sys, urllib.request, urllib.error\n' +
    'ctx = ssl._create_unverified_context()\n' +
    'for u in sys.argv[3:]:\n' +
    '    r = urllib.request.Request(u, method=sys.argv[2], headers={"Host": sys.argv[1]}, data=b"" if sys.argv[2] == "POST" else None)\n' +
    '    try: print(urllib.request.urlopen(r, context=ctx, timeout=10).status)\n' +
    '    except urllib.error.HTTPError as e: print(e.code)\n' +
    '    except Exception as e: print("ERR", type(e).__name__)\n';
  return execFileSync('docker', ['exec', container, 'python3', '-c', py, host, method, ...urls]).toString().trim().split('\n');
}

test.describe('Keycloak public origin (:9443)', () => {
  test.skip(!FULL, 'needs tests/e2e-auth/harness/sso-openmrs.yml (OPENMRS_SSO_FULL=1)');

  // KC-07-01
  test('the issuer is the Keycloak origin on :9443 @KC-07-01 @security @sso-on-only', async ({ request }) => {
    const d = await (await request.get(`${KC_ORIGIN}/auth/realms/nidan/.well-known/openid-configuration`)).json();
    expect(d.issuer).toBe(`${KC_ORIGIN}/auth/realms/nidan`);
    expect(d.authorization_endpoint).toBe(`${KC_ORIGIN}/auth/realms/nidan/protocol/openid-connect/auth`);
  });

  // KC-07-02
  test('/auth/realms/nidan/* is 404 on every other origin (443, 8443, 8069) @KC-07-02 @security @sso-on-only', async ({ request }) => {
    for (const [name, origin] of Object.entries(ORIGINS).filter(([n]) => n !== 'keycloak')) {
      for (const path of ['/auth/realms/nidan/.well-known/openid-configuration', '/auth/realms/nidan/protocol/openid-connect/auth?client_id=nidan-gateway',
        '/auth/realms/nidan/login-actions/authenticate', '/auth/resources/']) {
        expect((await request.get(`${origin}${path}`, { maxRedirects: 0 })).status(), `${name} ${path}`).toBe(404);
      }
    }
  });

  // KC-07-03: never served on any port. The gateway's path-trick guard (KC-56-08) answers 400 before routing for
  // ";" (on :9443), "//" and encoded slashes; everything else is the 404 catch-all.
  // Reported 2026-10-13 (404 after login): someone on the sign-in port is sent to the main address, same host, not a 404
  test('on the sign-in port, / and app addresses go to the main address; anything else stays 404 @KC-07-02 @security @sso-on-only', async ({ request }) => {
    const go = async (p: string) => {
      const r = await request.get(KC_ORIGIN + p, { maxRedirects: 0 });
      return `${r.status()} ${r.headers()['location'] ?? ''}`;
    };
    expect(await go('/')).toBe(`302 ${ORIGINS.main}/`);
    expect(await go('/openmrs/spa/home')).toBe(`302 ${ORIGINS.main}/openmrs/spa/home`);
    expect(await go('/openelis/')).toBe(`302 ${ORIGINS.main}/openelis/`);
    for (const p of ['/random', '/openmrsX', '/api/OpenELIS-Global/rest/x', '//evil.example/openmrs/']) expect(await go(p), p).toMatch(/^(404|400) $/);
  });

  test('master realm, admin console, path tricks and client registration are refused on every port @KC-07-03 @security @sso-on-only', async ({ request }) => {
    const paths = ['/auth/realms/master/', '/auth/realms/master/protocol/openid-connect/auth?client_id=security-admin-console',
      '/auth/admin/', '/auth/admin/master/console/', '/auth/admin/realms/nidan/users', '/auth/admin;a=b/realms',
      '/auth/%61dmin/realms', '/auth//admin/realms', '/auth/realms/nidan/clients-registrations/default',
      '/auth/realms/nidan/account/', '/auth/realms/nidan/protocol/openid-connect/registrations', '/auth/realms/nidan/'];
    for (const [name, origin] of Object.entries(ORIGINS)) {
      for (const path of paths) {
        const s = (await request.get(`${origin}${path}`, { maxRedirects: 0 })).status();
        expect([400, 404], `${name} ${path} -> ${s}`).toContain(s);
      }
    }
    // the admin API through the tunnel still works (control)
    expect((await admin(request, '/users?max=1')).status()).toBe(200);
  });

  // KC-12-07: from one client address, the 11th request in a minute to auth, token or a login form is 429. Sent from
  // a container on the harness network (its own address; the host's is exempt so the rest of the suite can log in).
  test('more than 10 requests a minute per IP on auth, token and login-actions get 429 @KC-12-07 @security @sso-on-only', () => {
    const base = 'https://gateway:9443/auth/realms/nidan';
    const auth = statusesFrom('nidan-sso-openmrs-harness-upstreams-1', Array(12).fill(`${base}/protocol/openid-connect/auth?client_id=nidan-gateway&response_type=code&scope=openid&redirect_uri=https%3A%2F%2Flocalhost%2Foauth2%2Fcallback`));
    expect(auth.slice(0, 10).every((s) => s !== '429'), `first 10 auth: ${auth}`).toBe(true);
    expect(auth.slice(10), `11th/12th auth: ${auth}`).toEqual(['429', '429']);
    const token = statusesFrom('nidan-sso-openmrs-harness-superset-1', Array(12).fill(`${base}/protocol/openid-connect/token`), 'localhost', 'POST');
    expect(token.slice(10), `token: ${token}`).toEqual(['429', '429']);
    const actions = statusesFrom('nidan-sso-openmrs-harness-odoo-1', Array(12).fill(`${base}/login-actions/authenticate`));
    expect(actions.slice(10), `login-actions: ${actions}`).toEqual(['429', '429']);
    // static theme assets are not limited
    const assets = statusesFrom('nidan-sso-openmrs-harness-upstreams-1', Array(3).fill('https://gateway:9443/auth/resources/'));
    expect(assets).not.toContain('429');
  });

  // KC-56-05: a client-typed X-Forwarded-For never reaches Keycloak's events; the gateway's view of the client does.
  test('a failed login with a forged X-Forwarded-For records the real client address @KC-56-05 @security @sso-on-only', async ({ browser, request }) => {
    const username = `xff${Date.now()}`;
    const r = await admin(request, '/users', 'POST', { username, firstName: 'X', lastName: 'Ff', enabled: true, attributes: { gender: ['M'] }, groups: ['/staff/Nurse'] });
    const id = r.headers()['location'].split('/').pop()!;
    await admin(request, `/users/${id}/reset-password`, 'PUT', { type: 'password', value: 'Harness-Pass-123', temporary: false });
    const ctx = await browser.newContext({ extraHTTPHeaders: { 'X-Forwarded-For': '6.6.6.6', 'X-Real-IP': '6.6.6.6', Forwarded: 'for=6.6.6.6' } });
    const page = await ctx.newPage();
    await page.goto('https://localhost/openmrs/ws/rest/v1/session');
    await page.fill('#username', username);
    await page.fill('#password', 'wrong-password-1');
    await page.click('#kc-login');
    await page.waitForLoadState();
    await ctx.close();
    await expect.poll(async () => ((await (await admin(request, `/events?type=LOGIN_ERROR&user=${id}`)).json()) as Array<{ ipAddress: string }>).map((e) => e.ipAddress),
      { timeout: 15_000 }).toEqual(['172.31.252.1']);   // the host as the gateway sees it (Docker Desktop bridge)
  });

  // KC-43-02: app containers reach the internal back-channel (gateway:8099) for token and certs only.
  test('from app containers the back-channel serves token/certs and 404s admin and master @KC-43-02 @security @sso-on-only', () => {
    const b = 'http://gateway:8099/auth';
    // (the OpenELIS image has no python to probe with)
    for (const c of ['nidan-sso-openmrs-harness-odoo-1', 'nidan-sso-openmrs-harness-superset-1']) {
      expect(statusesFrom(c, [`${b}/realms/nidan/protocol/openid-connect/certs`], 'gateway'), `${c} certs`).toEqual(['200']);
      const tok = statusesFrom(c, [`${b}/realms/nidan/protocol/openid-connect/token`], 'gateway', 'POST')[0];
      expect(['400', '401'], `${c} token is served (no credentials -> 400/401, not 404)`).toContain(tok);
      expect(statusesFrom(c, [`${b}/admin/realms/nidan/users`, `${b}/admin/master/console/`, `${b}/realms/master/protocol/openid-connect/token`,
        `${b}/realms/master/.well-known/openid-configuration`], 'gateway'), `${c} admin/master`).toEqual(['404', '404', '404', '404']);
    }
  });

  // KC-07-08: the login page is another origin than the apps; a script on an app page cannot read it.
  test('a script on /openmrs/ cannot read the DOM or URL of a Keycloak page it opens @KC-07-08 @security @sso-on-only', async ({ page }) => {
    await page.goto('https://localhost/not-allowed.html');   // a static page on the apps' origin
    const popupPromise = page.waitForEvent('popup');
    const handle = await page.evaluateHandle(() => window.open('/oauth2/start?rd=%2F', 'kc'));   // the real login entry
    const popup = await popupPromise;
    await popup.waitForSelector('#username');
    expect(popup.url(), 'the popup really is a Keycloak page').toMatch(new RegExp(`^${KC_ORIGIN}/auth/realms/nidan/`));
    const read = await page.evaluate((w) => {
      const out: string[] = [];
      try { out.push(`dom:${(w as Window).document.body.innerHTML.length}`); } catch { out.push('dom:blocked'); }
      try { out.push(`url:${(w as Window).location.href}`); } catch { out.push('url:blocked'); }
      return out;
    }, handle);
    expect(read).toEqual(['dom:blocked', 'url:blocked']);
  });
});

