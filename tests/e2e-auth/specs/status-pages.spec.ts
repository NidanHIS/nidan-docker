import { expect, test } from '@playwright/test';

/** Not-allowed and outage pages (backlog KC-16). */

test('not-allowed names the app and the person and offers a way out @KC-16-01 @KC-16-02', async ({ page }) => {
  await page.route('**/oauth2/userinfo', (r) => r.fulfill({ contentType: 'application/json',
    body: JSON.stringify({ user: 'nurse1', preferredUsername: 'nurse1', groups: [] }) }));
  await page.goto('/not-allowed.html?app=openelis');
  await expect(page.locator('#np-title')).toHaveText("You don't have access to Lab");
  await expect(page.locator('#np-who')).toHaveText('You are signed in as nurse1.');
  await expect(page.locator('body')).toContainText('ask your hospital administrator');
  await expect(page.getByRole('link', { name: 'Back to your apps' })).toHaveAttribute('href', '/?launcher');
  const sw = page.locator('#np-switch');
  await expect(sw).toHaveText('Not nurse1? Sign in as someone else');
  await expect(sw).toHaveAttribute('href', '/oauth2/sign_out');
});

test('the app name never comes from the URL @KC-16-04 @security', async ({ page }) => {
  let dialog = false;
  page.on('dialog', async (d) => { dialog = true; await d.dismiss(); });
  for (const app of ['<img src=x onerror=alert(1)>', '__proto__', 'constructor', 'toString', 'Lab']) {
    await page.goto('/not-allowed.html?app=' + encodeURIComponent(app));
    await expect(page.locator('#np-title')).toHaveText("You don't have access to this application");
  }
  expect(await page.locator('img[src="x"]').count()).toBe(0);
  expect(dialog).toBe(false);
});

test('outage page: whole-hospital message, paper downtime, support phone @KC-16-03', async ({ page }) => {
  test.skip(!process.env.SUPPORT_PHONE, 'render the page with SUPPORT_PHONE set');
  await page.goto('/outage.html');
  await expect(page.locator('body')).toContainText('whole hospital');
  await expect(page.locator('body')).toContainText('paper downtime');
  await expect(page.locator('#outage-phone a')).toHaveText(process.env.SUPPORT_PHONE!);
  await expect(page.locator('#outage-phone a')).toHaveAttribute('href', /^tel:\+?\d+$/);
});

test('outage page returns to the original page once login answers again @KC-16-03', async ({ page }) => {
  await page.clock.install();
  let up = false;
  await page.route('**/openmrs/spa/home', (r) => r.fulfill(up ? { contentType: 'text/html', body: '<title>O3</title>' }
    : { status: 502, body: '' }));
  await page.goto('/outage.html?rd=' + encodeURIComponent('/openmrs/spa/home'));
  await page.clock.runFor(31_000);
  await expect(page.locator('#outage-retry')).toContainText('Still not responding');
  up = true;
  await page.clock.runFor(30_000);
  await page.waitForURL('**/openmrs/spa/home');
});

test('outage page never redirects off-site @KC-16-03 @security', async ({ page, baseURL }) => {
  await page.clock.install();
  await page.goto('/outage.html?rd=' + encodeURIComponent('//evil.example/phish'));
  await page.clock.runFor(31_000);
  await page.waitForTimeout(300);
  expect(new URL(page.url()).origin).toBe(new URL(baseURL!).origin);
});
