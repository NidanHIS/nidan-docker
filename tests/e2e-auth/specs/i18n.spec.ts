import { readFileSync } from 'node:fs';
import { join } from 'node:path';
import { expect, test } from '@playwright/test';
import { composeDir } from './helpers';

/** Gateway page languages (backlog KC-18). */
const msgs = (loc: string) => JSON.parse(readFileSync(join(composeDir, 'proxy', 'assets', 'i18n', `messages_${loc}.json`), 'utf8'));
const EN = msgs('en');
const NE = msgs('ne');

test('en and ne have the same keys @KC-18-03', () => {
  const ne = { ...NE };
  delete ne._review;
  expect(Object.keys(ne).sort()).toEqual(Object.keys(EN).sort());
  for (const [k, v] of Object.entries(ne)) expect(String(v).trim(), k).not.toBe('');
});

test('choosing नेपाली sets the shared cookie and the page reloads in Nepali @KC-18-01', async ({ page, context }) => {
  await page.goto('/?launcher');
  await page.selectOption('#locale-picker', 'ne');
  await expect(page.locator('html')).toHaveAttribute('lang', 'ne');
  await expect(page.locator('.hero-title')).toHaveText(NE['launcher.title']);
  const cookie = (await context.cookies()).find((c) => c.name === 'nidan_locale');
  expect(cookie?.value).toBe('ne');
  expect(cookie?.path).toBe('/');
});

test.describe('with nidan_locale=ne no gateway page shows English @KC-18-02', () => {
  test.beforeEach(async ({ context, baseURL }) => {
    await context.addCookies([{ name: 'nidan_locale', value: 'ne', url: baseURL! }]);
  });
  for (const path of ['/?launcher', '/not-allowed.html?app=openelis', '/outage.html']) {
    test(`${path}`, async ({ page }) => {
      await page.route('**/oauth2/userinfo', (r) => r.fulfill({ contentType: 'application/json',
        body: JSON.stringify({ user: 'nurse1', preferredUsername: 'nurse1', groups: ['role:nidan-gateway:app-openmrs'] }) }));
      await page.goto(path);
      await expect(page.locator('html')).toHaveAttribute('lang', 'ne');
      const els = page.locator('[data-i18n]');
      const n = await els.count();
      expect(n).toBeGreaterThan(0);
      for (let i = 0; i < n; i++) {
        const key = await els.nth(i).getAttribute('data-i18n');
        // the not-allowed title is replaced by the app-specific one (asserted below)
        if (key === 'na.titleGeneric' && path.includes('app=')) continue;
        await expect(els.nth(i), key!).toHaveText(NE[key!]);
      }
      // and nothing untagged: no English word on screen beyond product names and acronyms
      const ALLOW = /^(Nidan|NidanEHR|EHR|OpenMRS|Odoo|OpenELIS|Global|Orthanc|OHIF|Superset|DHIS2|HMIS|ERP|PACS|Explorer|Trigonal|Technology|IT|CIS|POS|OTP|English)$/;
      // the user's own name is data; the language picker lists each language in its own name (Español, Français)
      let text = (await page.locator('body').innerText()).replaceAll('nurse1', '');
      for (const name of await page.locator('#locale-picker option').allInnerTexts()) text = text.replaceAll(name, '');
      const english = [...new Set((text.match(/[A-Za-z][A-Za-z'’-]+/g) ?? []).filter((w) => !ALLOW.test(w)))];
      expect(english, `English left on ${path}`).toEqual([]);
      if (path.startsWith('/?launcher')) await expect(page.locator('#identity-line')).toContainText('को रूपमा लगइन गरिएको');
      if (path.startsWith('/not-allowed')) await expect(page.locator('#np-title')).toHaveText(`तपाईंलाई ${NE['app.openelis']} चलाउने अनुमति छैन`);
    });
  }
});
