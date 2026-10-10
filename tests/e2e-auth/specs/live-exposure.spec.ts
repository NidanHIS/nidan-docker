import { expect, test } from '@playwright/test';
import { basicAuth, contractRoutes, dockerExec, httpBase, rawGet, tcpOpen, tlsHandshake } from './helpers';

/**
 * Epic E-1 (live exposure fixes). Title tags are backlog AC ids; every test here is @security.
 * Tests for ACs still waiting on a user decision are test.fixme() with the reason.
 */

// ---------------------------------------------------------------- KC-53 /cis/ narrowed
test.describe('KC-53 internal APIs are not reachable through the gateway', () => {
  for (const path of [
    '/cis/openelis/lab-result',
    '/cis/openelis/sample-collected',
    '/cis/openelis/catalog/lab-test-definition',
    '/cis/orthanc/webhook',
    '/cis/actuator/env',
    '/cis/api/dead-letters',
    '/cis/anything',
    '/cis/dead-letters/../openelis/lab-result',
    '/cis/dead-letters/%2e%2e/openelis/lab-result',
    '/cis/dead-letters/..%2fopenelis/lab-result',
  ]) {
    test(`POST ${path} -> refused at the gateway @KC-53-01 @security`, async ({ request }) => {
      const res = await request.post(path, { headers: { 'X-Nidan-Webhook-Secret': 'change-me' }, maxRedirects: 0 });
      // 404 from the /cis/ allow-list, or 400 from the path-trick guard (KC-56-08); never an upstream answer
      expect([400, 404]).toContain(res.status());
    });
  }
});

// ---------------------------------------------------------------- KC-51 fail-closed webhook secrets
test('lab-result webhook with the old default secret is refused @KC-51-02 @security', async ({ request }) => {
  const res = await request.post('/cis/openelis/lab-result', {
    headers: { 'X-Nidan-Webhook-Secret': 'change-me', 'Content-Type': 'application/json' },
    data: '{"resourceType":"Bundle"}',
  });
  expect([401, 404]).toContain(res.status());
});

// ---------------------------------------------------------------- KC-53-06 dead letters
test.describe('KC-53-06 dead-letter page and API need a credential', () => {
  for (const path of ['/api/dead-letters', '/dead-letters/index.html', '/cis/dead-letters/index.html']) {
    test(`GET ${path} without credentials -> 401 @KC-53-06 @security @needs-app`, async ({ request }) => {
      const res = await request.get(path, { maxRedirects: 0 });
      expect(res.status()).toBe(401);
      expect(res.headers()['www-authenticate'] ?? '').toContain('Basic');
    });
  }

  test('wrong admin password -> 401, wrong secret -> 401 @KC-53-06 @security @needs-app', async ({ request }) => {
    expect((await request.get('/api/dead-letters', { headers: { Authorization: basicAuth('dead-letter-admin', 'wrong') } })).status()).toBe(401);
    expect((await request.get('/api/dead-letters', { headers: { 'X-Nidan-Webhook-Secret': 'sample' } })).status()).toBe(401);
  });

  test('dead-letter admin can list @KC-53-06 @security', async ({ request }) => {
    const password = process.env.DEAD_LETTER_ADMIN_PASSWORD;
    test.skip(!password, 'set DEAD_LETTER_ADMIN_PASSWORD to the site value');
    const res = await request.get('/api/dead-letters', { headers: { Authorization: basicAuth('dead-letter-admin', password!) } });
    expect(res.status()).toBe(200);
  });
});

// ---------------------------------------------------------------- KC-14-03 Orthanc webhook secret (internal)
test('Orthanc webhook without its secret is refused on the internal network @KC-14-03 @security', async () => {
  let status: string;
  try {
    status = dockerExec('nidan-cis', ['sh', '-c',
      'curl -s -o /dev/null -w "%{http_code}" -X POST -H "Content-Type: application/json" -d "{}" http://localhost:8081/orthanc/webhook'
      + ' || wget -q -S -O /dev/null --post-data="{}" http://localhost:8081/orthanc/webhook 2>&1 | awk "/HTTP\\//{print \\$2}"']).trim();
  } catch {
    test.skip(true, 'stack not running (docker compose exec failed)');
    return;
  }
  expect(status).toContain('401');
});

// ---------------------------------------------------------------- KC-06 Secure cookies
test('every Set-Cookie through the TLS gateway carries Secure @KC-06-01 @security', async ({ request }) => {
  const res = await request.get('/openmrs/ws/rest/v1/session');
  const cookies = res.headersArray().filter((h) => h.name.toLowerCase() === 'set-cookie').map((h) => h.value);
  test.skip(cookies.length === 0, 'upstream set no cookie on this route');
  for (const c of cookies) expect(c, c.split(';')[0].split('=')[0]).toMatch(/;\s*Secure/i);
});

// ---------------------------------------------------------------- KC-03 Superset
test.describe('KC-03 Superset lockdown', () => {
  test('no CORS for foreign origins @KC-03-06 @security', async ({ request }) => {
    for (const origin of ['https://evil.example', 'https://localhost:8069']) {
      const res = await request.get('/superset/api/v1/dashboard/', { headers: { Origin: origin } });
      expect(res.headers()['access-control-allow-origin'], origin).toBeUndefined();
      expect(res.headers()['access-control-allow-credentials'], origin).toBeUndefined();
    }
  });

  test('session cookie is Secure, HttpOnly, Path=/superset @KC-03-08 @security', async ({ request }) => {
    const res = await request.get('/superset/login/');
    const cookie = res.headersArray().find((h) => h.name.toLowerCase() === 'set-cookie' && h.value.startsWith('superset_session='));
    test.skip(!cookie, 'Superset did not set a session cookie on /login/');
    expect(cookie!.value).toMatch(/;\s*Secure/i);
    expect(cookie!.value).toMatch(/;\s*HttpOnly/i);
    expect(cookie!.value).toMatch(/;\s*Path=\/superset/i);
  });

  test('anonymous visitors see no dashboard list @KC-03-01 @security @needs-app', async ({ request }) => {
    const res = await request.get('/superset/api/v1/dashboard/', { maxRedirects: 0 });
    expect([401, 403, 302]).toContain(res.status());
  });
});

// ---------------------------------------------------------------- KC-04-04 no third-party script
test('launcher loads nothing from another origin @KC-04-04 @security', async ({ request }) => {
  test.skip(!!process.env.GATEWAY_ANALYTICS_ID, 'analytics deliberately enabled on this deployment');
  const html = await (await request.get('/')).text();
  const external = [...html.matchAll(/\b(?:src|href)=["'](https?:)?\/\/[^"']+/g)].map((m) => m[0])
    .filter((u) => !u.includes('trigonaltechnology.com')); // footer link, not a loaded resource
  expect(external.filter((u) => u.startsWith('src'))).toEqual([]);
});

// ---------------------------------------------------------------- KC-56-08 path tricks
test.describe('KC-56-08 path tricks are rejected before any upstream', () => {
  for (const path of ['/openmrs/..;/admin', '/openmrs/%2e%2e/admin', '/openmrs/a%2fb', '/openmrs/a%5cb',
      '//openmrs/x', '/openmrs//x', '/superset/..;/admin', '/api/OpenELIS-Global/..;/x']) {
    test(`GET ${path} -> 400 @KC-56-08 @security`, async ({ baseURL }) => {
      expect(await rawGet(baseURL!, path)).toBe(400);
    });
  }
  test('a // inside the query string is not a path trick @KC-56-08 @security', async ({ baseURL }) => {
    expect(await rawGet(baseURL!, '/openmrs/x?redirect=https://a//b')).not.toBe(400);
  });
});

// ---------------------------------------------------------------- KC-12 gateway hardening
test.describe('KC-12 gateway pages and TLS', () => {
  test('gateway pages send the security headers and no server version @KC-12-01 @security', async ({ request }) => {
    const res = await request.get('/');
    const h = res.headers();
    expect(h['x-frame-options']).toBe('DENY');
    expect(h['x-content-type-options']).toBe('nosniff');
    expect(h['referrer-policy']).toBe('strict-origin-when-cross-origin');
    expect(h['content-security-policy']).toContain("frame-ancestors 'none'");
    expect(h['server'] ?? '').not.toMatch(/\d/);
  });

  for (const [label, opts, ok] of [
    ['TLS 1.2 ECDHE-AES-GCM', { maxVersion: 'TLSv1.2', ciphers: 'ECDHE-RSA-AES128-GCM-SHA256:ECDHE-ECDSA-AES128-GCM-SHA256' }, true],
    ['TLS 1.3', { minVersion: 'TLSv1.3' }, true],
    ['TLS 1.2 CBC (ECDHE-RSA-AES128-SHA)', { maxVersion: 'TLSv1.2', ciphers: 'ECDHE-RSA-AES128-SHA:ECDHE-ECDSA-AES128-SHA' }, false],
    ['TLS 1.2 non-forward-secret (AES128-SHA)', { maxVersion: 'TLSv1.2', ciphers: 'AES128-SHA:AES128-GCM-SHA256' }, false],
    ['TLS 1.1', { minVersion: 'TLSv1.1', maxVersion: 'TLSv1.1', ciphers: 'DEFAULT@SECLEVEL=0' }, false],
  ] as const) {
    test(`${label} ${ok ? 'accepted' : 'refused'} @KC-12-03 @security`, async ({ baseURL }) => {
      expect(await tlsHandshake(baseURL!, opts as object)).toBe(ok);
    });
  }
});

// ---------------------------------------------------------------- KC-47 port 80
test.describe('KC-47 port 80 serves redirects and ACME only', () => {
  test.fixme(true, 'needs the user to approve removing the port-80 localhost server block (KC-47)');
  for (const host of ['localhost', '127.0.0.1', 'evil.example']) {
    test(`Host: ${host} on :80 never proxies an app @KC-47-01 @security`, async ({ request }) => {
      for (const path of ['/openmrs/', '/orthanc-container/', '/api/dead-letters', '/superset/']) {
        const res = await request.get(httpBase + path, { headers: { Host: host }, maxRedirects: 0 });
        // 421 = unknown Host refused outright (KC-56-06); either way no app is proxied
        expect([301, 308, 421], `${host}${path}`).toContain(res.status());
      }
    });
  }
});

// ---------------------------------------------------------------- KC-02 published ports
test.describe('KC-02 only gateway ports answer from the LAN', () => {
  const lan = process.env.LAN_HOST;
  test('DB ports are closed @KC-02-02 @security', async () => {
    test.skip(!lan, 'set LAN_HOST to probe from outside the Docker network');
    for (const port of [3307, 15432]) expect(await tcpOpen(lan!, port), `port ${port}`).toBe(false);
  });
  test('other service ports are closed @KC-02-02 @security', async () => {
    test.skip(!lan, 'set LAN_HOST to probe from outside the Docker network');
    for (const port of [8042, 8081, 8082, 8083, 8085, 8088, 5003, 8180]) expect(await tcpOpen(lan!, port), `port ${port}`).toBe(false);
  });
  // KC-02-01: a full TCP scan shows only the gateway's ports and DICOM (4242, when DICOM_BIND_IP is this address)
  test('full TCP scan: only 80, 443, 8443, 8069, 9443 (and 4242) answer @KC-02-01 @security', async () => {
    test.skip(!lan, 'set LAN_HOST to probe from outside the Docker network');
    test.setTimeout(10 * 60_000);
    const open: number[] = [];
    const ports = Array.from({ length: 65535 }, (_, i) => i + 1);
    for (let i = 0; i < ports.length; i += 1000) {   // 1000 connects at a time, 400 ms each
      const batch = ports.slice(i, i + 1000);
      const res = await Promise.all(batch.map((p) => tcpOpen(lan!, p, 400)));
      batch.forEach((p, k) => { if (res[k]) open.push(p); });
    }
    expect(open.filter((p) => ![80, 443, 8443, 8069, 9443, 4242].includes(p)), `open: ${open}`).toEqual([]);
    expect(open, 'the gateway itself answers').toEqual(expect.arrayContaining([80, 443]));
  });
});

// ---------------------------------------------------------------- KC-10-01 contract routes gated (SSO on)
test.describe('KC-10-01 every gated contract route refuses API calls without a session', () => {
  const gated = contractRoutes().filter((r) => !['exempt', 'deny'].includes(r.gate) && r.server === 'main'
      && r.match !== 'regex' && r.kind !== 'redirect');
  for (const r of [...new Map(gated.map((g) => [g.path, g])).values()]) {
    test(`${r.path} (${r.gate}) -> 401 @KC-10-01 @security @sso-on-only`, async ({ request }) => {
      const res = await request.get(r.path, { maxRedirects: 0, headers: { 'Sec-Fetch-Mode': 'cors', Accept: 'application/json' } });
      expect(res.status()).toBe(401);
    });
  }
});
