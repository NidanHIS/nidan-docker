import { test, expect, APIRequestContext } from '@playwright/test';
import { createHash, createHmac, randomBytes } from 'node:crypto';
import { readFileSync, readdirSync } from 'node:fs';
import { join } from 'node:path';
import { parse as parseYaml } from 'yaml';

/**
 * Realm behaviour against a live Keycloak (backlog KC-54, KC-55, KC-01-03): the realm harness
 * (tests/e2e-auth/harness/keycloak.yml, after an import) or a site's Keycloak through the operator's tunnel.
 * Runs only when KEYCLOAK_URL is set (e.g. http://127.0.0.1:18180/auth). The admin account and the client
 * secret are the harness's throwaway values unless overridden. Users it creates get unique names per run and
 * are not removed (the harness Keycloak keeps nothing).
 */
const KC = process.env.KEYCLOAK_URL ?? '';
// Login pages as a browser reaches them: the gateway's :9443 on the full harness (D1); the admin API, token and
// account endpoints stay on KC. Unset = KC (the standalone realm harness serves both on one address).
const KC_PUBLIC = process.env.KEYCLOAK_PUBLIC_URL ?? KC;
const ADMIN_USER = process.env.KEYCLOAK_ADMIN_USER ?? 'admin';
const ADMIN_PASSWORD = process.env.KEYCLOAK_ADMIN_PASSWORD ?? 'harness-admin-password';
const SECRET = process.env.KC_HARNESS_CLIENT_SECRET ?? 'harness-only-not-a-real-secret-0123456789';
const MACHINE = ['cis', 'odoo-sync', 'portal-sync'];
const SYNC_USER = process.env.NIDAN_SYNC_USERNAME ?? 'nidan-sync';

async function adminToken(request: APIRequestContext) {
  const r = await request.post(`${KC}/realms/master/protocol/openid-connect/token`, {
    form: { grant_type: 'password', client_id: 'admin-cli', username: ADMIN_USER, password: ADMIN_PASSWORD } });
  expect(r.status(), 'admin login').toBe(200);
  return (await r.json()).access_token as string;
}

async function admin(request: APIRequestContext, path: string, init: { method?: string; data?: unknown } = {}) {
  return request.fetch(`${KC}/admin/realms/nidan${path}`, {
    method: init.method ?? 'GET', data: init.data,
    headers: { Authorization: `Bearer ${await adminToken(request)}` } });
}

function claims(jwt: string): Record<string, any> {
  return JSON.parse(Buffer.from(jwt.split('.')[1], 'base64url').toString());
}

const DOMAIN = process.env.GATEWAY_DOMAIN ?? 'nidan.example';

/** A user with a known password, in the given top-level groups (e.g. hospital-admin). Returns its username. */
async function makeUser(request: APIRequestContext, groups: string[]) {
  const username = `rt${Date.now()}${Math.floor(Math.random() * 1000)}`;
  const created = await admin(request, '/users', { method: 'POST', data: { username, firstName: 'Sita', lastName: 'Rai',
    email: `${username}@nidan.example`, emailVerified: true, enabled: true, attributes: { gender: ['F'] },
    groups: groups.map((g) => '/' + g) } });
  expect(created.status(), 'create test user').toBe(201);
  const id = created.headers()['location'].split('/').pop()!;
  const pw = await admin(request, `/users/${id}/reset-password`, { method: 'PUT',
    data: { type: 'password', value: 'Harness-Pass-123', temporary: false } });
  expect(pw.status(), 'set password').toBe(204);
  return username;
}

// ---- Keycloak login without a browser -------------------------------------------------------------------------
// Keycloak marks its login cookies Secure; the harness speaks plain http, so a small jar hands them back.
// OTP: Keycloak's totpSecret form value is the raw HMAC-SHA1 key (6 digits, 30 s). A code is accepted once, so a
// user's next code waits for the next 30-second step when the current one was already used.
const otpSecrets = new Map<string, string>();
const usedStep = new Map<string, number>();
const totpFor = (secret: string, step: number) => {
  const msg = Buffer.alloc(8); msg.writeBigUInt64BE(BigInt(step));
  const d = createHmac('sha1', Buffer.from(secret)).update(msg).digest(); const o = d[d.length - 1] & 15;
  return String((d.readUInt32BE(o) & 0x7fffffff) % 1000000).padStart(6, '0');
};
async function freshOtp(username: string) {
  let step = Math.floor(Date.now() / 30000);
  if ((usedStep.get(username) ?? -1) >= step) {
    await new Promise((r) => setTimeout(r, (step + 1) * 30000 - Date.now() + 300));
    step += 1;
  }
  usedStep.set(username, step);
  return totpFor(otpSecrets.get(username)!, step);
}
type Page = { status: number; location: string; body: string };
const formAction = (body: string) => body.match(/<form[^>]*action="([^"]+)"/)![1].replace(/&amp;/g, '&');
const gotCode = (p: Page) => /[?&]code=/.test(p.location);
const totpSecretOf = (body: string) => body.match(/name="totpSecret" value="([^"]+)"/)?.[1];

class KcSession {
  private jar: Record<string, string> = {};
  constructor(private request: APIRequestContext) {}
  private keep(r: { headersArray(): { name: string; value: string }[] }) {
    for (const h of r.headersArray()) {
      if (h.name.toLowerCase() === 'set-cookie') { const [k, v] = h.value.split(';')[0].split(/=(.*)/s); this.jar[k] = v; }
    }
  }
  /** One request, then Keycloak's own redirects (never the client's redirect_uri). */
  async go(method: 'GET' | 'POST', url: string, form?: Record<string, string>): Promise<Page> {
    const cookie = () => Object.entries(this.jar).map(([k, v]) => `${k}=${v}`).join('; ');
    let r = await this.request.fetch(url, { method, form, headers: { Cookie: cookie() }, maxRedirects: 0 });
    this.keep(r);
    const hops: string[] = [];
    while ([302, 303].includes(r.status()) && [KC, KC_PUBLIC].some((o) => (r.headers()['location'] ?? '').startsWith(o)) && !/[?&]code=/.test(r.headers()['location'])) {
      hops.push(r.headers()['location'].split('?')[0] + ' ' + (r.headers()['location'].match(/execution=[^&]+/)?.[0] ?? ''));
      if (hops.length > 20) throw new Error('Keycloak redirect loop:\n' + hops.slice(-6).join('\n'));   // fail fast, not a 30 min hang
      r = await this.request.get(r.headers()['location'], { headers: { Cookie: cookie() }, maxRedirects: 0 });
      this.keep(r);
    }
    return { status: r.status(), location: r.headers()['location'] ?? '', body: await r.text() };
  }
  /** Login page + password; returns whatever comes next (a code redirect, the OTP form, or OTP enrolment). */
  async password(clientId: string, redirectUri: string, username: string, challenge: string) {
    const q = new URLSearchParams({ client_id: clientId, redirect_uri: redirectUri, response_type: 'code', scope: 'openid',
      state: 'harness', code_challenge: challenge, code_challenge_method: 'S256' });
    const page = await this.go('GET', `${KC_PUBLIC}/realms/nidan/protocol/openid-connect/auth?${q}`);
    expect(page.status, `${clientId} login page`).toBe(200);
    return this.go('POST', formAction(page.body), { username, password: 'Harness-Pass-123' });
  }
  /** Finish a login that stopped at OTP enrolment or the OTP form, as the user would. */
  async otp(username: string, p: Page) {
    const secret = totpSecretOf(p.body);
    if (secret) {
      otpSecrets.set(username, secret);
      return this.go('POST', formAction(p.body), { totp: await freshOtp(username), totpSecret: secret, userLabel: 'phone' });
    }
    if (/name="otp"/.test(p.body)) return this.go('POST', formAction(p.body), { otp: await freshOtp(username) });
    return p;
  }
}

/** Authorization code + PKCE through Keycloak's own login form (OTP handled), without a browser. */
async function userToken(request: APIRequestContext, clientId: string, redirectUri: string, username: string) {
  const verifier = randomBytes(32).toString('base64url');
  const challenge = createHash('sha256').update(verifier).digest('base64url');
  const kc = new KcSession(request);
  const done = await kc.otp(username, await kc.password(clientId, redirectUri, username, challenge));
  expect(gotCode(done), `${clientId} login`).toBe(true);
  const back = new URL(done.location);
  expect(back.origin + back.pathname, 'redirect_uri').toBe(redirectUri);
  const form: Record<string, string> = { grant_type: 'authorization_code', code: back.searchParams.get('code')!,
    redirect_uri: redirectUri, client_id: clientId, code_verifier: verifier };
  if (clientId !== 'account-console') form.client_secret = SECRET;
  const tok = await request.post(`${KC}/realms/nidan/protocol/openid-connect/token`, { form });
  expect(tok.status(), `${clientId} token`).toBe(200);
  const raw = (await tok.json()).access_token as string;
  return Object.assign(claims(raw), { __raw: raw });
}

test.describe('realm against a live Keycloak', () => {
  test.skip(!KC, 'set KEYCLOAK_URL (realm harness or a site tunnel)');

  test('no self-registration, no reset, no username edits @KC-55-02 @security', async ({ request }) => {
    const realm = await (await admin(request, '')).json();
    expect(realm.registrationAllowed).toBe(false);
    expect(realm.resetPasswordAllowed).toBe(false);
    expect(realm.editUsernameAllowed).toBe(false);
    const reset = await request.get(`${KC}/realms/nidan/login-actions/reset-credentials?client_id=nidan-gateway`, { maxRedirects: 0 });
    expect(reset.status()).toBeGreaterThanOrEqual(400);
    expect(await reset.text()).not.toMatch(/name="username"/);
    const reg = await request.get(`${KC}/realms/nidan/protocol/openid-connect/registrations?client_id=nidan-gateway&response_type=code&scope=openid`, { maxRedirects: 0 });
    expect(await reg.text()).not.toMatch(/name="password-confirm"/);
  });

  test('the password grant is refused on every client @KC-54-03 @security', async ({ request }) => {
    const clients: Array<{ clientId: string; publicClient: boolean; bearerOnly?: boolean }> = await (await admin(request, '/clients')).json();
    expect(clients.length).toBeGreaterThan(5);
    for (const c of clients) {
      const form: Record<string, string> = { grant_type: 'password', client_id: c.clientId, username: 'nobody', password: 'x' };
      if (!c.publicClient) form.client_secret = SECRET;
      const r = await request.post(`${KC}/realms/nidan/protocol/openid-connect/token`, { form });
      const body = await r.json();
      expect(r.status(), c.clientId).not.toBe(200);
      // Keycloak answers unauthorized_client when the grant is off; invalid_client for clients whose secret we do
      // not hold (Keycloak's own built-ins) - both mean no password login through this client.
      expect(['unauthorized_client', 'invalid_client'], `${c.clientId}: ${JSON.stringify(body)}`).toContain(body.error);
      if (MACHINE.includes(c.clientId) || !c.clientId.match(/^(account|admin-cli|broker|realm-management|security-admin-console)/)) {
        expect(body.error, c.clientId).toBe('unauthorized_client');
      }
    }
  });

  test('reserved usernames cannot be created, an ordinary one can @KC-55-03 @security', async ({ request }) => {
    for (const username of ['admin', 'nidan', 'nidan-sync', 'daemon', '__system__', 'public', 'default', 'cis-worker',
      'ois-1', 'odoo-sync', 'portal-sync', 'nidan-odoo', 'nidan-portal', 'dead-letter-admin', '12345-6', '0f8e9a4c-1b2d-4e3f-8a9b-0c1d2e3f4a5b']) {
      const r = await admin(request, '/users', { method: 'POST',
        data: { username, firstName: 'T', lastName: 'T', enabled: true, attributes: { gender: ['U'] } } });
      expect(r.status(), username).toBe(400);
    }
    const ok = await admin(request, '/users', { method: 'POST',
      data: { username: `ordinary${Date.now()}`, firstName: 'T', lastName: 'T', enabled: true, attributes: { gender: ['U'] } } });
    expect(ok.status()).toBe(201);
  });

  test('only cis, odoo-sync and portal-sync get service tokens, and theirs match the contract @KC-01-03 @KC-58-02 @security', async ({ request }) => {
    const clients: Array<{ clientId: string; publicClient: boolean }> = await (await admin(request, '/clients')).json();
    for (const c of clients.filter((x) => !x.publicClient)) {
      const r = await request.post(`${KC}/realms/nidan/protocol/openid-connect/token`, {
        form: { grant_type: 'client_credentials', client_id: c.clientId, client_secret: SECRET } });
      if (!MACHINE.includes(c.clientId)) {
        expect(r.status(), `${c.clientId} must not get a service token`).not.toBe(200);
        continue;
      }
      expect(r.status(), c.clientId).toBe(200);
      const t = claims((await r.json()).access_token);
      for (const k of ['iss', 'sub', 'aud', 'azp', 'exp', 'iat', 'typ']) expect(t[k], `${c.clientId}.${k}`).toBeDefined();
      expect(t.iss).toMatch(/\/auth\/realms\/nidan$/);
      expect(t.typ).toBe('Bearer');
      expect(t.azp).toBe(c.clientId);
      // each machine client has its own least-privilege OpenMRS account (nidancore ServiceAccounts, KC-32-05)
      expect(t.openmrs_user).toBe({ cis: SYNC_USER, 'odoo-sync': 'nidan-odoo', 'portal-sync': 'nidan-portal' }[c.clientId]);
      expect([t.aud].flat(), `${c.clientId}.aud`).toEqual(['openmrs']);
      expect(t.realm_access?.roles ?? [], `${c.clientId} carries realm roles`).toEqual([]);
    }
  });

  test('the gateway token is for the gateway only and carries only gateway roles @KC-54-04 @KC-01-03 @security', async ({ playwright }) => {
    const request = await playwright.request.newContext();
    const username = await makeUser(request, ['hospital-admin', 'orthanc-admin']);
    const t = await userToken(request, 'nidan-gateway', `https://${DOMAIN}/oauth2/callback`, username);
    expect([t.aud].flat()).toEqual(['nidan-gateway']);
    expect(t.azp).toBe('nidan-gateway');
    expect(t.preferred_username).toBe(username);
    expect(Object.keys(t.resource_access ?? {})).toEqual(['nidan-gateway']);
    expect([...t.resource_access['nidan-gateway'].roles].sort()).toEqual(['app-orthanc', 'hospital-admin', 'orthanc-admin']);
    expect(t.realm_access?.roles ?? []).toEqual([]);
    expect(t.openmrs_user).toBeUndefined();
  });

  test('the OpenMRS login token matches the contract, no roles claim means no roles @KC-01-03 @KC-58-07 @security', async ({ playwright }) => {
    const request = await playwright.request.newContext();
    const username = await makeUser(request, ['staff/Nurse']);
    const t = await userToken(request, 'openmrs', `https://${DOMAIN}/openmrs/oauth2login`, username);
    expect(t.typ).toBe('Bearer');
    expect(t.preferred_username).toBe(username);
    expect(t.given_name).toBe('Sita');
    expect(t.family_name).toBe('Rai');
    expect(t.gender).toBe('F');
    // Keycloak omits an empty roles claim; oauth2login reads a missing claim as no roles (UserInfoTest), so the user
    // ends with zero OpenMRS roles (KC-58-07). Present means a list.
    expect(t.roles === undefined || Array.isArray(t.roles), 'roles is absent or a list').toBe(true);
    expect(t.openmrs_user).toBeUndefined();
    expect(Object.keys(t.resource_access ?? {}).filter((k) => k !== 'openmrs')).toEqual([]);
  });

  test('staff cannot change their own places, names, email, gender or add attributes @KC-55-01 @security', async ({ playwright }) => {
    const request = await playwright.request.newContext();
    const username = await makeUser(request, ['staff/Cashier']);
    const users = await (await admin(request, `/users?username=${username}&exact=true`)).json();
    const id = users[0].id;
    await admin(request, `/users/${id}`, { method: 'PUT', data: { ...users[0], attributes: { gender: ['F'], nidan_shops: ['Main Pharmacy'] } } });
    const before = await (await admin(request, `/users/${id}`)).json();
    const t = await userToken(request, 'account-console', `${KC_PUBLIC}/realms/nidan/account/`, username);
    const auth = { Authorization: `Bearer ${t.__raw}`, 'Content-Type': 'application/json' };
    for (const change of [
      { attributes: { ...before.attributes, nidan_shops: ['Every Shop'] } },
      { attributes: { ...before.attributes, nidan_locations: ['ICU'] } },
      { attributes: { ...before.attributes, nidan_lab_units: ['Haematology'] } },
      { attributes: { ...before.attributes, gender: ['M'] } },
      { attributes: { ...before.attributes, made_up_attribute: ['x'] } },
      { firstName: 'Changed' }, { lastName: 'Changed' }, { email: 'other@nidan.example' },
    ]) {
      const body = { username, firstName: before.firstName, lastName: before.lastName, email: before.email, ...change };
      const r = await request.post(`${KC}/realms/nidan/account/`, { headers: auth, data: body });
      // Declared fields are admin-edit only: 400 read-only. An undeclared attribute is accepted and dropped
      // (unmanaged attributes disabled), which the unchanged user below proves.
      // Without manage-account (default role is only view-profile) every write is 403; were it ever granted again,
      // the declared fields would still be read-only (400). Either way nothing changes, which is checked below.
      expect([400, 401, 403], JSON.stringify(change)).toContain(r.status());
    }
    const after = await (await admin(request, `/users/${id}`)).json();
    expect(after.firstName).toBe(before.firstName);
    expect(after.lastName).toBe(before.lastName);
    expect(after.email).toBe(before.email);
    expect(after.attributes).toEqual(before.attributes);
  });

  // KC-09 (needs scripts/keycloak-admin-permissions.py --apply on the realm): what a hospital admin's own token can
  // do through Keycloak's admin API. The gateway's /staff-api allow-list narrows it further (staff-api.spec.ts).
  test('hospital admins manage staff/* people only, never admins, roles or other groups @KC-09-01 @KC-09-02 @KC-09-03 @KC-09-04 @KC-09-05 @KC-09-06 @KC-21-04 @security', async ({ playwright }) => {
    test.skip(!process.env.KC_ADMIN_PERMISSIONS, 'set KC_ADMIN_PERMISSIONS=1 after keycloak-admin-permissions.py --apply');
    const request = await playwright.request.newContext();
    const groups: Array<{ id: string; path: string }> = await (await admin(request, '/groups')).json();
    const gid = (path: string) => groups.find((g) => g.path === path)!.id;
    const kids: Array<{ id: string; name: string }> = await (await admin(request, `/groups/${gid('/staff')}/children`)).json();
    const job = (name: string) => kids.find((g) => g.name === name)!.id;
    const idOf = async (username: string) => (await (await admin(request, `/users?username=${username}&exact=true`)).json())[0].id;
    const hadmin1 = await makeUser(request, ['hospital-admin']);
    const hadmin2 = await idOf(await makeUser(request, ['hospital-admin']));
    const nurse = await idOf(await makeUser(request, ['staff/Nurse']));
    const sup = await idOf(await makeUser(request, ['app-superusers']));
    const me = await idOf(hadmin1);
    const t = await userToken(request, 'nidan-gateway', `https://${DOMAIN}/oauth2/callback`, hadmin1);
    const as = (method: string, path: string, data?: unknown) => request.fetch(`${KC}/admin/realms/nidan${path}`,
      { method, data, headers: { Authorization: `Bearer ${t.__raw}` } }).then((r) => r.status());
    const N = job('Nurse'), C = job('Cashier');
    const tmp = { type: 'password', value: 'Tmp-Pass-12345', temporary: true };
    const cases: Array<[string, string, string, unknown, number]> = [
      ['list Nurse members', 'GET', `/groups/${N}/members`, undefined, 200],
      ['list every user', 'GET', '/users', undefined, 403],
      ['create into /staff/Nurse', 'POST', '/users', { username: `new${Date.now()}`, firstName: 'N', lastName: 'U', enabled: true,
        attributes: { gender: ['M'] }, groups: ['/staff/Nurse'] }, 201],
      ['reset a nurse', 'PUT', `/users/${nurse}/reset-password`, tmp, 204],
      ['disable a nurse', 'PUT', `/users/${nurse}`, { enabled: false }, 204],
      ['enable a nurse', 'PUT', `/users/${nurse}`, { enabled: true }, 204],
      ['move a nurse to Cashier (add)', 'PUT', `/users/${nurse}/groups/${C}`, undefined, 204],
      ['move a nurse to Cashier (remove)', 'DELETE', `/users/${nurse}/groups/${N}`, undefined, 204],
      ['reset another hospital admin', 'PUT', `/users/${hadmin2}/reset-password`, tmp, 403],
      ['disable another hospital admin', 'PUT', `/users/${hadmin2}`, { enabled: false }, 403],
      ['add a nurse to /hospital-admin', 'PUT', `/users/${nurse}/groups/${gid('/hospital-admin')}`, undefined, 403],
      ['add a nurse to /app-superusers', 'PUT', `/users/${nurse}/groups/${gid('/app-superusers')}`, undefined, 403],
      ['add another admin to Nurse', 'PUT', `/users/${hadmin2}/groups/${N}`, undefined, 403],
      ['add a superuser to Nurse', 'PUT', `/users/${sup}/groups/${N}`, undefined, 403],
      ['add herself to Nurse', 'PUT', `/users/${me}/groups/${N}`, undefined, 403],
      ['map a realm role', 'POST', `/users/${nurse}/role-mappings/realm`, [{ name: 'offline_access' }], 403],
      ['impersonate a nurse', 'POST', `/users/${nurse}/impersonation`, undefined, 403],
    ];
    for (const [what, method, path, data, want] of cases) expect(await as(method, path, data), what).toBe(want);
  });

  // Second step OPTIONAL FOR EVERYONE (user decision 2026-10-12, replaces "admin groups always need OTP"): nobody is
  // made to enrol; a person who has set one up must give it on every sign-in; the account API still cannot remove it.
  test('the second step is optional for everyone, and required once a person sets one up @KC-09-09 @KC-09-10 @KC-57-03 @security', async ({ playwright }) => {
    test.setTimeout(180_000);   // fresh OTP codes may wait for the next 30-second step
    const request = await playwright.request.newContext();
    const GW = `https://${DOMAIN}/oauth2/callback`;
    const CH = createHash('sha256').update('harness-verifier-0123456789-0123456789-xyz').digest('base64url');

    for (const group of ['staff/Nurse', 'hospital-admin', 'cis-admin', 'orthanc-admin', 'app-superusers']) {
      const r = await new KcSession(request).password('nidan-gateway', GW, await makeUser(request, [group]), CH);
      expect(gotCode(r), `${group}: password alone signs in (nothing set up)`).toBe(true);
    }

    // a hospital admin who sets up an authenticator app (here started by the operator; the launcher's
    // "Two-step sign-in" starts the same Keycloak step)
    const adm = await makeUser(request, ['hospital-admin']);
    const id = (await (await admin(request, `/users?username=${adm}&exact=true`)).json())[0].id;
    const user = await (await admin(request, `/users/${id}`)).json();
    await admin(request, `/users/${id}`, { method: 'PUT', data: { ...user, requiredActions: ['CONFIGURE_TOTP'] } });
    let kc = new KcSession(request);
    expect(gotCode(await kc.otp(adm, await kc.password('nidan-gateway', GW, adm, CH))), 'set-up completes the login').toBe(true);
    kc = new KcSession(request);
    const next = await kc.password('nidan-gateway', GW, adm, CH);
    expect(gotCode(next), 'set up: no code after the password alone').toBe(false);
    expect(next.body).toMatch(/name="otp"/);
    const wrong = await kc.go('POST', formAction(next.body), { otp: '000000' });
    expect(gotCode(wrong)).toBe(false);
    expect(gotCode(await kc.otp(adm, wrong)), 'the right code completes it').toBe(true);

    // KC-09-10: her own account-API session cannot remove it (the default role has no manage-account)
    const otpId = ((await (await admin(request, `/users/${id}/credentials`)).json()) as Array<{ id: string; type: string }>)
      .find((c) => c.type === 'otp')!.id;
    const acct = await userToken(request, 'account-console', `${KC_PUBLIC}/realms/nidan/account/`, adm);
    const del = await request.delete(`${KC}/realms/nidan/account/credentials/${otpId}`,
      { headers: { Authorization: `Bearer ${acct.__raw}`, Accept: 'application/json' } });
    expect([401, 403], 'account API delete of her own OTP').toContain(del.status());
    const still = (await (await admin(request, `/users/${id}/credentials`)).json()) as Array<{ type: string }>;
    expect(still.some((c) => c.type === 'otp'), 'her OTP is still there').toBe(true);

    // the operator removes it (lost phone): back to password only, as for anyone who never set one up
    expect((await admin(request, `/users/${id}/credentials/${otpId}`, { method: 'DELETE' })).status()).toBe(204);
    expect(gotCode(await new KcSession(request).password('nidan-gateway', GW, adm, CH)), 'removed: password alone again').toBe(true);
  });

  // KC-17: the login page as a person sees it.
  const loginUrl = (locale?: string) => `${KC_PUBLIC}/realms/nidan/protocol/openid-connect/auth?` + new URLSearchParams({ client_id: 'nidan-gateway',
    redirect_uri: `https://${DOMAIN}/oauth2/callback`, response_type: 'code', scope: 'openid',
    code_challenge: 'E9Melhoa2OwvFrEMTJguCHaoeK1t8URWbuGJSstw-cM', code_challenge_method: 'S256', ...(locale ? { ui_locales: locale } : {}) });

  test('login page uses nidan.css and hides the native language picker @KC-17-01', async ({ page }) => {
    const css: string[] = [];
    page.on('response', (r) => { if (r.url().endsWith('.css')) css.push(`${r.status()} ${r.url()}`); });
    await page.goto(loginUrl());
    await expect(page.locator('#username')).toBeVisible();
    expect(css.some((c) => /^200 .*\/login\/nidan\/css\/nidan\.css$/.test(c)), css.join('\n')).toBe(true);
    await expect(page.locator('#login-select-toggle')).toBeHidden();
  });

  async function failedLoginText(page: import('@playwright/test').Page, username: string, password: string, locale?: string) {
    await page.context().clearCookies();
    await page.goto(loginUrl(locale));
    await page.fill('#username', username);
    await page.fill('#password', password);
    await page.click('#kc-login');
    await expect(page.locator('#username')).toBeVisible();
    return (await page.locator('body').innerText()).replaceAll(username, '<user>').replace(/\s+/g, ' ').trim();
  }

  test('a failed login reads the same whether the account exists, is disabled or is locked @KC-17-02 @security', async ({ page, playwright }) => {
    test.setTimeout(120_000);
    const request = await playwright.request.newContext();
    const real = await makeUser(request, ['staff/Nurse']);
    const disabled = await makeUser(request, ['staff/Nurse']);
    const locked = await makeUser(request, ['staff/Nurse']);
    const id = async (u: string) => (await (await admin(request, `/users?username=${u}&exact=true`)).json())[0].id;
    expect((await admin(request, `/users/${await id(disabled)}`, { method: 'PUT', data: { enabled: false } })).status()).toBe(204);
    for (let i = 0; i < 6; i++) { await failedLoginText(page, locked, `Wrong-${i}`); await page.waitForTimeout(1100); }

    const reference = await failedLoginText(page, `nobody${Date.now()}`, 'Wrong-Pass-1');
    expect(reference).toContain('Wrong username or password');
    for (const [what, user, pw] of [['existing account, wrong password', real, 'Wrong-Pass-1'],
      ['disabled account, wrong password', disabled, 'Wrong-Pass-1'],
      ['locked account, wrong password', locked, 'Wrong-Pass-1'],
      ['locked account, right password', locked, 'Harness-Pass-123']] as const) {
      expect(await failedLoginText(page, user, pw), what).toBe(reference);
    }
  });

  // Spec 8.3: crisis messages show the default language plus English, so a Nepali-default site still tells an
  // English-reading locum what went wrong; and they stay the same text for every kind of failure (KC-17-02).
  test('in Nepali, a failed login shows the Nepali and the English message, the same for every failure @KC-17-04 @security', async ({ page, playwright }) => {
    const request = await playwright.request.newContext();
    const disabled = await makeUser(request, ['staff/Nurse']);
    const id = (await (await admin(request, `/users?username=${disabled}&exact=true`)).json())[0].id;
    await admin(request, `/users/${id}`, { method: 'PUT', data: { enabled: false } });
    const unknown = await failedLoginText(page, `nobody${Date.now()}`, 'Wrong-Pass-1', 'ne');
    expect(unknown).toContain('प्रयोगकर्ता नाम वा पासवर्ड मिलेन');
    expect(unknown).toContain('Wrong username or password');
    expect(await failedLoginText(page, disabled, 'Wrong-Pass-1', 'ne'), 'disabled account').toBe(unknown);
  });

  // KC-17-05: the login page in English and in Nepali is the same page: same layout (pixel baselines would differ by
  // platform, so the two languages are compared with each other), no horizontal overflow from the longer Nepali text,
  // the Nidan theme in both. Screenshots are attached to the report for a person to look at.
  test('login page: English and Nepali render the same layout, nothing overflows @KC-17-05 @security', async ({ page }, testInfo) => {
    await page.setViewportSize({ width: 390, height: 844 });     // phone width: where long words break layouts first
    const layout = async (locale: string) => {
      await page.context().clearCookies();
      await page.goto(loginUrl(locale));
      await expect(page.locator('#username')).toBeVisible();
      await testInfo.attach(`login-${locale}.png`, { body: await page.screenshot({ fullPage: true }), contentType: 'image/png' });
      const box = async (sel: string) => { const b = await page.locator(sel).first().boundingBox(); expect(b, sel).not.toBeNull(); return b!; };
      return {
        lang: await page.locator('html').getAttribute('lang'),
        overflow: await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth),
        css: await page.evaluate(() => [...document.styleSheets].some((x) => (x.href ?? '').endsWith('/css/nidan.css'))),
        username: await box('#username'), password: await box('#password'), submit: await box('#kc-login'),
      };
    };
    const en = await layout('en');
    const ne = await layout('ne');
    expect([en.lang, ne.lang]).toEqual(['en', 'ne']);
    for (const l of [en, ne]) {
      expect(l.css, `${l.lang}: Nidan theme`).toBe(true);
      expect(l.overflow, `${l.lang}: no horizontal scroll at phone width`).toBeLessThanOrEqual(0);
    }
    for (const f of ['username', 'password', 'submit'] as const) {
      expect(Math.abs(en[f].x - ne[f].x), `${f} x`).toBeLessThanOrEqual(2);
      expect(Math.abs(en[f].width - ne[f].width), `${f} width`).toBeLessThanOrEqual(2);
      expect(Math.abs(en[f].y - ne[f].y), `${f} y (labels may wrap a little)`).toBeLessThanOrEqual(40);
    }
  });

  test('a disabled account with the RIGHT password reads like any failed login @KC-17-02 @security', async ({ page, playwright }) => {
    // keycloak.v2 showed this one as a page alert above the form instead of under the username field, telling someone
    // who holds the password that the account is disabled; the Nidan login.ftl override puts it in the same place.
    const request = await playwright.request.newContext();
    const disabled = await makeUser(request, ['staff/Nurse']);
    const id = (await (await admin(request, `/users?username=${disabled}&exact=true`)).json())[0].id;
    await admin(request, `/users/${id}`, { method: 'PUT', data: { enabled: false } });
    expect(await failedLoginText(page, disabled, 'Harness-Pass-123')).toBe(await failedLoginText(page, `nobody${Date.now()}`, 'Wrong-Pass-1'));
  });

  test('first login with a slip forces a new password that passes the policy @KC-17-03 @KC-08-04 @KC-08-06 @security', async ({ page, playwright }) => {
    const request = await playwright.request.newContext();
    const user = await makeUser(request, ['staff/Nurse']);
    const id = (await (await admin(request, `/users?username=${user}&exact=true`)).json())[0].id;
    expect((await admin(request, `/users/${id}/reset-password`, { method: 'PUT',
      data: { type: 'password', value: 'river-mango-tiger-42', temporary: true } })).status()).toBe(204);
    // the policy itself (ds8): blocklist case-insensitive, length 8, not the username
    for (const bad of ['Nepal@123', 'KATHMANDU123', 'short', user]) {
      const r = await admin(request, `/users/${id}/reset-password`, { method: 'PUT', data: { type: 'password', value: bad, temporary: true } });
      expect(r.status(), bad).toBe(400);
    }
    await page.goto(loginUrl());
    await page.fill('#username', user);
    await page.fill('#password', 'river-mango-tiger-42');   // gitleaks:allow (test password)
    await page.click('#kc-login');
    await expect(page.locator('#password-new')).toBeVisible();          // UPDATE_PASSWORD, before any access
    await page.fill('#password-new', 'Hospital123');
    await page.fill('#password-confirm', 'Hospital123');
    await page.locator('[type=submit]').first().click();
    await expect(page.locator('#password-new')).toBeVisible();          // blocklisted: still on the page
    await expect(page.locator('body')).toContainText(/password/i);
    const back = page.waitForRequest((r) => r.url().startsWith(`https://${DOMAIN}/oauth2/callback`));
    await page.route(`https://${DOMAIN}/**`, (r) => r.fulfill({ status: 200, body: 'ok' }));
    await page.fill('#password-new', 'Correct-Horse-Battery-9');
    await page.fill('#password-confirm', 'Correct-Horse-Battery-9');
    await page.locator('[type=submit]').first().click();
    expect(new URL((await back).url()).searchParams.get('code')).toBeTruthy();
  });

  test('ui_locales=ne gives the Nepali login page, en the English one @KC-18-01', async ({ page }) => {
    await page.goto(loginUrl() + '&ui_locales=ne');
    await expect(page.locator('html')).toHaveAttribute('lang', 'ne');
    await expect(page.locator('#kc-login')).toHaveText('साइन इन');
    await expect(page.locator('#kc-page-title')).toHaveText('साइन इन गर्नुहोस्');
    await page.context().clearCookies();
    await page.goto(loginUrl() + '&ui_locales=en');
    await expect(page.locator('html')).toHaveAttribute('lang', 'en');
    await expect(page.locator('#kc-login')).toHaveText('Sign in');
    await expect(page.locator('#kc-page-title')).toHaveText('Sign in');
    await expect(page, 'the tab names the hospital (realm display name, NIDAN_BRAND_NAME)').toHaveTitle('Sign in to Nidan EHR');
  });

  test('token and session lifetimes, events on for a year, nobody holds manage-events @KC-08-02 @KC-08-07 @security', async ({ request }) => {
    const realm = await (await admin(request, '')).json();
    expect(realm.accessTokenLifespan).toBe(300);
    expect(realm.ssoSessionIdleTimeout).toBe(900);
    expect(realm.ssoSessionMaxLifespan).toBe(43200);
    const ev = await (await admin(request, '/events/config')).json();
    expect(ev.eventsEnabled).toBe(true);
    expect(ev.eventsExpiration).toBe(31536000);
    expect(ev.adminEventsEnabled).toBe(true);
    for (const t of ['LOGIN', 'LOGIN_ERROR', 'LOGOUT', 'UPDATE_PASSWORD']) expect(ev.enabledEventTypes, t).toContain(t);
    const rm = (await (await admin(request, '/clients?clientId=realm-management')).json())[0].id;
    const holders: unknown[] = await (await admin(request, `/clients/${rm}/roles/manage-events/users`)).json();
    expect(holders, 'users holding realm-management manage-events').toEqual([]);
    const groups: Array<{ id: string; path: string }> = await (await admin(request, '/groups')).json();
    for (const g of groups) {
      const mapped = await (await admin(request, `/groups/${g.id}/role-mappings/clients/${rm}/composite`)).json();
      expect(mapped.map((r: { name: string }) => r.name), g.path).not.toContain('manage-events');
    }
  });

  test('gender is required, email is not @KC-08-03 @security', async ({ request }) => {
    const base = { firstName: 'T', lastName: 'T', enabled: true };
    expect((await admin(request, '/users', { method: 'POST', data: { ...base, username: `nogender${Date.now()}` } })).status()).toBe(400);
    expect((await admin(request, '/users', { method: 'POST', data: { ...base, username: `noemail${Date.now()}`,
      attributes: { gender: ['M'] } } })).status()).toBe(201);
  });

  test('five wrong passwords lock the account for a while, never for good @KC-08-05 @security @slow', async ({ playwright }) => {
    test.setTimeout(150_000);
    const request = await playwright.request.newContext();
    const user = await makeUser(request, ['staff/Nurse']);
    const CH = createHash('sha256').update('harness-verifier-0123456789-0123456789-xyz').digest('base64url');
    const GWCB = `https://${DOMAIN}/oauth2/callback`;
    for (let i = 0; i < 5; i++) {           // five real failures, a second apart (quickLoginCheck is 1 s)
      const kc = new KcSession(request);
      const q = new URLSearchParams({ client_id: 'nidan-gateway', redirect_uri: GWCB, response_type: 'code', scope: 'openid',
        code_challenge: CH, code_challenge_method: 'S256' });
      const page = await kc.go('GET', `${KC_PUBLIC}/realms/nidan/protocol/openid-connect/auth?${q}`);
      await kc.go('POST', formAction(page.body), { username: user, password: `Wrong-${i}-pass` });
      await new Promise((r) => setTimeout(r, 1100));
    }
    const id = (await (await admin(request, `/users?username=${user}&exact=true`)).json())[0].id;
    const s = await (await admin(request, `/attack-detection/brute-force/users/${id}`)).json();
    expect(s.disabled, 'temporarily locked after 5 failures').toBe(true);
    const locked = await new KcSession(request).password('nidan-gateway', GWCB, user, CH);
    expect(gotCode(locked), 'the right password is refused while locked').toBe(false);
    const realm = await (await admin(request, '')).json();
    expect(realm.permanentLockout).toBe(false);
    expect(realm.maxFailureWaitSeconds).toBeLessThanOrEqual(300);
    await expect.poll(async () => gotCode(await new KcSession(request).password('nidan-gateway', GWCB, user, CH)),
      { timeout: 120_000, intervals: [10_000] }).toBe(true);    // the wait ends on its own
  });

  test('people a hospital admin creates, in any job, get no superuser role in any app @KC-57-02 @security', async ({ playwright }) => {
    test.skip(!process.env.KC_ADMIN_PERMISSIONS, 'set KC_ADMIN_PERMISSIONS=1 after keycloak-admin-permissions.py --apply');
    test.setTimeout(180_000);
    const request = await playwright.request.newContext();
    const rolesDir = join(__dirname, '..', '..', '..', 'keycloak', 'realm', 'roles');
    const superuser = new Set<string>();
    for (const f of readdirSync(rolesDir).filter((x) => x.endsWith('.yaml'))) {
      for (const r of (parseYaml(readFileSync(join(rolesDir, f), 'utf8')).roles ?? []) as Array<{ name: string; superuser?: boolean }>) {
        if (r.superuser) superuser.add(r.name);
      }
    }
    expect(superuser.size, 'superuser roles in the catalogs').toBeGreaterThan(0);
    const hadmin = await makeUser(request, ['hospital-admin']);
    const t = await userToken(request, 'nidan-gateway', `https://${DOMAIN}/oauth2/callback`, hadmin);
    const groups: Array<{ id: string; path: string }> = await (await admin(request, '/groups')).json();
    const staffId = groups.find((g) => g.path === '/staff')!.id;
    const jobs: Array<{ name: string }> = await (await admin(request, `/groups/${staffId}/children`)).json();
    const APPS: Array<[string, string]> = [['openmrs', `https://${DOMAIN}/openmrs/oauth2login`], ['odoo', `https://${DOMAIN}:8069/auth_oauth/signin`],
      ['openelis', `https://${DOMAIN}/OpenELIS-Global/login/oauth2/code/nidan-keycloak`],
      ['superset', `https://${DOMAIN}/superset/oauth-authorized/nidan-keycloak`], ['dhis2', `https://${DOMAIN}/dhis2/auth/oidc/callback`],
      ['nidan-gateway', `https://${DOMAIN}/oauth2/callback`]];
    for (const job of jobs) {
      const username = `job${job.name.replace(/\W/g, '').toLowerCase()}${Date.now()}`;
      const created = await request.post(`${KC}/admin/realms/nidan/users`, { headers: { Authorization: `Bearer ${t.__raw}` },
        data: { username, firstName: 'Ram', lastName: 'Thapa', enabled: true, attributes: { gender: ['M'] }, groups: [`/staff/${job.name}`],
          // smuggled privileges: Keycloak must ignore all of these on create
          realmRoles: ['admin', 'requires-otp'], clientRoles: { superset: ['Admin'], openelis: ['Global Administrator'] } } });
      expect(created.status(), `hospital admin creates into ${job.name}`).toBe(201);
      const id = created.headers()['location'].split('/').pop()!;
      // tokens alone would hide a smuggled realm role (fullScopeAllowed=false): read the user's own role mappings too
      const maps = await (await admin(request, `/users/${id}/role-mappings`)).json();
      const realmRoles = (maps.realmMappings ?? []).map((r: { name: string }) => r.name);
      expect(realmRoles.filter((r: string) => r === 'admin' || r === 'requires-otp'), `${job.name}: realm roles`).toEqual([]);
      expect(Object.keys(maps.clientMappings ?? {}), `${job.name}: direct client roles`).toEqual([]);
      await admin(request, `/users/${id}/reset-password`, { method: 'PUT', data: { type: 'password', value: 'Harness-Pass-123', temporary: false } });
      for (const [client, redirect] of APPS) {
        const tok = await userToken(request, client, redirect, username);
        const granted = new Set<string>([...((tok.roles as string[]) ?? []), ...((tok.groups as string[]) ?? []),
          ...Object.values((tok.resource_access ?? {}) as Record<string, { roles: string[] }>).flatMap((r) => r.roles),
          ...((tok.realm_access?.roles as string[]) ?? [])]);
        const bad = [...granted].filter((r) => superuser.has(r) || r === 'admin' || r === 'requires-otp');
        expect(bad, `${job.name} -> ${client}`).toEqual([]);
      }
    }
  });
});
