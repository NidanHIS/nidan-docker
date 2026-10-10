import { test, expect } from '@playwright/test';
import { execFileSync } from 'node:child_process';
import { readFileSync } from 'node:fs';

/**
 * Port 80 after KC-47 (the plain-http "localhost" full-proxy server is gone): on tests/e2e-auth/harness/sso-openmrs.yml.
 * The harness does not publish :80, so a container on the harness network stands in for the LAN probe. Runs when
 * OPENMRS_SSO_FULL is set.
 */
const FULL = !!process.env.OPENMRS_SSO_FULL;
const P = 'nidan-sso-openmrs-harness';

test.describe('port 80: redirect and ACME only', () => {
  test.skip(!FULL, 'needs tests/e2e-auth/harness/sso-openmrs.yml (OPENMRS_SSO_FULL=1)');

  // KC-47-01: every contract path, with every Host a client might send, gets a redirect to https on the same host (or
  // 421 for a host that is not ours, 400 for a path trick), never an app answer; ACME paths are files only.
  test('every route and Host: 301 to https, 421, or an ACME file; never an app @KC-47-01 @security @sso-on-only', () => {
    const routes = readFileSync(`${__dirname}/../../../proxy/routes.yaml`, 'utf-8');
    const paths = [...new Set([...routes.matchAll(/path: "?([^",}]+)"?/g)].map((m) => m[1].trim())
      .filter((p) => p.startsWith('/')).map((p) => p.replace(/\(.*$/, '').replace(/[$^\\]/g, '') || '/'))];
    expect(paths.length).toBeGreaterThan(40);
    const py = 'import http.client, json, sys\n' +
      'out = []\n' +
      'for path in json.loads(sys.argv[1]):\n' +
      '    for host in ["localhost", "127.0.0.1", "evil.example", None]:\n' +
      '        c = http.client.HTTPConnection("gateway", 80, timeout=5)\n' +
      '        c.putrequest("GET", path, skip_host=True)\n' +
      '        if host: c.putheader("Host", host)\n' +
      '        c.endheaders(); r = c.getresponse()\n' +
      '        out.append([path, host, r.status, r.getheader("Location") or ""]); c.close()\n' +
      'print(json.dumps(out))\n';
    const rows = JSON.parse(execFileSync('docker', ['exec', `${P}-upstreams-1`, 'python3', '-c', py, JSON.stringify(paths)]).toString()) as
      Array<[string, string | null, number, string]>;
    const bad = rows.filter(([path, host, status, location]) => {
      if (path.startsWith('/.well-known/acme-challenge/')) return ![404, 200, 421, 400].includes(status) || location !== '';   // certbot sends our own Host
      if (status === 301) return !host || host === 'evil.example' || location !== `https://${host}${path}`;
      return ![421, 400].includes(status);
    });
    expect(bad, JSON.stringify(bad.slice(0, 5))).toEqual([]);
  });

  // KC-47-03: the container healthcheck uses the internal listener (never published), which answers; from the host
  // that port is closed.
  test('healthcheck on the internal listener, which the host cannot reach @KC-47-03 @security @sso-on-only', () => {
    const compose = readFileSync(`${__dirname}/../../../docker-compose.yml`, 'utf-8');
    expect(compose).toContain('"http://localhost:8099/gateway-health"');
    expect(execFileSync('docker', ['exec', `${P}-gateway-1`, 'curl', '-fsS', '-o', '/dev/null', '-w', '%{http_code}',
      'http://localhost:8099/gateway-health']).toString()).toBe('204');
    const fromHost = execFileSync('sh', ['-c', 'curl -s -o /dev/null -w "%{http_code}" --max-time 3 http://127.0.0.1:8099/gateway-health; true']).toString();
    expect(fromHost, 'not published').toBe('000');
  });
});
