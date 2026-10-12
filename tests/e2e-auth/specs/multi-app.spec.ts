import { test, expect, APIRequestContext, Browser, BrowserContext, Page } from '@playwright/test';
import { createHmac } from 'crypto';

/**
 * People with access to SEVERAL apps (and not others), on the REAL apps: tests/e2e-auth/harness/sso-openmrs.yml with the
 * odoo, openelis and superset profiles. One Keycloak login must open each permitted app with no second password, each
 * app must see the same person with that app's own roles, every other app must refuse, and sign-out, a removed app
 * role and a disable must act on all of them. Runs when OPENMRS_SSO_FULL, ODOO_SSO_FULL, OPENELIS_SSO_FULL and
 * SUPERSET_SSO_FULL are set (run-keycloak-suite.sh full stage).
 */
const FULL = !!(process.env.OPENMRS_SSO_FULL && process.env.ODOO_SSO_FULL && process.env.OPENELIS_SSO_FULL && process.env.SUPERSET_SSO_FULL);
const KC = process.env.KEYCLOAK_URL ?? 'http://localhost:18180/auth';
const GW = 'https://localhost';
const ODOO = 'https://localhost:8069';
const PASSWORD = 'Harness-Pass-123';
const CARDS = ['openmrs', 'openelis', 'odoo', 'orthanc', 'cis', 'superset', 'dhis2'];

type Roles = Record<string, string[]>;   // client -> role names

async function admin(request: APIRequestContext, path: string, method = 'GET', data?: unknown) {
  const t = await request.post(`${KC}/realms/master/protocol/openid-connect/token`, {
    form: { grant_type: 'password', client_id: 'admin-cli', username: 'admin', password: 'harness-admin-password' } });
  return request.fetch(`${KC}/admin/realms/nidan${path}`, { method, data, headers: { Authorization: `Bearer ${(await t.json()).access_token}` } });
}

async function clientId(request: APIRequestContext, client: string) {
  return (await (await admin(request, `/clients?clientId=${client}`)).json())[0].id as string;
}

async function roleReps(request: APIRequestContext, client: string, names: string[]) {
  const cid = await clientId(request, client);
  const out = [];
  for (const n of names) {
    let r = await admin(request, `/clients/${cid}/roles/${encodeURIComponent(n)}`);
    if (r.status() === 404) {       // the harness realm has no full catalog: add the role, as the catalog would
      await admin(request, `/clients/${cid}/roles`, 'POST', { name: n });
      r = await admin(request, `/clients/${cid}/roles/${encodeURIComponent(n)}`);
    }
    out.push(await r.json());
  }
  return { cid, reps: out };
}

/** A person in a staff job with exactly these client roles (gateway app roles + per-app roles). */
async function person(request: APIRequestContext, prefix: string, roles: Roles, attributes: Record<string, string[]> = {}) {
  const username = `${prefix}${Date.now()}${Math.floor(Math.random() * 1000)}`;
  const r = await admin(request, '/users', 'POST', { username, firstName: prefix, lastName: 'Multi', enabled: true,
    email: `${username}@example.org`, emailVerified: true, attributes: { gender: ['F'], ...attributes }, groups: ['/staff/Nurse'] });
  const id = r.headers()['location'].split('/').pop()!;
  await admin(request, `/users/${id}/reset-password`, 'PUT', { type: 'password', value: PASSWORD, temporary: false });
  for (const [client, names] of Object.entries(roles)) {
    const { cid, reps } = await roleReps(request, client, names);
    await admin(request, `/users/${id}/role-mappings/clients/${cid}`, 'POST', reps);
  }
  return { username, id };
}

async function gatewayLogin(page: Page, username: string) {
  await page.goto(`${GW}/`);                            // no session: straight to Keycloak, the launcher never shows first
  await expect(page).toHaveURL(/\/auth\/realms\/nidan\/protocol\/openid-connect\/auth/);
  await page.fill('#username', username);
  await page.fill('#password', PASSWORD);
  await page.click('#kc-login');
  await page.waitForURL(`${GW}/**`);
}

/** Opens each app the way a person does and returns who the app thinks is signed in (null = not signed in). */
const apps = {
  async openmrs(page: Page) {
    await page.goto(`${GW}/openmrs/oauth2login`);
    const r = await page.request.get(`${GW}/openmrs/ws/rest/v1/session`, { headers: { Accept: 'application/json' } });
    const s = r.ok() ? await r.json() : {};
    return s.user ? { user: s.user.username as string, roles: (s.user.roles ?? []).map((x: { display: string }) => x.display) as string[] } : null;
  },
  async openelis(page: Page) {
    // The SPA's own background calls set an OpenELIS session cookie; a person clicks the SSO button once the page has
    // settled. A test that starts SSO while those calls are in flight can lose that race, so it retries the SSO once.
    const session = async () => {
      const r = await page.request.get(`${GW}/api/OpenELIS-Global/session`, { headers: { Accept: 'application/json' } });
      return r.ok() ? await r.json() : {};
    };
    await page.goto(`${GW}/openelis/`);
    await page.waitForLoadState('networkidle');
    let s: { authenticated?: boolean; loginName?: string; roles?: string[] } = {};
    for (let attempt = 0; attempt < 2 && !s.authenticated; attempt++) {
      await page.goto(`${GW}/api/OpenELIS-Global/oauth2/authorization/nidan-keycloak`);
      await page.waitForLoadState('networkidle');
      s = await session();
    }
    return s.authenticated ? { user: s.loginName as string, roles: (s.roles ?? []) as string[] } : null;
  },
  async superset(page: Page) {
    await page.goto(`${GW}/superset/login/nidan-keycloak`);
    await page.waitForLoadState('networkidle');
    const r = await page.request.get(`${GW}/superset/api/v1/me/roles/`, { headers: { Accept: 'application/json' } });
    if (!r.ok()) return null;
    const me = (await r.json()).result;
    return { user: me.username as string, roles: Object.keys(me.roles ?? {}) };
  },
  async odoo(page: Page) {
    await page.goto(`${ODOO}/web/login`);                // goes on to Keycloak by itself (session there) and back
    // Odoo's web client keeps a live connection open (never network-idle): wait for its app URL instead
    await page.waitForURL(/localhost:8069\/(odoo|web)(\/|\?|#|$)/, { timeout: 60_000 }).catch(() => undefined);
    const r = await page.request.post(`${ODOO}/web/session/get_session_info`, { data: { jsonrpc: '2.0', method: 'call', params: {} } });
    const info = r.ok() ? await r.json().catch(() => ({})) : {};
    return info.result?.uid ? { user: info.result.username as string, roles: [] as string[] } : null;
  },
};
type App = keyof typeof apps;

/** Who each app thinks is signed in, from its session API only (no navigation). */
const whoami: Record<App, (r: APIRequestContext) => Promise<string | undefined>> = {
  openmrs: async (r) => (await (await r.get(`${GW}/openmrs/ws/rest/v1/session`, { headers: { Accept: 'application/json' } })).json().catch(() => ({})))?.user?.username,
  openelis: async (r) => {
    const s = await (await r.get(`${GW}/api/OpenELIS-Global/session`, { headers: { Accept: 'application/json' } })).json().catch(() => ({}));
    return s.authenticated ? s.loginName : undefined;
  },
  superset: async (r) => {
    const x = await r.get(`${GW}/superset/api/v1/me/`, { headers: { Accept: 'application/json' } });
    return x.ok() ? (await x.json()).result?.username : undefined;
  },
  odoo: async (r) => {
    const x = await r.post(`${ODOO}/web/session/get_session_info`, { data: { jsonrpc: '2.0', method: 'call', params: {} } });
    const info = x.ok() ? await x.json().catch(() => ({})) : {};
    return info.result?.uid ? info.result.username : undefined;
  },
};

/** Each app's own login page: a person who came through the launcher must never be left on one. */
const LOGIN_PAGE: Record<App, RegExp> = {
  openmrs: /\/openmrs\/spa\/login\/?(\?|$)/, openelis: /\/openelis\/login/, superset: /\/superset\/login\/?(\?|$)/, odoo: /\/web\/login/,
};

/** What the gateway does when this person (no matter their app session) navigates to an app's front door. */
async function gatewayAnswer(ctx: BrowserContext, app: App | 'orthanc') {
  const url = { openmrs: `${GW}/openmrs/spa/home`, openelis: `${GW}/openelis/`, superset: `${GW}/superset/`,
    odoo: `${ODOO}/web`, orthanc: `${GW}/ohif/` }[app];
  const r = await ctx.request.get(url, { maxRedirects: 0, headers: { 'Sec-Fetch-Mode': 'navigate', Accept: 'text/html' } });
  const loc = r.headers()['location'] ?? '';
  return loc.includes('/not-allowed.html') ? 'not-allowed' : loc.includes('/oauth2/start') ? 'login' : r.status() < 400 ? 'allowed' : `status ${r.status()}`;
}

async function cards(page: Page) {
  await page.goto(`${GW}/?launcher`);
  await expect(page.locator('#identity-line')).toBeVisible();
  const out: string[] = [];
  for (const id of CARDS) if (await page.locator(`#card-${id}`).isVisible()) out.push(id);
  return out;
}

async function newPage(browser: Browser) {
  const ctx = await browser.newContext({ ignoreHTTPSErrors: true });
  return { ctx, page: await ctx.newPage() };
}

test.describe('people with several apps', () => {
  test.skip(!FULL, 'needs the full harness with odoo, openelis and superset (OPENMRS/ODOO/OPENELIS/SUPERSET_SSO_FULL=1)');
  test.describe.configure({ mode: 'serial' });
  test.setTimeout(180_000);

  const MIXES: Array<{ name: string; roles: Roles; attrs?: Record<string, string[]>; has: App[]; hasNot: Array<App | 'orthanc'>;
    cards: string[]; appRoles: Partial<Record<App, string[]>> }> = [
    { name: 'clinician', roles: { 'nidan-gateway': ['app-openmrs', 'app-openelis', 'app-superset'],
      openmrs: ['Organizational: Doctor', 'Application: Writes Clinical Notes'], openelis: ['Results', 'Validation'], superset: ['Gamma', 'Clinical'] },
      has: ['openmrs', 'openelis', 'superset'], hasNot: ['odoo', 'orthanc'], cards: ['openmrs', 'openelis', 'superset'],
      appRoles: { openmrs: ['Organizational: Doctor', 'Application: Writes Clinical Notes'], openelis: ['Results', 'Validation'], superset: ['Gamma', 'Clinical'] } },
    { name: 'frontdesk', roles: { 'nidan-gateway': ['app-openmrs', 'app-odoo'], openmrs: ['Organizational: Registration Clerk'],
      odoo: ['point_of_sale.group_pos_user'] }, attrs: { nidan_shops: ['Harness Shop A'] },
      has: ['openmrs', 'odoo'], hasNot: ['openelis', 'superset', 'orthanc'], cards: ['openmrs', 'odoo'],
      appRoles: { openmrs: ['Organizational: Registration Clerk'] } },
    { name: 'labcashier', roles: { 'nidan-gateway': ['app-openelis', 'app-odoo'], openelis: ['Reception'], odoo: ['point_of_sale.group_pos_user'] },
      attrs: { nidan_shops: ['Harness Shop B'] }, has: ['openelis', 'odoo'], hasNot: ['openmrs', 'superset', 'orthanc'],
      cards: ['openelis', 'odoo'], appRoles: { openelis: ['Reception'] } },
  ];

  for (const mix of MIXES) {
    test(`${mix.name}: one login opens ${mix.has.join(' + ')} with their own roles; ${mix.hasNot.join(', ')} refused; launcher shows exactly those @KC-10-05 @KC-15-01 @security @sso-on-only`,
      async ({ browser, request }) => {
        const { username } = await person(request, mix.name, mix.roles, mix.attrs);
        const { ctx, page } = await newPage(browser);
        await gatewayLogin(page, username);
        expect(await cards(page), 'launcher cards').toEqual(mix.cards);
        for (const app of mix.has) {
          const who = await apps[app](page);
          expect(who?.user?.toLowerCase(), `${app}: same person, no second password`).toBe(username.toLowerCase());
          await expect(page.locator('#username'), `${app}: no password prompt`).toHaveCount(0);
          for (const role of mix.appRoles[app] ?? []) expect(who!.roles, `${app} roles`).toContain(role);
        }
        for (const app of mix.hasNot) expect(await gatewayAnswer(ctx, app), `${app} for ${mix.name}`).toBe('not-allowed');
        for (const app of mix.has) expect(await gatewayAnswer(ctx, app), `${app} front door`).toBe('allowed');
        await ctx.close();
      });
  }

  // The launcher card is how people open an app (bug 2026-10-11: OpenELIS and Superset showed their own login page with
  // an SSO button). Clicking a card must land in the app already signed in, never on the app's login page.
  for (const mix of MIXES) {
    test(`${mix.name}: every launcher card opens its app already signed in, with no app login page or button to press @KC-10-05 @KC-15-01 @sso-on-only`,
      async ({ browser, request }) => {
        const { username } = await person(request, `${mix.name}card`, mix.roles, mix.attrs);
        const { ctx, page } = await newPage(browser);
        await gatewayLogin(page, username);
        for (const app of mix.has) {
          expect(await cards(page)).toContain(app);
          const [tab] = await Promise.all([ctx.waitForEvent('page'), page.click(`#card-${app}`)]);
          await expect.poll(async () => {
            const url = tab.url();
            if (LOGIN_PAGE[app].test(url)) return `on the ${app} login page: ${url}`;
            if (url.includes('/protocol/openid-connect/')) return `on Keycloak: ${url}`;
            return (await whoami[app](tab.request))?.toLowerCase() ?? `not signed in at ${url}`;
          }, { timeout: 60_000, intervals: [2_000], message: `${app} card: signed in with nothing to click` }).toBe(username.toLowerCase());
          await tab.waitForTimeout(3_000);         // and it stays there: no late redirect to a login page
          expect(LOGIN_PAGE[app].test(tab.url()), `${app} ends on ${tab.url()}`).toBe(false);
          await tab.close();
        }
        await ctx.close();
      });
  }

  // Rule 7 (changed 2026-10-12): an app's own logout ends that app's session and returns to the launcher. With
  // several apps the launcher stays (notice + "sign out of everything") and the other apps keep working; with one app
  // the person is signed out of everything and sees the login page.
  test('with several apps, logging out of Odoo or OpenMRS returns to the launcher; the other app stays signed in @KC-19-01 @security @sso-on-only',
    async ({ browser, request }) => {
      const p = await person(request, 'lout', MIXES[1].roles, MIXES[1].attrs);   // OpenMRS + Odoo
      const { ctx, page } = await newPage(browser);
      await gatewayLogin(page, p.username);
      expect((await apps.openmrs(page))?.user).toBe(p.username);
      expect((await apps.odoo(page))?.user?.toLowerCase()).toBe(p.username.toLowerCase());

      await page.goto(`${ODOO}/web/session/logout`);          // Odoo's own logout link
      await expect(page).toHaveURL(`${GW}/`);
      await expect(page.locator('#signed-out')).toContainText('ERP & Inventory');
      expect(await whoami.odoo(ctx.request), 'Odoo session ended').toBeUndefined();
      expect(await whoami.openmrs(ctx.request), 'OpenMRS still signed in').toBe(p.username);
      expect(await cards(page)).toEqual(['openmrs', 'odoo']);

      await page.goto(`${GW}/openmrs/oauth2logout`);         // what the O3 logout button opens
      await expect(page).toHaveURL(`${GW}/`);
      await expect(page.locator('#signed-out')).toBeVisible();
      expect(await whoami.openmrs(ctx.request), 'OpenMRS session ended').toBeUndefined();
      expect((await (await admin(request, `/users/${p.id}/sessions`)).json()).length, 'central session kept').toBeGreaterThan(0);
      expect((await apps.odoo(page))?.user?.toLowerCase(), 'Odoo opens again with no password').toBe(p.username.toLowerCase());
      await ctx.close();
    });

  // The same through the apps' own buttons (what a person clicks), not their logout addresses.
  test('the Logout buttons in OpenMRS and Odoo return to the launcher with the other app still open @KC-19-01 @security @sso-on-only',
    async ({ browser, request }) => {
      const p = await person(request, 'btn', MIXES[1].roles, MIXES[1].attrs);   // OpenMRS + Odoo
      const { ctx, page } = await newPage(browser);
      await gatewayLogin(page, p.username);
      expect((await apps.odoo(page))?.user?.toLowerCase()).toBe(p.username.toLowerCase());

      expect((await apps.openmrs(page))?.user).toBe(p.username);   // signed in to OpenMRS (lands on its home)
      await page.goto(`${GW}/openmrs/spa/home`);
      const account = page.getByRole('button', { name: 'My Account' }), place = page.locator('input[type=radio]').first();
      await account.or(place).first().waitFor({ timeout: 60_000 });
      if (await place.isVisible()) {                         // the login-location step
        await place.check({ force: true });
        await page.getByRole('button', { name: /confirm/i }).click();
      }
      const logout = page.getByRole('button', { name: 'Logout' });
      await page.waitForLoadState('domcontentloaded');
      // A closed user menu is parked just off screen to the right, so Playwright still calls its Logout "visible":
      // decide by position. Open it when Logout is off screen, wait until it is on screen, click; again if O3
      // re-rendered and closed it meanwhile.
      const onScreen = async () => { const b = await logout.boundingBox(); return !!b && b.x + b.width <= page.viewportSize()!.width; };
      await expect(async () => {
        if (!(await onScreen())) await account.click();
        await expect(logout).toBeInViewport({ timeout: 3_000 });
        await logout.click({ timeout: 3_000 });
      }).toPass({ timeout: 45_000, intervals: [500, 1_000, 2_000] });
      await expect(page).toHaveURL(`${GW}/`, { timeout: 30_000 });
      await expect(page.locator('#signed-out')).toContainText('Electronic Medical Record');
      expect(await whoami.openmrs(ctx.request), 'OpenMRS session ended').toBeUndefined();

      expect((await apps.odoo(page))?.user?.toLowerCase(), 'Odoo still open, no password').toBe(p.username.toLowerCase());
      await page.locator('.o_user_menu button, .o_user_menu .dropdown-toggle').first().click();
      await page.locator('a[href*="/web/session/logout"], [data-menu="logout"]').first().click();
      await expect(page).toHaveURL(`${GW}/`, { timeout: 30_000 });
      await expect(page.locator('#signed-out')).toContainText('ERP & Inventory');
      expect(await whoami.odoo(ctx.request), 'Odoo session ended').toBeUndefined();
      await ctx.close();
    });

  test('the logout entries in OpenELIS and Superset return to the launcher with the other apps still open @KC-19-01 @security @sso-on-only',
    async ({ browser, request }) => {
      const p = await person(request, 'btn2', MIXES[0].roles);   // OpenMRS + OpenELIS + Superset
      const { ctx, page } = await newPage(browser);
      await gatewayLogin(page, p.username);

      expect((await apps.openelis(page))?.user).toBe(p.username);
      await page.goto(`${GW}/openelis/`);
      await page.locator('#user-Icon').click();                // the header's user menu
      await page.locator('[data-cy="logOut"]').click();
      await expect(page).toHaveURL(`${GW}/`, { timeout: 30_000 });
      await expect(page.locator('#signed-out')).toContainText('Laboratory Information System');
      expect(await whoami.openelis(ctx.request), 'OpenELIS session ended').toBeUndefined();

      expect((await apps.superset(page))?.user).toBe(p.username);
      await page.goto(`${GW}/superset/dashboard/list/`);
      await page.getByText('Settings', { exact: true }).hover();
      await page.locator('a[href*="/logout"]').first().click();
      await expect(page).toHaveURL(`${GW}/`, { timeout: 30_000 });
      await expect(page.locator('#signed-out')).toContainText('Analytics');
      expect(await whoami.superset(ctx.request), 'Superset session ended').toBeUndefined();

      expect((await apps.openmrs(page))?.user, 'OpenMRS still opens with no password').toBe(p.username);
      await ctx.close();
    });

  // NIDAN_APP_LOGOUT_ENDS_ALL=true (shared PCs): any app logout signs out of everything, even with several apps.
  // The harness gateway runs with the default (false), so the page's flag is switched in the browser.
  test('with NIDAN_APP_LOGOUT_ENDS_ALL=true, an app logout signs out of everything even with several apps @KC-19-01 @security @sso-on-only',
    async ({ browser, request }) => {
      const p = await person(request, 'shared', MIXES[1].roles, MIXES[1].attrs);   // OpenMRS + Odoo
      const { ctx, page } = await newPage(browser);
      await page.route(/^https:\/\/localhost\/\?signed_out=/, async (route) => {
        const r = await route.fetch();
        await route.fulfill({ response: r, body: (await r.text()).replace('data-logout-all="false"', 'data-logout-all="true"') });
      });
      await gatewayLogin(page, p.username);
      expect((await apps.odoo(page))?.user?.toLowerCase()).toBe(p.username.toLowerCase());
      // Odoo's logout, then the launcher address it redirects to, opened directly: Playwright does not route the
      // target of a redirect, and the flag is switched on that page
      const out = await page.request.get(`${ODOO}/web/session/logout`, { maxRedirects: 0 });
      expect(out.headers()['location']).toBe(`${GW}/?signed_out=odoo`);
      await page.goto(`${GW}/?signed_out=odoo`);
      await expect(page.locator('#kc-login'), 'the login page, not the launcher').toBeVisible({ timeout: 30_000 });
      await expect.poll(async () => (await (await admin(request, `/users/${p.id}/sessions`)).json()).length,
        { message: 'Keycloak session ended' }).toBe(0);
      await ctx.close();
    });

  test('with one app, logging out of it signs out of everything and shows the login page @KC-19-01 @security @sso-on-only',
    async ({ browser, request }) => {
      const p = await person(request, 'lone', { 'nidan-gateway': ['app-odoo'], odoo: ['point_of_sale.group_pos_user'] },
        { nidan_shops: ['Harness Shop A'] });
      const { ctx, page } = await newPage(browser);
      await gatewayLogin(page, p.username);
      expect((await apps.odoo(page))?.user?.toLowerCase()).toBe(p.username.toLowerCase());
      await page.goto(`${ODOO}/web/session/logout`);
      await expect(page.locator('#kc-login'), 'the login page, not the launcher').toBeVisible({ timeout: 30_000 });
      await expect.poll(async () => (await (await admin(request, `/users/${p.id}/sessions`)).json()).length,
        { message: 'Keycloak session ended' }).toBe(0);
      expect(await whoami.odoo(ctx.request), 'Odoo session ended').toBeUndefined();
      await ctx.close();
    });

  // The launcher's header: who is signed in, Change password and Sign out at the top right, beside the language.
  test('the launcher shows the signed-in person and Sign out at the top right @KC-15-01 @sso-on-only', async ({ browser, request }) => {
    const p = await person(request, 'head', MIXES[0].roles);
    const { ctx, page } = await newPage(browser);
    await gatewayLogin(page, p.username);
    await cards(page);
    const line = page.locator('header .header-right #identity-line');
    await expect(line).toContainText(p.username);
    const out = line.locator('a.sign-out[href="/oauth2/sign_out"]');
    await expect(out).toBeVisible();
    const box = (await out.boundingBox())!, vw = page.viewportSize()!.width;
    expect(box.x + box.width, 'Sign out sits at the right edge').toBeGreaterThan(vw * 0.75);
    expect(box.y, 'and at the top').toBeLessThan(150);
    await ctx.close();
  });

  // Branding (proxy/assets/brand): the launcher and the Keycloak login page use the same logo and colours.
  test('the login page and the launcher use the gateway\'s branding folder @KC-17-01 @sso-on-only', async ({ browser }) => {
    const { ctx, page } = await newPage(browser);
    await page.goto(`${GW}/`);
    await expect(page.locator('#kc-login')).toBeVisible();
    const logo = await page.locator('#kc-header-wrapper').evaluate((e) => getComputedStyle(e).backgroundImage);
    expect(logo, 'login page logo from brand/').toMatch(/\/login\/nidan\/brand\/logo\.svg/);
    const url = /url\("?([^")]+)"?\)/.exec(logo)![1];
    expect((await page.request.get(url)).headers()['content-type']).toContain('image/svg');
    const primary = await page.evaluate(() => getComputedStyle(document.documentElement).getPropertyValue('--brand-primary').trim());
    expect(primary, 'brand.css loaded on the login page').not.toBe('');
    const launcher = await ctx.request.get(`${GW}/assets/brand/logo.svg`);
    expect(launcher.status()).toBe(200);
    await ctx.close();
  });

  // The Staff page (:9443) has the same header: the hospital's logo, who is signed in, All apps, Sign out.
  test('the Staff page shows the branding, the signed-in admin, All apps and Sign out @KC-20-01 @sso-on-only', async ({ browser, request }) => {
    const username = `stf${Date.now()}`;
    const r = await admin(request, '/users', 'POST', { username, firstName: 'Hari', lastName: 'Admin', enabled: true,
      email: `${username}@example.org`, emailVerified: true, attributes: { gender: ['M'] }, groups: ['/hospital-admin'] });
    const id = r.headers()['location'].split('/').pop()!;
    await admin(request, `/users/${id}/reset-password`, 'PUT', { type: 'password', value: PASSWORD, temporary: false });
    const { ctx, page } = await newPage(browser);
    await page.goto('https://localhost:18445/staff/');
    await page.fill('#username', username);
    await page.fill('#password', PASSWORD);
    await page.click('#kc-login');
    // admins must set up an authenticator (KC-08): read the key from the form, answer with its current code
    const secret = await page.locator('input[name="totpSecret"]').inputValue();
    const msg = Buffer.alloc(8); msg.writeBigUInt64BE(BigInt(Math.floor(Date.now() / 30000)));
    const d = createHmac('sha1', Buffer.from(secret)).update(msg).digest(); const o = d[d.length - 1] & 15;
    await page.fill('#totp', String((d.readUInt32BE(o) & 0x7fffffff) % 1000000).padStart(6, '0'));
    if (await page.locator('#userLabel').count()) await page.fill('#userLabel', 'test phone');
    await page.locator('#kc-totp-settings-form [type="submit"], input[type="submit"]').first().click();
    await page.waitForURL('https://localhost:18445/staff/**', { timeout: 60_000 });
    await expect(page.locator('#who-name')).toHaveText(username);
    await expect(page.locator('#who a.sign-out[href="/oauth2/sign_out"]')).toBeVisible();
    await expect(page.locator('#to-apps')).toHaveAttribute('href', 'https://localhost/');
    expect(await page.locator('header .logo').evaluate((i: HTMLImageElement) => i.complete && i.naturalWidth > 0), 'logo loaded').toBe(true);
    await ctx.close();
  });

  // KC-11-04 on real apps: A (OpenMRS + Odoo) signs out; B (OpenMRS + OpenELIS + Superset) signs in on the same browser:
  // every app shows B, and A's other app is refused for B.
  test('A signs out, B signs in on the same browser: every app shows B, A\'s apps are refused for B @KC-11-04 @security @sso-on-only',
    async ({ browser, request }) => {
      const a = await person(request, 'deska', MIXES[1].roles, MIXES[1].attrs);
      const b = await person(request, 'clinb', MIXES[0].roles);
      const { ctx, page } = await newPage(browser);
      await gatewayLogin(page, a.username);
      expect((await apps.openmrs(page))?.user).toBe(a.username);
      expect((await apps.odoo(page))?.user?.toLowerCase()).toBe(a.username.toLowerCase());
      await page.goto(`${GW}/oauth2/sign_out`);
      await page.waitForLoadState();
      expect(await (await admin(request, `/users/${a.id}/sessions`)).json(), 'A has no Keycloak session left').toEqual([]);
      for (const app of ['openmrs', 'odoo'] as const) expect(await gatewayAnswer(ctx, app), `${app} after sign-out`).toBe('login');
      await gatewayLogin(page, b.username);
      for (const app of ['openmrs', 'openelis', 'superset'] as const) {
        expect((await apps[app](page))?.user?.toLowerCase(), `${app} shows B`).toBe(b.username.toLowerCase());
      }
      expect(await gatewayAnswer(ctx, 'odoo'), 'A\'s Odoo is refused for B').toBe('not-allowed');
      await ctx.close();
    });

  // Taking one app away (a job change) acts at the next login; the person's other apps keep working.
  test('removing one app role refuses that app at the next login and leaves the others working @KC-15-01 @security @sso-on-only',
    async ({ browser, request }) => {
      const p = await person(request, 'move', MIXES[1].roles, MIXES[1].attrs);
      let { ctx, page } = await newPage(browser);
      await gatewayLogin(page, p.username);
      expect(await gatewayAnswer(ctx, 'odoo')).toBe('allowed');
      await ctx.close();
      const gw = await clientId(request, 'nidan-gateway');
      await admin(request, `/users/${p.id}/role-mappings/clients/${gw}`, 'DELETE', [await (await admin(request, `/clients/${gw}/roles/app-odoo`)).json()]);
      ({ ctx, page } = await newPage(browser));
      await gatewayLogin(page, p.username);
      expect(await gatewayAnswer(ctx, 'odoo'), 'Odoo after the role was removed').toBe('not-allowed');
      expect((await apps.openmrs(page))?.user, 'OpenMRS still works').toBe(p.username);
      expect(await cards(page)).toEqual(['openmrs']);
      await ctx.close();
    });

  // KC-11-01 on real apps: disabled while active in OpenMRS and Odoo -> both refuse within 6 minutes.
  test('disabled while active in two apps: both refuse within 6 minutes @KC-11-01 @security @slow @sso-on-only', async ({ browser, request }) => {
    test.setTimeout(10 * 60_000);
    const p = await person(request, 'dis', MIXES[1].roles, MIXES[1].attrs);
    const { ctx, page } = await newPage(browser);
    await gatewayLogin(page, p.username);
    expect((await apps.openmrs(page))?.user).toBe(p.username);
    expect((await apps.odoo(page))?.user?.toLowerCase()).toBe(p.username.toLowerCase());
    const t0 = Date.now();
    expect((await admin(request, `/users/${p.id}/logout`, 'POST')).status()).toBe(204);   // the Staff page's disable
    expect((await admin(request, `/users/${p.id}`, 'PUT', { enabled: false })).status()).toBe(204);
    const api = async (url: string) => (await ctx.request.get(url, { maxRedirects: 0, headers: { Accept: 'application/json' } })).status();
    await expect.poll(async () => [await api(`${GW}/openmrs/ws/rest/v1/session`), await api(`${ODOO}/web/webclient/version_info`)],
      { timeout: 6 * 60_000, intervals: [15_000] }).toEqual([401, 401]);
    console.log(`multi-app disable: refused after ${Math.round((Date.now() - t0) / 1000)} s`);
    await ctx.close();
  });
});
