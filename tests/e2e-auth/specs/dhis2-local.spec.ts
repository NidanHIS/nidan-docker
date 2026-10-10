import { test, expect } from '@playwright/test';

/**
 * The DHIS2 reporting app with its own local login (AUTH_PROVIDER=env_file), as it runs with central login off:
 * tests/e2e-auth/harness/sso-openmrs.yml --profile dhis2, service dhis2-local on 127.0.0.1:15003 (harness only).
 * Runs when DHIS2_LOCAL is set.
 */
const FULL = !!process.env.DHIS2_LOCAL;
const APP = 'http://127.0.0.1:15003';

test.describe('DHIS2 app, local login (central login off)', () => {
  test.skip(!FULL, 'needs tests/e2e-auth/harness/sso-openmrs.yml --profile dhis2 (DHIS2_LOCAL=1)');

  // KC-40-03
  test('the local provider signs in the configured admin and refuses a wrong password @KC-40-03 @sso-off-only', async ({ page }) => {
    await page.goto(`${APP}/auth/login`);
    await page.fill('input[name="username"]', 'admin');
    await page.fill('input[name="password"]', 'not-the-password-123456');
    await page.click('form[action^="/auth/login"] button[type="submit"]');
    await expect(page.getByText('Invalid username or password.').first()).toBeVisible();
    expect(new URL(page.url()).pathname).toBe('/auth/login');

    await page.fill('input[name="username"]', 'admin');
    await page.fill('input[name="password"]', 'Harness-Dhis2-Admin-0123456789');
    await page.click('form[action^="/auth/login"] button[type="submit"]');
    await page.waitForLoadState();
    expect(new URL(page.url()).pathname, 'signed in: off the login page').not.toBe('/auth/login');
    await expect(page.locator('form[action*="/auth/logout"]'), 'a sign-out form is shown').toHaveCount(1);
  });
});
