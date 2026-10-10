import { expect, test } from '@playwright/test';
import { rawGet } from './helpers';

/**
 * Gateway side of /staff-api (backlog KC-20, D33/D56). Needs the :9443 server with an upstream that
 * echoes {path, headers} as JSON in place of Keycloak (the local nginx harness provides one).
 */
const STAFF = process.env.STAFF_URL ?? 'https://127.0.0.1:18445';
const U = '0f8e9a4c-1b2d-4e3f-8a9b-0c1d2e3f4a5b';
const G = '11111111-2222-4333-8444-555555555555';
const ADMIN_GROUP = '33333333-4444-4555-8666-777777777777'; // not a /staff/* job: not in the site's staff-groups.map
const OK = { 'X-Nidan-Staff': '1', 'Sec-Fetch-Site': 'same-origin' };

test.describe('KC-20 /staff-api', () => {
  test.skip(!process.env.STAFF_ECHO, 'set STAFF_ECHO=1 when the Keycloak upstream is the header-echo stub');

  for (const [method, path, upstream] of [
    ['GET', `/staff-api/groups/${G}/members?first=0&max=50`, `/auth/admin/realms/nidan/groups/${G}/members?first=0&max=50`],
    ['POST', '/staff-api/users', '/auth/admin/realms/nidan/users'],
    ['GET', `/staff-api/users/${U}`, `/auth/admin/realms/nidan/users/${U}`],
    ['GET', `/staff-api/users/${U}/groups`, `/auth/admin/realms/nidan/users/${U}/groups`],
    ['PUT', `/staff-api/users/${U}/groups/${G}`, `/auth/admin/realms/nidan/users/${U}/groups/${G}`],
    ['DELETE', `/staff-api/users/${U}/groups/${G}`, `/auth/admin/realms/nidan/users/${U}/groups/${G}`],
    ['GET', '/staff-api/events?type=LOGIN_ERROR', '/auth/admin/realms/nidan/events?type=LOGIN_ERROR'],
    ['GET', '/staff-api/admin-events', '/auth/admin/realms/nidan/admin-events'],
  ] as const) {
    test(`${method} ${path.split('?')[0]} reaches only its Keycloak path, no client credentials @KC-20-03 @KC-20-06 @security`, async ({ request }) => {
      const res = await request.fetch(STAFF + path, { method, headers: { ...OK, Authorization: 'Bearer stolen', Cookie: 'x=1' } });
      expect(res.status()).toBe(200);
      const echo = await res.json();
      expect(echo.path).toBe(upstream);
      expect(echo.headers.authorization).toBeUndefined();
      expect(echo.headers.cookie).toBeUndefined();
    });
  }

  for (const [method, path] of [
    ['GET', '/staff-api/users'], ['GET', '/staff-api/users?search=a'], ['GET', '/staff-api/clients'],
    ['GET', `/staff-api/users/${U}/credentials`], ['DELETE', `/staff-api/users/${U}/credentials/${U}`],
    ['GET', `/staff-api/users/${U}/role-mappings`], ['POST', `/staff-api/users/${U}/impersonation`],
    ['DELETE', `/staff-api/users/${U}`], ['GET', '/staff-api/users/not-a-uuid'], ['POST', '/staff-api/groups'],
  ] as const) {
    test(`${method} ${path} -> 403 @KC-20-03 @security`, async ({ request }) => {
      expect((await request.fetch(STAFF + path, { method, headers: OK })).status()).toBe(403);
    });
  }

  // KC-09-07: Keycloak's fine-grained permissions cannot split credential deletion off "manage members", so the
  // gateway is the layer that refuses it (scripts/keycloak-admin-permissions.py).
  for (const method of ['GET', 'DELETE', 'PUT', 'POST']) {
    test(`${method} a user's credentials through /staff-api -> 403 @KC-09-07 @security`, async ({ request }) => {
      const res = await request.fetch(`${STAFF}/staff-api/users/${U}/credentials/${U}`, { method, headers: OK });
      expect(res.status()).toBe(403);
    });
  }

  // KC-21-09 / KC-09-04 (gateway part): only this site's /staff/* job groups are routable at all.
  for (const [method, path] of [
    ['PUT', `/staff-api/users/${U}/groups/${ADMIN_GROUP}`], ['DELETE', `/staff-api/users/${U}/groups/${ADMIN_GROUP}`],
    ['GET', `/staff-api/groups/${ADMIN_GROUP}/members`],
  ] as const) {
    test(`${method} ${path} (not a staff job) -> 403 @KC-21-09 @security`, async ({ request }) => {
      const res = await request.fetch(STAFF + path, { method, headers: OK });
      expect(res.status()).toBe(403);
    });
  }

  for (const [label, headers] of [
    ['no X-Nidan-Staff', { 'Sec-Fetch-Site': 'same-origin' }],
    ['cross-site fetch', { 'X-Nidan-Staff': '1', 'Sec-Fetch-Site': 'cross-site' }],
    ['same-site (another gateway port)', { 'X-Nidan-Staff': '1', 'Sec-Fetch-Site': 'same-site' }],
    ['foreign Origin', { ...OK, Origin: 'https://evil.example' }],
    ['Origin of another gateway port', { ...OK, Origin: 'https://127.0.0.1:18443' }],
  ] as const) {
    for (const method of ['GET', 'POST', 'PUT', 'DELETE', 'OPTIONS']) {
      test(`${method} with ${label} -> 403 @KC-20-04 @security`, async ({ request }) => {
        const res = await request.fetch(`${STAFF}/staff-api/users/${U}`, { method, headers });
        expect(res.status()).toBe(403);
        expect(res.headers()['access-control-allow-origin']).toBeUndefined();
      });
    }
  }

  test('the Staff origin itself passes @KC-20-04 @security', async ({ request }) => {
    const res = await request.post(`${STAFF}/staff-api/users`, { headers: { ...OK, Origin: new URL(STAFF).origin } });
    expect(res.status()).toBe(200);
  });

  for (const p of [`/staff-api/users/${U}%2fcredentials`, `/staff-api/users/${U};x=1`, '/staff-api/users/%2e%2e/clients',
    `//staff-api/users/${U}`, `/staff-api/users/${U}/..;/clients`]) {
    test(`${p} -> 400 @KC-20-02 @security`, async () => {
      expect(await rawGet(STAFF, p)).toBe(400);
    });
  }
});
