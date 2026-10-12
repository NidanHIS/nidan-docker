import { test, expect, APIRequestContext } from '@playwright/test';
import { execFileSync } from 'node:child_process';
import { readFileSync } from 'node:fs';

/**
 * Superset with central login on (tests/e2e-auth/harness/sso-openmrs.yml --profile superset). Runs when SUPERSET_SSO_FULL
 * is set. Calls go to superset:8088 from inside the Docker network (the gateway gates /superset/ for people).
 */
const FULL = !!process.env.SUPERSET_SSO_FULL;
const ADMIN_PW = process.env.SUPERSET_ADMIN_PASSWORD ?? 'Harness-Superset-Admin-0123456789';

function apiLogin(username: string, password: string) {
  const body = JSON.stringify({ username, password, provider: 'db', refresh: true }).replace(/'/g, "'\\''");
  return execFileSync('docker', ['exec', 'nidan-sso-openmrs-harness-gateway-1', 'sh', '-c',
    `wget -qSO /dev/null --header 'Content-Type: application/json' --post-data '${body}' ` +
    "http://superset:8088/superset/api/v1/security/login 2>&1 | grep -m1 HTTP/; true"]).toString();
}

test.describe('Superset local logins', () => {
  test.skip(!FULL, 'needs tests/e2e-auth/harness/sso-openmrs.yml --profile superset (SUPERSET_SSO_FULL=1)');

  // KC-03-09: the local admin cannot log in with "admin" or any value in the tracked .env; with central login on
  // not even with its real password (no password login at all, KC-39-04).
  test('the local admin has no password login: admin, tracked .env values, even its real password @KC-03-09 @security @sso-on-only', () => {
    const tracked = readFileSync(`${__dirname}/../../../.env`, 'utf-8').split('\n')
      .map((l) => l.match(/^\s*[A-Z0-9_]*PASSWORD[A-Z0-9_]*\s*=\s*(.+)$/)?.[1]?.trim().replace(/^['"]|['"]$/g, ''))
      .filter((v): v is string => !!v);
    const candidates = [...new Set(['admin', 'Admin123', ...tracked, ADMIN_PW])];
    expect(candidates.length).toBeGreaterThan(5);
    for (const pw of candidates) expect(apiLogin('admin', pw), `admin / ${pw.slice(0, 3)}...`).toMatch(/HTTP\/1\.1 401/);
  });
});

// KC-01-07: the app session cookies the gateway expires at login/logout (proxy/auth/expire-app-cookies.conf) are the ones
// the apps really set: same name, same path. Read from each live app on the harness (OpenMRS, Odoo, Superset).
test.describe('app cookie names', () => {
  test.skip(!FULL || !process.env.ODOO_SSO_FULL, 'needs the superset and odoo profiles (SUPERSET_SSO_FULL=1 ODOO_SSO_FULL=1)');

  test('the gateway expires exactly the cookies the apps set (name and path) @KC-01-07 @security @sso-on-only', () => {
    const conf = readFileSync(`${__dirname}/../../../proxy/auth/expire-app-cookies.conf`, 'utf-8');
    const expired = new Set([...conf.matchAll(/Set-Cookie "([^=]+)=; Path=([^;]+);/g)].map((m) => `${m[1]} ${m[2]}`));
    const live = (url: string, accept = 'text/html') => {
      const out = execFileSync('docker', ['exec', 'nidan-sso-openmrs-harness-gateway-1', 'sh', '-c',
        // curl, not busybox wget: wget drops the headers after a Location (Superset's /login/ now redirects)
        `curl -sS -o /dev/null -D - -H 'Accept: ${accept}' '${url}' | grep -i 'set-cookie'; true`]).toString();
      return [...out.matchAll(/Set-Cookie:\s*([^=]+)=[^;]*;(.*)$/gim)].map((m) => {
        const path = /Path=([^;\s]+)/i.exec(m[2])?.[1] ?? '/';
        return `${m[1].trim()} ${path}`;
      });
    };
    const apps: Array<[string, string, string?]> = [
      ['OpenMRS', 'http://openmrs-backend:8080/openmrs/ws/rest/v1/session', 'application/json'],
      ['Odoo', 'http://odoo:8069/web/login'],
      ['Superset', 'http://superset:8088/superset/login/'],
    ];
    for (const [name, url, accept] of apps) {
      const cookies = live(url, accept);
      expect(cookies.length, `${name} sets a session cookie`).toBeGreaterThan(0);
      for (const c of cookies) expect([...expired], `${name}: ${c} is expired by the gateway`).toContain(c);
    }
  });
});

const KC = process.env.KEYCLOAK_URL ?? 'http://localhost:18180/auth';
async function admin(request: APIRequestContext, path: string, method = 'GET', data?: unknown) {
  const t = await request.post(`${KC}/realms/master/protocol/openid-connect/token`, {
    form: { grant_type: 'password', client_id: 'admin-cli', username: 'admin', password: 'harness-admin-password' } });
  return request.fetch(`${KC}/admin/realms/nidan${path}`, { method, data, headers: { Authorization: `Bearer ${(await t.json()).access_token}` } });
}

// KC-03-04: a Superset API token (a JWT Superset itself signs, as its /security/login would issue) that a nurse held
// before being disabled is refused through the gateway when sent as Bearer without the gateway cookie, at once and
// still after the 6-minute window: the gate strips Authorization and needs a live gateway session.
test.describe('Superset API token after disable', () => {
  test.skip(!FULL, 'needs tests/e2e-auth/harness/sso-openmrs.yml --profile superset (SUPERSET_SSO_FULL=1)');

  test('a Superset JWT sent as Bearer with no cookie is refused, before and after the disable @KC-03-04 @security @sso-on-only', async ({ browser, request, playwright }) => {
    const username = `ss${Date.now()}`;
    const r = await admin(request, '/users', 'POST', { username, firstName: 'Su', lastName: 'Per', enabled: true,
      email: `${username}@example.org`, emailVerified: true, attributes: { gender: ['F'] }, groups: ['/staff/Nurse'] });
    const id = r.headers()['location'].split('/').pop()!;
    await admin(request, `/users/${id}/reset-password`, 'PUT', { type: 'password', value: 'Harness-Pass-123', temporary: false });
    const gw = (await (await admin(request, '/clients?clientId=nidan-gateway')).json())[0].id;
    await admin(request, `/users/${id}/role-mappings/clients/${gw}`, 'POST', [await (await admin(request, `/clients/${gw}/roles/app-superset`)).json()]);
    const sc = (await (await admin(request, '/clients?clientId=superset')).json())[0].id;   // a Superset role (her job's, in production)
    await admin(request, `/users/${id}/role-mappings/clients/${sc}`, 'POST', [await (await admin(request, `/clients/${sc}/roles/Gamma`)).json()]);

    // her first central login creates her Superset account
    const ctx = await browser.newContext({ ignoreHTTPSErrors: true });
    const page = await ctx.newPage();
    await page.goto('https://localhost/superset/login/nidan-keycloak');
    await page.fill('#username', username);
    await page.fill('#password', 'Harness-Pass-123');
    await page.click('#kc-login');
    await page.waitForURL(/\/superset\/(welcome|dashboard|superset)/, { timeout: 60_000 });
    expect((await page.request.get('https://localhost/superset/api/v1/me/')).status(), 'control: her session works').toBe(200);
    await ctx.close();

    const jwt = execFileSync('docker', ['exec', '-i', 'nidan-sso-openmrs-harness-superset-1', 'python', '-'], { input:
      'from superset.app import create_app\napp = create_app()\nwith app.app_context():\n' +
      '    from flask_jwt_extended import create_access_token\n    from superset.extensions import security_manager\n' +
      `    u = security_manager.find_user(username="${username}")\n    print(create_access_token(identity=str(u.id), fresh=True))\n` })
      .toString().trim().split('\n').pop()!;
    expect(jwt.split('.').length, 'a JWT').toBe(3);
    const anon = await playwright.request.newContext({ ignoreHTTPSErrors: true });
    const bearer = async () => (await anon.get('https://localhost/superset/api/v1/me/', { headers: { Authorization: `Bearer ${jwt}`,
      Accept: 'application/json' }, maxRedirects: 0 })).status();
    expect(await bearer(), 'a JWT alone never gets in').toBe(401);
    expect((await admin(request, `/users/${id}`, 'PUT', { enabled: false })).status()).toBe(204);
    expect(await bearer(), 'after the disable').toBe(401);
    await anon.dispose();
  });
});

// A person with app-superset at the gateway but no Superset role in the token: sign-in refused with a message, not a
// login <-> home redirect loop as a permission-less Public user (found live, 2026-10-10).
test.describe('Superset sign-in without a Superset role', () => {
  test.skip(!FULL, 'needs tests/e2e-auth/harness/sso-openmrs.yml --profile superset (SUPERSET_SSO_FULL=1)');

  test('is refused with a message, no redirect loop @KC-39-01 @sso-on-only', async ({ browser, request }) => {
    const username = `sn${Date.now()}`;
    const r = await admin(request, '/users', 'POST', { username, firstName: 'No', lastName: 'Role', enabled: true,
      email: `${username}@example.org`, emailVerified: true, attributes: { gender: ['F'] }, groups: ['/staff/Nurse'] });
    const id = r.headers()['location'].split('/').pop()!;
    await admin(request, `/users/${id}/reset-password`, 'PUT', { type: 'password', value: 'Harness-Pass-123', temporary: false });
    const gw = (await (await admin(request, '/clients?clientId=nidan-gateway')).json())[0].id;
    await admin(request, `/users/${id}/role-mappings/clients/${gw}`, 'POST', [await (await admin(request, `/clients/${gw}/roles/app-superset`)).json()]);
    const ctx = await browser.newContext({ ignoreHTTPSErrors: true });
    const page = await ctx.newPage();
    let hops = 0;
    page.on('response', (x) => { if (x.url().startsWith('https://localhost/superset/') && x.status() === 302) hops++; });
    await page.goto('https://localhost/superset/login/nidan-keycloak');
    await page.fill('#username', username);
    await page.fill('#password', 'Harness-Pass-123');
    await page.click('#kc-login');
    await page.waitForURL(/\/superset\/login\//, { timeout: 60_000 });
    await page.waitForLoadState('networkidle');
    expect(hops, 'a few redirects, not a loop').toBeLessThan(6);
    expect(new URL(page.url()).pathname).toBe('/superset/login/');
    expect((await page.request.get('https://localhost/superset/api/v1/me/')).status(), 'not signed in to Superset').toBe(401);
    await ctx.close();
  });
});

// KC-03-02 / KC-39-02 (decision 2026-10-10: no public dashboards): nothing renders without login, neither through the
// gateway nor from inside the network straight to Superset, and the Public role holds no permissions.
test.describe('no public dashboards', () => {
  test.skip(!FULL, 'needs tests/e2e-auth/harness/sso-openmrs.yml --profile superset (SUPERSET_SSO_FULL=1)');

  test('anonymous dashboard, chart and data requests are refused; Public has no permissions @KC-03-02 @KC-39-02 @security @sso-on-only', async ({ playwright }) => {
    const perms = execFileSync('docker', ['exec', 'nidan-sso-openmrs-harness-superset-1', 'python', '-c',
      "import sqlite3; c = sqlite3.connect('file:/app/superset_home/superset.db?mode=ro', uri=True); " +
      "print(c.execute(\"select count(*) from ab_permission_view_role p join ab_role r on r.id = p.role_id where r.name = 'Public'\").fetchone()[0]); " +
      "print(c.execute('select min(id) from dashboards').fetchone()[0])"]).toString().trim().split('\n');
    expect(perms[0], 'Public role permissions').toBe('0');
    const dash = perms[1];
    expect(dash, 'the harness has a dashboard to ask for').toMatch(/^\d+$/);
    const anon = await playwright.request.newContext({ ignoreHTTPSErrors: true });
    const page = await anon.get(`https://localhost/superset/dashboard/${dash}/`, { maxRedirects: 0, headers: { 'Sec-Fetch-Mode': 'navigate' } });
    expect(page.status(), 'a page: sent to login').toBe(302);
    expect(page.headers()['location']).toContain('/oauth2/start');
    for (const path of [`/superset/api/v1/dashboard/${dash}`, `/superset/api/v1/dashboard/${dash}/charts`, '/superset/api/v1/chart/', '/superset/api/v1/chart/data']) {
      expect((await anon.get(`https://localhost${path}`, { maxRedirects: 0, headers: { Accept: 'application/json' } })).status(), path).toBe(401);
    }
    await anon.dispose();
    // straight to Superset from inside the network (no gateway): Superset itself shows nothing to an anonymous caller
    const inside = execFileSync('docker', ['exec', 'nidan-sso-openmrs-harness-gateway-1', 'sh', '-c',
      `wget -qSO /dev/null --header 'Accept: application/json' http://superset:8088/superset/api/v1/dashboard/${dash} 2>&1 | grep -m1 HTTP/; true`]).toString();
    expect(inside).toMatch(/HTTP\/1\.1 (401|403|404)/);
  });
});

