import { expect, Page, Route, test } from '@playwright/test';

/**
 * Staff page (backlog KC-21/22/23) against an in-memory fake of the Keycloak admin API, so it runs
 * without Keycloak. The gateway side of /staff-api (allow-list, CSRF guard) is tested in staff-api.spec.ts.
 */
const STAFF = process.env.STAFF_URL ?? 'https://127.0.0.1:18445';
const NURSE = '11111111-2222-4333-8444-555555555555';
const CASHIER = '22222222-3333-4444-8555-666666666666';
const ADMIN_GROUP = '33333333-4444-4555-8666-777777777777';
const uid = (n: number) => `0f8e9a4c-1b2d-4e3f-8a9b-${String(n).padStart(12, '0')}`;

type User = { id: string; username: string; firstName: string; lastName: string; enabled: boolean; groups: string[];
  attributes?: Record<string, string[]> };

async function fakeKeycloak(page: Page) {
  const users: User[] = [
    { id: uid(1), username: 'nurse1', firstName: 'Sita', lastName: 'Rai', enabled: true, groups: [NURSE],
      attributes: { gender: ['F'], nidan_locations: ['Ward A'], nidan_shops: ['Old Shop'] } },
    { id: uid(2), username: 'cashier1', firstName: 'Hari', lastName: 'KC', enabled: true, groups: [CASHIER] },
    { id: uid(3), username: 'evil', firstName: '<img src=x onerror=alert(1)>', lastName: 'X', enabled: true, groups: [NURSE] },
  ];
  const calls: { method: string; path: string; body: any; staffHeader: string | undefined }[] = [];
  await page.route(`${STAFF}/staff/staff-config.json`, (r) => r.fulfill({ contentType: 'application/json',
    body: JSON.stringify({ jobs: [{ name: 'Nurse', id: NURSE, path: '/staff/Nurse' }, { name: 'Cashier', id: CASHIER, path: '/staff/Cashier' },
      { name: 'Hospital Admins', id: ADMIN_GROUP, path: '/admins/hospital' }] }) }));
  await page.route(`${STAFF}/staff-api/**`, async (route: Route) => {
    const req = route.request();
    const url = new URL(req.url());
    const path = url.pathname.replace('/staff-api', '');
    const body = req.postData() ? JSON.parse(req.postData()!) : null;
    calls.push({ method: req.method(), path, body, staffHeader: req.headers()['x-nidan-staff'] });
    let m;
    if (req.method() === 'GET' && (m = path.match(/^\/groups\/([0-9a-f-]+)\/members$/))) {
      return route.fulfill({ json: users.filter((u) => u.groups.includes(m[1])) });
    }
    if (req.method() === 'POST' && path === '/users') {
      if (users.some((u) => u.username === body.username)) return route.fulfill({ status: 409, json: { errorMessage: 'exists' } });
      const g = body.groups[0] === '/staff/Nurse' ? NURSE : CASHIER;
      users.push({ id: uid(users.length + 10), username: body.username, firstName: body.firstName, lastName: body.lastName, enabled: true, groups: [g] });
      return route.fulfill({ status: 201, body: '' });
    }
    if (req.method() === 'GET' && (m = path.match(/^\/users\/([0-9a-f-]+)$/))) {
      const u = users.find((x) => x.id === m[1])!;
      return route.fulfill({ json: { id: u.id, username: u.username, firstName: u.firstName, lastName: u.lastName, enabled: u.enabled,
        attributes: u.attributes ?? {} } });
    }
    if (req.method() === 'PUT' && (m = path.match(/^\/users\/([0-9a-f-]+)$/))) {
      const u = users.find((x) => x.id === m[1])!;
      if ('enabled' in body) u.enabled = body.enabled;
      if ('attributes' in body) u.attributes = body.attributes;  // Keycloak replaces the whole map
      return route.fulfill({ status: 204, body: '' });
    }
    if (req.method() === 'PUT' && /\/reset-password$/.test(path)) return route.fulfill({ status: 204, body: '' });
    if (req.method() === 'POST' && /^\/users\/[0-9a-f-]+\/logout$/.test(path)) return route.fulfill({ status: 204, body: '' });
    if ((m = path.match(/^\/users\/([0-9a-f-]+)\/groups\/([0-9a-f-]+)$/))) {
      const u = users.find((x) => x.id === m[1])!;
      if (req.method() === 'PUT') u.groups.push(m[2]); else u.groups = u.groups.filter((g) => g !== m[2]);
      return route.fulfill({ status: 204, body: '' });
    }
    if (path === '/events') return route.fulfill({ json: [{ time: 1, type: 'LOGIN_ERROR', userId: uid(1), ipAddress: '10.0.0.5', details: { username: 'nurse1' } }] });
    if (path === '/admin-events') return route.fulfill({ json: [
      { time: 2, operationType: 'UPDATE', resourceType: 'USER', resourcePath: `users/${uid(1)}`, authDetails: { userId: 'hadmin1' } },
      { time: 3, operationType: 'UPDATE', resourceType: 'CLIENT', resourcePath: 'clients/secret', authDetails: { userId: 'operator' } }] });
    return route.fulfill({ status: 403, body: '' });
  });
  return { users, calls };
}

test('lists staff per job with in-browser search, never GET /users @KC-21-01 @KC-21-06 @security', async ({ page }) => {
  const kc = await fakeKeycloak(page);
  await page.goto(`${STAFF}/staff/`);
  await expect(page.locator('#people tbody tr')).toHaveCount(3);
  await page.fill('#search', 'hari');
  await expect(page.locator('#people tbody tr')).toHaveCount(1);
  await expect(page.locator('#people tbody tr td').nth(1)).toHaveText('cashier1');
  expect(kc.calls.every((c) => c.staffHeader === '1')).toBe(true);
  expect(kc.calls.some((c) => c.method === 'GET' && c.path === '/users')).toBe(false);
});

test('a name with markup is shown as text @KC-21-08 @security', async ({ page }) => {
  let dialog = false;
  page.on('dialog', async (d) => { if (d.type() === 'alert') dialog = true; await d.dismiss(); });
  await fakeKeycloak(page);
  await page.goto(`${STAFF}/staff/`);
  await expect(page.locator('#people tbody')).toContainText('<img src=x onerror=alert(1)> X');
  expect(await page.locator('#people img').count()).toBe(0);
  expect(dialog).toBe(false);
});

test('adding a person sends no roles or credentials and shows a temporary password once @KC-21-02 @KC-09-06 @KC-08-10 @security', async ({ page }) => {
  const kc = await fakeKeycloak(page);
  await page.goto(`${STAFF}/staff/`);
  await page.click('#add-open');
  await page.fill('input[name=firstName]', 'Gita');
  await page.fill('input[name=lastName]', 'Thapa');
  await page.fill('input[name=username]', 'gita.thapa');
  await page.selectOption('select[name=gender]', 'F');
  await page.selectOption('#add-job', NURSE);
  await page.click('#add-form button[type=submit]');
  await expect(page.locator('#slip-dialog')).toBeVisible();
  await expect(page.locator('#slip-password')).toHaveText(/^[a-z]+-[a-z]+-[a-z]+-\d{2}$/);
  const create = kc.calls.find((c) => c.method === 'POST' && c.path === '/users')!;
  expect(create.body.groups).toEqual(['/staff/Nurse']);
  expect(create.body.attributes.gender).toEqual(['F']);
  for (const k of ['realmRoles', 'clientRoles', 'credentials']) expect(create.body).not.toHaveProperty(k);
  const reset = kc.calls.find((c) => /reset-password$/.test(c.path))!;
  expect(reset.body.temporary).toBe(true);
  await page.click('#slip-close');
  await expect(page.locator('#slip-password')).toHaveText('');
});

test('reserved, invalid or missing fields create nothing @KC-21-03 @security', async ({ page }) => {
  const kc = await fakeKeycloak(page);
  await page.goto(`${STAFF}/staff/`);
  await page.click('#add-open');
  await page.fill('input[name=firstName]', 'A');
  await page.fill('input[name=lastName]', 'B');
  await page.selectOption('select[name=gender]', 'M');
  await page.selectOption('#add-job', NURSE);
  for (const name of ['admin', 'nidan-sync', '3-4']) {
    await page.fill('input[name=username]', name);
    await page.evaluate(() => (document.getElementById('add-form') as HTMLFormElement).requestSubmit());
    await expect(page.locator('#add-error')).toContainText(/reserved|Username/);
  }
  await page.fill('input[name=username]', 'nurse1');
  await page.evaluate(() => (document.getElementById('add-form') as HTMLFormElement).requestSubmit());
  await expect(page.locator('#add-error')).toContainText('already exists');
  expect(kc.calls.filter((c) => c.method === 'POST').length).toBe(1); // only the duplicate reached the API
});

test('disable names the 6-minute window, then enable again @KC-22-02', async ({ page }) => {
  const kc = await fakeKeycloak(page);
  let text = '';
  page.on('dialog', async (d) => { text = d.message(); await d.accept(); });
  await page.goto(`${STAFF}/staff/`);
  await page.locator('#people tbody tr', { hasText: 'nurse1' }).getByRole('button', { name: 'Disable' }).click();
  await expect(page.locator('#people tbody tr', { hasText: 'nurse1' })).toContainText('Disabled');
  expect(text).toContain('6 minutes');
  expect(kc.calls.find((c) => c.method === 'PUT' && c.path === `/users/${uid(1)}`)!.body).toEqual({ enabled: false });
  // KC-11-02: sessions are ended (back-channel logout to the apps) BEFORE the account is disabled
  const order = kc.calls.map((c) => `${c.method} ${c.path}`);
  expect(order.indexOf(`POST /users/${uid(1)}/logout`)).toBeGreaterThan(-1);
  expect(order.indexOf(`POST /users/${uid(1)}/logout`)).toBeLessThan(order.indexOf(`PUT /users/${uid(1)}`));
  await page.locator('#people tbody tr', { hasText: 'nurse1' }).getByRole('button', { name: 'Enable' }).click();
  await expect(page.locator('#people tbody tr', { hasText: 'nurse1' })).toContainText('Active');
  expect(kc.calls.filter((c) => c.path === `/users/${uid(1)}/logout`).length, 'enabling signs nobody out').toBe(1);
});

test('reset password: temporary, word-based, printable slip @KC-22-01', async ({ page }) => {
  const kc = await fakeKeycloak(page);
  await page.goto(`${STAFF}/staff/`);
  await page.locator('#people tbody tr', { hasText: 'cashier1' }).getByRole('button', { name: 'Reset password' }).click();
  await expect(page.locator('#slip-password')).toHaveText(/^[a-z]+-[a-z]+-[a-z]+-\d{2}$/);
  await expect(page.locator('#slip')).toContainText('७ दिन');
  expect(kc.calls.find((c) => /reset-password$/.test(c.path))!.body).toMatchObject({ type: 'password', temporary: true });
});

test('change job: added to the new group, removed from the old @KC-21-04', async ({ page }) => {
  const kc = await fakeKeycloak(page);
  await page.goto(`${STAFF}/staff/`);
  await page.locator('#people tbody tr', { hasText: 'nurse1' }).locator('select').selectOption(CASHIER);
  await expect(page.locator('#notice')).toContainText('nurse1 is now Cashier');
  expect(kc.calls.some((c) => c.method === 'PUT' && c.path === `/users/${uid(1)}/groups/${CASHIER}`)).toBe(true);
  expect(kc.calls.some((c) => c.method === 'DELETE' && c.path === `/users/${uid(1)}/groups/${NURSE}`)).toBe(true);
});

test('activity tab is read-only and hides client/realm config events @KC-23-03 @KC-23-04 @security', async ({ page }) => {
  await fakeKeycloak(page);
  await page.goto(`${STAFF}/staff/`);
  await page.click('.tab[data-tab=activity]');
  await expect(page.locator('#events tbody tr')).toHaveCount(2);
  await expect(page.locator('#events')).toContainText('LOGIN_ERROR');
  await expect(page.locator('#events')).not.toContainText('clients/secret');
  expect(await page.locator('#tab-activity button').count()).toBe(0);
});

test('the Staff page runs under the strict CSP with no violations @KC-20 @security', async ({ page }) => {
  await fakeKeycloak(page);
  const violations: string[] = [];
  page.on('console', (m) => { if (/Content Security Policy|Refused to/i.test(m.text())) violations.push(m.text()); });
  const res = await page.goto(`${STAFF}/staff/`);
  expect(res!.headers()['content-security-policy']).toContain("default-src 'self'");
  await expect(page.locator('#people tbody tr')).toHaveCount(3);
  expect(violations).toEqual([]);
});

// KC-22-03 (page part; the forced call -> 403 is realm.spec KC-09): an admin is never listed, so no reset or disable
// is ever offered on her, and the page does not even read the admin group's members.
test('admins are never listed, so no reset or disable is offered on them @KC-22-03 @security', async ({ page }) => {
  const kc = await fakeKeycloak(page);
  kc.users.push({ id: uid(9), username: 'hadmin2', firstName: 'Gita', lastName: 'Admin', enabled: true, groups: [ADMIN_GROUP] });
  await page.goto(`${STAFF}/staff/`);
  await expect(page.locator('#people tbody tr')).toHaveCount(3);
  await expect(page.locator('#people')).not.toContainText('hadmin2');
  await expect(page.locator('button', { hasText: /hadmin2|Gita/ })).toHaveCount(0);
  expect(kc.calls.some((c) => c.path === `/groups/${ADMIN_GROUP}/members`), 'admin group members never fetched').toBe(false);
});

test('the job pickers offer only /staff/* jobs, never an admin group @KC-21-09 @security', async ({ page }) => {
  await fakeKeycloak(page);
  await page.goto(`${STAFF}/staff/`);
  await expect(page.locator('#people tbody tr').first()).toBeVisible();
  for (const sel of ['#job-filter', '#add-job', 'select[aria-label^="Change job for"]']) {
    const options = await page.locator(sel).first().locator('option').allTextContents();
    expect(options, sel).not.toContain('Hospital Admins');
    expect(options, sel).toEqual(expect.arrayContaining(['Nurse', 'Cashier']));
  }
});

test('places (locations, lab units, POS shops) are saved as attributes, nothing else changes @KC-21-05 @security', async ({ page }) => {
  const kc = await fakeKeycloak(page);
  await page.goto(`${STAFF}/staff/`);
  const row = page.locator('#people tbody tr', { hasText: 'nurse1' });
  await row.getByRole('button', { name: 'Places' }).click();
  const form = page.locator('#places-form');
  await expect(form.locator('[name=nidan_locations]')).toHaveValue('Ward A');
  await expect(form.locator('[name=nidan_shops]')).toHaveValue('Old Shop');

  await form.locator('[name=nidan_shops]').fill('Main Pharmacy, <b>x</b>');
  await form.getByRole('button', { name: 'Save' }).click();
  await expect(page.locator('#places-error')).toBeVisible();
  expect(kc.calls.filter((c) => c.method === 'PUT')).toHaveLength(0);

  await form.locator('[name=nidan_shops]').fill('Main Pharmacy, Emergency POS, Main Pharmacy');
  await form.locator('[name=nidan_lab_units]').fill('Haematology');
  await form.locator('[name=nidan_locations]').fill('');
  await form.getByRole('button', { name: 'Save' }).click();
  await expect(page.locator('#notice')).toContainText('apply at the next login');

  const put = kc.calls.find((c) => c.method === 'PUT' && c.path === `/users/${uid(1)}`)!;
  expect(put.staffHeader).toBe('1');
  expect(Object.keys(put.body).sort()).toEqual(['attributes', 'firstName', 'lastName', 'username']);
  expect(put.body.attributes).toEqual({ gender: ['F'], nidan_shops: ['Main Pharmacy', 'Emergency POS'], nidan_lab_units: ['Haematology'] });
  expect(kc.users[0].attributes!.gender).toEqual(['F']);
});

test('Activity shows login errors and user changes with who did them @KC-23-01 @KC-23-02 @security', async ({ page }) => {
  await fakeKeycloak(page);
  await page.goto(`${STAFF}/staff/`);
  await page.getByRole('button', { name: 'Activity' }).click();
  const rows = page.locator('#events tbody tr');
  await expect(rows.filter({ hasText: 'LOGIN_ERROR' })).toContainText('nurse1');
  await expect(rows.filter({ hasText: 'UPDATE USER' })).toContainText('hadmin1');
});

test.describe('Staff page states @KC-21-07', () => {
  const config = (page: Page, body: unknown, status = 200) => page.route(`${STAFF}/staff/staff-config.json`,
    (r) => r.fulfill({ status, contentType: 'application/json', body: JSON.stringify(body) }));
  const jobs = { jobs: [{ name: 'Nurse', id: NURSE, path: '/staff/Nurse' }] };

  test('loading, then no staff found', async ({ page }) => {
    await config(page, jobs);
    let release!: () => void;
    const gate = new Promise<void>((r) => { release = r; });
    await page.route(`${STAFF}/staff-api/**`, async (r) => { await gate; await r.fulfill({ json: [] }); });
    await page.goto(`${STAFF}/staff/`);
    await expect(page.locator('#list-state')).toHaveText('Loading…');
    release();
    await expect(page.locator('#list-state')).toHaveText('No staff found.');
    await expect(page.locator('#people')).toBeHidden();
  });

  for (const [status, text] of [[403, 'You are not allowed to manage staff.'], [500, 'Could not load staff. Try again.'],
    [502, 'Could not load staff. Try again.']] as const) {
    test(`staff-api ${status} -> "${text}"`, async ({ page }) => {
      await config(page, jobs);
      await page.route(`${STAFF}/staff-api/**`, (r) => r.fulfill({ status, body: '' }));
      await page.goto(`${STAFF}/staff/`);
      await expect(page.locator('#list-state')).toHaveText(text);
    });
  }

  test('an ended session offers sign-in and comes back to the Staff page', async ({ page }) => {
    await config(page, jobs);
    await page.route(`${STAFF}/staff-api/**`, (r) => r.fulfill({ status: 401, body: '' }));
    await page.goto(`${STAFF}/staff/`);
    await expect(page.locator('#list-state')).toContainText('Your session has ended.');
    await expect(page.locator('#list-state a')).toHaveAttribute('href', '/oauth2/start?rd=/staff/');
  });

  test('missing configuration is said plainly', async ({ page }) => {
    await config(page, { error: 'not found' }, 404);
    await page.goto(`${STAFF}/staff/`);
    await expect(page.locator('#list-state')).toHaveText('Staff configuration is missing (staff-config.json).');
  });

  test('a site with no jobs set up says so instead of "no staff"', async ({ page }) => {
    await config(page, { jobs: [] });
    await page.goto(`${STAFF}/staff/`);
    await expect(page.locator('#list-state')).toContainText('No jobs are set up for this hospital yet');
  });
});
