import { expect, Page, test } from '@playwright/test';

/**
 * Launcher (backlog KC-15, KC-04-01, KC-04-04, KC-12-01). Runs against any gateway; the SSO-on cases
 * stub /oauth2/userinfo so they also run before oauth2-proxy exists.
 */
const CARDS = ['openmrs', 'openelis', 'odoo', 'orthanc', 'cis', 'superset', 'dhis2'];
const HEALTH: Record<string, string> = { openmrs: '/openmrs/spa/config.json', openelis: '/openelis/', odoo: '/odoo-health/',
  orthanc: '/auth-ping/orthanc', cis: '/cis/actuator/health', superset: '/superset/', dhis2: '/dhis2/auth/login' };

// Health probes are stubbed "up" so these tests depend only on the launcher, not on which apps run
// behind this gateway; individual tests override chosen ones to 502 (the last registered route wins).
test.beforeEach(async ({ page }) => {
  for (const url of Object.values(HEALTH)) await page.route(`**${url}`, (r) => r.fulfill({ status: 200, body: 'ok' }));
});

async function stubUser(page: Page, user: string, roles: string[]) {
  await page.route('**/oauth2/userinfo', (route) => route.fulfill({
    contentType: 'application/json',
    body: JSON.stringify({ user, preferredUsername: user, groups: roles.map((r) => `role:nidan-gateway:${r}`) }),
  }));
}

async function visibleCards(page: Page) {
  const out: string[] = [];
  for (const id of CARDS) if (await page.locator(`#card-${id}`).isVisible()) out.push(id);
  return out;
}

function watch(page: Page, origin: string) {
  const cspViolations: string[] = [];
  const foreign: string[] = [];
  page.on('console', (m) => { if (/Content Security Policy|Refused to/i.test(m.text())) cspViolations.push(m.text()); });
  page.on('request', (r) => { if (!r.url().startsWith(origin) && !r.url().startsWith('data:')) foreign.push(r.url()); });
  return { cspViolations, foreign };
}

test('SSO off: every card, no identity line @KC-15-07 @security', async ({ page }) => {
  await page.goto('/');
  await expect(page.locator('#card-openmrs')).toBeVisible();
  expect(await visibleCards(page)).toEqual(CARDS);
  await expect(page.locator('#identity-line')).toBeHidden();
});

test('a nurse sees only her cards and who she is signed in as @KC-15-01 @KC-15-02 @security', async ({ page }) => {
  await stubUser(page, 'nurse1', ['app-openmrs']);
  await page.goto('/?launcher');
  await expect(page.locator('#identity-line')).toBeVisible();
  expect(await visibleCards(page)).toEqual(['openmrs']);
  await expect(page.locator('#identity-line')).toContainText('Signed in as nurse1');
  await expect(page.locator('#identity-line a')).toHaveAttribute('href', '/oauth2/sign_out');
});

test('only cis-admin sees Sync Issues; a cashier sees only Billing @KC-15-08 @security', async ({ page }) => {
  await stubUser(page, 'cisadmin1', ['cis-admin']);
  await page.goto('/?launcher');
  await expect(page.locator('#identity-line')).toBeVisible();
  expect(await visibleCards(page)).toEqual(['cis']);
  await stubUser(page, 'cashier1', ['app-odoo']);
  await page.goto('/?launcher');
  await expect(page.locator('#identity-line')).toContainText('cashier1');
  expect(await visibleCards(page)).toEqual(['odoo']);
});

test('no app roles: the no-apps message @KC-15-04', async ({ page }) => {
  await stubUser(page, 'newstaff', []);
  await page.goto('/');
  await expect(page.locator('.no-apps')).toContainText('hospital administrator');
  expect(await visibleCards(page)).toEqual([]);
});

test('a display name with markup renders as text @KC-15-09 @security', async ({ page }) => {
  let dialog = false;
  page.on('dialog', async (d) => { dialog = true; await d.dismiss(); });
  await stubUser(page, '<img src=x onerror=alert(1)>', ['app-openmrs']);
  await page.goto('/?launcher');
  await expect(page.locator('#identity-line strong')).toHaveText('<img src=x onerror=alert(1)>');
  expect(await page.locator('#identity-line img').count()).toBe(0);
  await page.waitForTimeout(300);
  expect(dialog).toBe(false);
});

test('card links open one named tab per app @KC-15-06', async ({ page }) => {
  await page.goto('/');
  for (const id of CARDS) {
    await expect(page.locator(`#card-${id}`)).toHaveAttribute('target', `nidan-${id}`);
    await expect(page.locator(`#card-${id}`)).toHaveAttribute('rel', /noopener/);
  }
});

test('no demo credentials on the launcher @KC-04-01 @security', async ({ page }) => {
  test.skip(process.env.GATEWAY_SHOW_DEMO_CREDENTIALS === 'true', 'public demo shows them on purpose');
  await page.goto('/');
  await expect(page.locator('body')).not.toContainText(/demo credentials|admin\/admin|Admin123/i);
  expect(await page.locator('#credentialBtn').count()).toBe(0);
});

test('strict CSP holds and nothing loads from another origin @KC-04-04 @KC-12-01 @security', async ({ page, baseURL }) => {
  test.skip(!!process.env.GATEWAY_ANALYTICS_ID, 'analytics deliberately enabled on this deployment');
  const seen = watch(page, new URL(baseURL!).origin);
  const res = await page.goto('/');
  expect(res!.headers()['content-security-policy']).toContain("default-src 'self'");
  // not 'networkidle': health probes to apps that are down stay pending until the upstream timeout
  await page.waitForLoadState('load');
  await page.waitForTimeout(1500);
  expect(seen.cspViolations).toEqual([]);
  expect(seen.foreign).toEqual([]);
});

test.describe('KC-15-03/05 single-app redirect and error states', () => {
  async function healthDown(page: Page, ids: string[]) {
    for (const id of ids) await page.route(`**${HEALTH[id]}`, (r) => r.fulfill({ status: 502, body: '' }));
  }

  test('exactly one permitted app that is up: straight into it @KC-15-03', async ({ page }) => {
    await stubUser(page, 'nurse1', ['app-openmrs']);
    await page.route('**/openmrs/spa', (r) => r.fulfill({ contentType: 'text/html', body: '<title>O3</title>' }));
    await page.goto('/');
    await page.waitForURL('**/openmrs/spa');
  });

  test('that one app is down: stay on the launcher with its card marked Offline @KC-15-03', async ({ page }) => {
    await stubUser(page, 'nurse1', ['app-openmrs']);
    await healthDown(page, ['openmrs']);
    await page.goto('/');
    await expect(page.locator('#status-openmrs')).toHaveText('Offline');
    expect(new URL(page.url()).pathname).toBe('/');
  });

  test('/?launcher never redirects @KC-15-03', async ({ page }) => {
    await stubUser(page, 'nurse1', ['app-openmrs']);
    await page.goto('/?launcher');
    await expect(page.locator('#status-openmrs')).toHaveText(/Online|Offline/);
    await page.waitForTimeout(500);
    expect(new URL(page.url()).search).toBe('?launcher');
  });

  test('an app that never answers is Offline within the probe timeout, others unaffected @KC-15-05', async ({ page }) => {
    await page.route(`**${HEALTH.dhis2}`, () => new Promise(() => {})); // accepts, never answers
    await stubUser(page, 'hmis1', ['app-dhis2', 'app-superset']);
    await page.goto('/?launcher');
    await expect(page.locator('#status-superset')).toHaveText('Online', { timeout: 2000 });
    await expect(page.locator('#status-dhis2')).toHaveText('Offline', { timeout: 8000 });
  });

  test('every visible app down: the all-down banner @KC-15-05', async ({ page }) => {
    await stubUser(page, 'doctor1', ['app-openmrs', 'app-orthanc', 'app-dhis2']);
    await healthDown(page, ['openmrs', 'dhis2']);
    await page.goto('/?launcher');
    await expect(page.locator('#all-down')).toContainText('paper downtime');
  });

  test('login service error: the retry banner, no cards @KC-15-05 @security', async ({ page }) => {
    await page.route('**/oauth2/userinfo', (r) => r.fulfill({ status: 503, body: '' }));
    await page.goto('/');
    await expect(page.locator('.state-banner')).toContainText('Could not load your apps');
    await expect(page.locator('.state-banner button')).toHaveText('Try again');
    expect(await visibleCards(page)).toEqual([]);
  });

  test('expired session: back to login @KC-15-05 @security', async ({ page }) => {
    await page.route('**/oauth2/userinfo', (r) => r.fulfill({ status: 401, body: '' }));
    await page.route('**/oauth2/start**', (r) => r.fulfill({ contentType: 'text/html', body: '<title>login</title>' }));
    await page.goto('/');
    await page.waitForURL('**/oauth2/start?rd=%2F');
  });
});
