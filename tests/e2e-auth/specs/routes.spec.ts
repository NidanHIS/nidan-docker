import { test, expect } from '@playwright/test';
import { httpBase, oeBase, rawGet, rawResponse } from './helpers';

// KC-10-03: unknown paths are 404 on every server and never reach an upstream or the launcher.
// Case variants (/OPENMRS/) are 404 on Linux only: nginx on macOS matches prefixes case-insensitively,
// so they are skipped when the gateway under test runs on darwin (GATEWAY_OS=darwin).
const UNKNOWN = ['/x', '/index.html', '/assets/nope.js', '/openmrsx', '/.env', '/web/login'];
const CASE = ['/OPENMRS/', '/OpenMRS/spa', '/Superset/'];

test('unknown paths are 404 on :443 and :8443 @KC-10-03 @security', async ({ baseURL }) => {
  for (const base of [baseURL!, oeBase]) {
    for (const p of UNKNOWN) {
      expect(await rawGet(base, p), `${base}${p}`).toBe(404);
    }
  }
});

test('case variants of app paths are 404 @KC-10-03 @security', async ({ baseURL }) => {
  test.skip(process.env.GATEWAY_OS === 'darwin', 'nginx on macOS matches prefixes case-insensitively');
  for (const p of CASE) {
    expect(await rawGet(baseURL!, p), p).toBe(404);
  }
});

test('the launcher and its files are still served @KC-10-03 @security', async ({ request }) => {
  for (const p of ['/', '/?launcher', '/assets/launcher.js', '/not-allowed.html', '/outage.html', '/trigonal-logo.png']) {
    expect((await request.get(p, { maxRedirects: 0 })).status(), p).toBe(200);
  }
});

// KC-56-06: a forged Host is refused before anything is proxied or redirected, on every server; the
// redirects nginx makes itself are relative.
test('a forged Host gets 421 and never shows up in a Location @KC-56-06 @security', async ({ baseURL }) => {
  for (const [base, path] of [[baseURL!, '/openmrs/'], [oeBase, '/OpenELIS-Global/x'], [httpBase, '/']] as const) {
    const r = await rawResponse(base, path, 'evil.example');
    expect(r.status, `${base}${path}`).toBe(421);
    expect(r.location).not.toContain('evil.example');
  }
  const own = await rawResponse(baseURL!, '/openelis');
  expect(own.location.startsWith('/'), own.location).toBe(true);
});
