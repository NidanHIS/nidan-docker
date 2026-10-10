import { test, expect, APIRequestContext } from '@playwright/test';
import { oeBase } from './helpers';

/**
 * Gateway side of the central-login gate (backlog KC-10, KC-56) with central login on. Runs against the real
 * stack or the gateway harness (tests/e2e-auth/harness), whose upstreams echo what they receive, so "never
 * reached the app" is visible as the absence of that echo. Roles come from the _oauth2_proxy cookie the
 * harness's stand-in oauth2-proxy reads (ok:<role,role>); on the real stack these tests need real sessions
 * and are covered by the login fixtures instead (KC-42-02), so they only run where SSO_HARNESS is set.
 */
const HARNESS = !!process.env.SSO_HARNESS;
const API = { Accept: 'application/json' };
const PAGE = { Accept: 'text/html', 'Sec-Fetch-Mode': 'navigate' };
const session = (...roles: string[]) => ({ Cookie: `_oauth2_proxy=ok:${roles.join(',')}` });

async function get(request: APIRequestContext, url: string, headers: Record<string, string>) {
  const res = await request.get(url, { headers, maxRedirects: 0 });
  const body = await res.text();
  let echoed: { path: string; headers: Record<string, string> } | null = null;
  try {
    const j = JSON.parse(body);
    if (j && typeof j.path === 'string' && j.headers) echoed = j;
  } catch { /* not the upstream echo */ }
  return { status: res.status(), headers: res.headers(), echoed };
}

test.describe('central-login gate', () => {
  test.skip(!HARNESS, 'needs the gateway harness (SSO_HARNESS=1)');

  // KC-10-02 on 443 and 8443 (the 8069 server is not behind the gateway yet: KC-02 port decision).
  for (const path of ['/openmrs/admin/index.htm', '/openmrs/module/legacyui/x', '/openmrs/moduleServlet/x',
    '/openmrs/loginServlet', '/openmrs/initialsetup', '/openmrs/ws/fhir2/R4/Patient', '/openmrs/ws/rest/v1/session',
    '/OpenELIS-Global/rest/x', '/api/OpenELIS-Global/rest/x', '/dicom-web/studies', '/wado/x', '/orthanc-container/',
    '/dhis2/', '/cis/dead-letters']) {
    test(`no session: ${path} never reaches an app, 401 where it is routed @KC-10-02 @security @sso-on-only`, async ({ request, baseURL }) => {
      const results = [];
      for (const base of [baseURL!, oeBase]) {
        let r = await get(request, base + path, API);
        // nginx's own trailing-slash redirect for a prefix location (before any access check): follow it once
        if (r.status === 301 && new URL(r.headers['location'], base).pathname === path + '/') r = await get(request, base + path + '/', API);
        results.push(r);
      }
      for (const r of results) {
        expect(r.echoed, `${path} reached the upstream`).toBeNull();
        expect([401, 404]).toContain(r.status);
      }
      expect(results.some((r) => r.status === 401), `${path} is gated on neither server`).toBe(true);
    });
  }

  test('a page without a session goes to login and keeps where it was going @KC-10-04 @security @sso-on-only', async ({ request, baseURL }) => {
    const r = await request.get(baseURL + '/openmrs/spa/home', { headers: PAGE, maxRedirects: 0 });
    expect(r.status()).toBe(302);
    const loc = new URL(r.headers()['location'], baseURL);
    expect(loc.pathname).toBe('/oauth2/start');
    expect(loc.searchParams.get('rd')).toBe('/openmrs/spa/home');
  });

  test('a cashier on a bookmarked viewer link gets not-allowed, never the viewer @KC-10-06 @security @sso-on-only', async ({ request, baseURL }) => {
    const page = await get(request, baseURL + '/ohif/viewer?StudyInstanceUIDs=1', { ...PAGE, ...session('app-odoo') });
    expect(page.status).toBe(302);
    expect(page.headers['location']).toMatch(/^\/not-allowed\.html\?app=orthanc$/);
    const api = await get(request, baseURL + '/dicom-web/studies', { ...API, ...session('app-odoo') });
    expect(api.status).toBe(403);
    expect(api.echoed).toBeNull();
  });

  test('an app response carries the refreshed session cookie @KC-10-07 @security @sso-on-only', async ({ request, baseURL }) => {
    const res = await request.get(baseURL + '/openmrs/ws/rest/v1/session', { headers: { ...API, ...session('app-openmrs') }, maxRedirects: 0 });
    expect(res.status()).toBe(200);
    const cookies = res.headersArray().filter((h) => h.name.toLowerCase() === 'set-cookie').map((h) => h.value);
    expect(cookies.some((c) => /^_oauth2_proxy=refreshed;/.test(c))).toBe(true);
  });

  test('client-sent identity headers and Authorization never reach an app @KC-56-01 @KC-56-03 @security @sso-on-only', async ({ request, baseURL }) => {
    const forged = {
      Authorization: 'Basic YWRtaW46QWRtaW4xMjM=', 'X-JWT-Assertion': 'forged', 'X-Auth-Request-User': 'admin',
      'X-Auth-Request-Email': 'a@x', 'X-Auth-Request-Groups': 'role:nidan-gateway:hospital-admin',
      'X-Auth-Request-Access-Token': 'forged', 'X-Forwarded-User': 'admin', 'X-Forwarded-Email': 'a@x',
      'X-Forwarded-Groups': 'admin', 'X-Forwarded-Access-Token': 'forged', 'X-Remote-User': 'admin',
    };
    for (const [base, path, role] of [[baseURL!, '/openmrs/ws/rest/v1/session', 'app-openmrs'],
      [oeBase, '/OpenELIS-Global/rest/x', 'app-openelis']] as const) {
      const r = await get(request, base + path, { ...API, ...session(role), ...forged });
      expect(r.status, `${path}`).toBe(200);
      for (const name of Object.keys(forged)) {
        expect(r.echoed!.headers[name.toLowerCase()] ?? '', `${path} forwarded ${name}`).toBe('');
      }
    }
  });

  test('/oauth2/auth is not reachable from outside and no response leaks a token @KC-56-04 @security @sso-on-only', async ({ request, baseURL }) => {
    for (const base of [baseURL!, oeBase]) {
      const r = await get(request, base + '/oauth2/auth', session('app-openmrs'));
      expect(r.status, base).toBe(404);
      expect(r.headers['x-auth-request-access-token']).toBeUndefined();
    }
    const app = await get(request, baseURL + '/openmrs/ws/rest/v1/session', { ...API, ...session('app-openmrs') });
    expect(app.headers['x-auth-request-access-token']).toBeUndefined();
  });
});

test.describe('Superset behind the gate', () => {
  test.skip(!HARNESS, 'needs the gateway harness (SSO_HARNESS=1)');

  // KC-03-03: Superset's own logins and token endpoints are never reachable without a gateway session.
  for (const [path, extra] of [['/superset/login/', {}], ['/superset/api/v1/security/login', {}],
    ['/superset/api/v1/security/refresh', {}], ['/superset/api/v1/security/guest_token/', {}], ['/superset/sqllab/', {}],
    ['/superset/api/v1/database/', {}], ['/superset/api/v1/chart/', { Authorization: 'Bearer forged' }],
    ['/superset/api/v1/dashboard/', { 'X-GuestToken': 'forged' }]] as const) {
    test(`no session: ${path} ${Object.keys(extra).join('') || ''} is refused at the gateway @KC-03-03 @security @sso-on-only`, async ({ request, baseURL }) => {
      const api = await get(request, baseURL + path, { ...API, ...extra });
      expect(api.status).toBe(401);
      expect(api.echoed).toBeNull();
      const page = await get(request, baseURL + path, { ...PAGE, ...extra });
      expect(page.status).toBe(302);
      expect(page.headers['location']).toMatch(/^\/oauth2\/start\?rd=/);
    });
  }
});

// KC-14-02 (gateway part): the launcher's health probes answer without a session, from a fixed upstream path,
// and pass on no version or stack details.
test.describe('launcher health probes', () => {
  test.skip(!HARNESS, 'needs the gateway harness (SSO_HARNESS=1)');

  test('health probes need no session and reveal no version @KC-14-02 @security @both-modes', async ({ request, baseURL }) => {
    const orthanc = await request.get(baseURL + '/auth-ping/orthanc', { maxRedirects: 0 });
    expect(orthanc.status()).toBe(204);
    expect(await orthanc.text()).toBe('');
    for (const [path, upstream] of [['/odoo-health/', '/web/health'], ['/cis/actuator/health', '/actuator/health']] as const) {
      const r = await get(request, baseURL + path + '?x=1', {});
      expect(r.status, path).toBe(200);
      expect(r.echoed!.path, `${path} upstream path`).toBe(upstream);
    }
    for (const path of ['/auth-ping/orthanc', '/odoo-health/', '/cis/actuator/health']) {
      const h = (await request.get(baseURL + path, { maxRedirects: 0 })).headers();
      expect(h['server'] ?? '', path).not.toMatch(/\d/);
      expect(h['x-powered-by'], path).toBeUndefined();
    }
  });
});

// KC-20-01 / KC-20-05: the Staff page and its API exist only on the Staff origin (:9443), and only for hospital admins.
test.describe('Staff page placement', () => {
  test.skip(!HARNESS, 'needs the gateway harness (SSO_HARNESS=1)');
  const STAFF = process.env.STAFF_URL ?? 'https://127.0.0.1:18445';

  test('/staff and /staff-api are 404 on the app servers @KC-20-01 @security @both-modes', async ({ request, baseURL }) => {
    for (const base of [baseURL!, oeBase]) {
      for (const path of ['/staff', '/staff/', '/staff/staff.js', '/staff-api/users', '/staff-api/events']) {
        const r = await get(request, base + path, { ...API, ...session('hospital-admin'), 'X-Nidan-Staff': '1' });
        expect(r.status, base + path).toBe(404);
        expect(r.echoed).toBeNull();
      }
    }
  });

  test('a nurse is refused the Staff page and API @KC-20-05 @security @sso-on-only', async ({ request }) => {
    const page = await get(request, STAFF + '/staff/', { ...PAGE, ...session('app-openmrs') });
    expect(page.status).toBe(302);
    expect(page.headers['location']).toMatch(/^\/not-allowed\.html\?app=/);
    const api = await get(request, STAFF + '/staff-api/events', { ...API, ...session('app-openmrs'),
      'X-Nidan-Staff': '1', 'Sec-Fetch-Site': 'same-origin' });
    expect(api.status).toBe(403);
    expect(api.echoed).toBeNull();
    const admin = await get(request, STAFF + '/staff-api/events', { ...API, ...session('hospital-admin'),
      'X-Nidan-Staff': '1', 'Sec-Fetch-Site': 'same-origin' });
    expect(admin.status).toBe(200);
  });
});

// KC-18-01 (gateway part): the launcher's language reaches Keycloak's login page as ui_locales.
test.describe('login language', () => {
  test.skip(!HARNESS, 'needs the gateway harness (SSO_HARNESS=1)');

  for (const [cookie, want] of [['nidan_locale=ne', 'ne'], ['nidan_locale=en', 'en'], ['', null],
    ['nidan_locale=xx', null], ['nidan_locale=ne%26prompt%3Dnone', null]] as const) {
    test(`cookie "${cookie}" -> ui_locales ${want ?? 'absent'} @KC-18-01 @sso-on-only`, async ({ request, baseURL }) => {
      const r = await request.get(baseURL + '/oauth2/start?rd=/openmrs/', { headers: cookie ? { Cookie: cookie } : {}, maxRedirects: 0 });
      expect(r.status()).toBe(302);
      const loc = new URL(r.headers()['location']);
      expect(loc.pathname).toMatch(/\/protocol\/openid-connect\/auth$/);
      expect(loc.searchParams.getAll('ui_locales')).toEqual(want ? [want] : []);
      expect(loc.searchParams.get('prompt')).toBeNull();
    });
  }
});

// KC-10-09: plain http on a TLS port goes to https on the same port, path and query kept, never to a foreign host.
test.describe('http on the TLS ports', () => {
  test.skip(!HARNESS, 'needs the gateway harness (SSO_HARNESS=1)');
  test('plain http on 8443 -> 301 https same port; a forged Host is not echoed @KC-10-09 @KC-56-06 @security @both-modes', async () => {
    const { request: httpRequest } = await import('node:http');
    const raw = (host?: string) => new Promise<{ status: number; location: string }>((resolve, reject) => {
      const u = new URL(oeBase);
      const req = httpRequest({ host: u.hostname, port: u.port, path: '/OpenELIS-Global/x?a=1', headers: host ? { Host: host } : {} },
        (res) => { res.resume(); resolve({ status: res.statusCode ?? 0, location: String(res.headers.location ?? '') }); });
      req.on('error', reject);
      req.end();
    });
    const ok = await raw();
    expect(ok.status).toBe(301);
    const loc = new URL(ok.location);
    expect(loc.protocol).toBe('https:');
    expect(loc.port).toBe('8443');                 // the server's own TLS port (the harness maps it to 18444)
    expect(loc.pathname + loc.search).toBe('/OpenELIS-Global/x?a=1');
    const forged = await raw('evil.example');
    expect(forged.status).toBe(301);
    expect(forged.location).not.toContain('evil.example');
  });
});

// KC-30-03 / KC-30-04 (gateway part): the image viewer only reads; Orthanc's own UI is for orthanc-admin only.
test.describe('radiology', () => {
  test.skip(!HARNESS, 'needs the gateway harness (SSO_HARNESS=1)');
  const radiologist = { ...API, ...session('app-orthanc') };

  test('viewer routes take only GET/HEAD: no upload, no delete @KC-30-03 @security @both-modes', async ({ request, baseURL }) => {
    for (const path of ['/dicom-web/studies', '/wado/x', '/ohif/viewer']) {
      const read = await request.get(baseURL + path, { headers: radiologist, maxRedirects: 0 });
      expect(read.status(), `GET ${path}`).toBe(200);
      for (const method of ['POST', 'PUT', 'DELETE', 'PATCH']) {
        const r = await request.fetch(baseURL + path, { method, headers: radiologist, data: '{}', maxRedirects: 0 });
        expect([403, 405], `${method} ${path}`).toContain(r.status());
        expect(await r.text(), `${method} ${path} reached Orthanc`).not.toContain('"path"');
      }
    }
  });

  test('Orthanc\'s own UI is for orthanc-admin only @KC-30-04 @security @sso-on-only', async ({ request, baseURL }) => {
    const denied = await get(request, baseURL + '/orthanc-container/app/explorer.html', radiologist);
    expect(denied.status).toBe(403);
    expect(denied.echoed).toBeNull();
    const ok = await get(request, baseURL + '/orthanc-container/app/explorer.html', { ...API, ...session('orthanc-admin') });
    expect(ok.status).toBe(200);
    expect(ok.echoed!.path).toBe('/app/explorer.html');
  });
});

// KC-30-06 (gateway part): behind the Orthanc gates the gateway authenticates to Orthanc; the browser's own
// Authorization never passes, and the credentials never come back in a response.
test.describe('Orthanc credentials', () => {
  test.skip(!HARNESS, 'needs the gateway harness (SSO_HARNESS=1)');
  test('the gateway, not the browser, authenticates to Orthanc @KC-30-06 @security @sso-on-only', async ({ request, baseURL }) => {
    const basic = (u: string, p: string) => 'Basic ' + Buffer.from(`${u}:${p}`).toString('base64');
    for (const [path, role, injected] of [
      ['/dicom-web/studies', 'app-orthanc', basic('nidan-viewer', 'harness-orthanc-viewer')],         // read-only account
      ['/orthanc-container/system', 'orthanc-admin', basic('harness-orthanc', 'harness-orthanc-password')]] as const) {
      const r = await get(request, baseURL + path, { ...API, ...session(role), Authorization: 'Basic ZXZpbDpldmls' });
      expect(r.status, path).toBe(200);
      expect(r.echoed!.headers.authorization, `${path}: Orthanc gets the gateway's credentials`).toBe(injected);
      expect(JSON.stringify(r.headers), `${path}: response leaks them`).not.toContain('harness-orthanc');
      expect(JSON.stringify(r.headers)).not.toContain(injected.slice(6));
    }
    const other = await get(request, baseURL + '/openmrs/ws/rest/v1/session', { ...API, ...session('app-openmrs') });
    expect(other.echoed!.headers.authorization ?? '', 'other apps get no Orthanc credentials').toBe('');
  });
});
