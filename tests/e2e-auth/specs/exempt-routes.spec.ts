import { test, expect } from '@playwright/test';
import { contractRoutes, oeBase, rawResponse } from './helpers';

// KC-14-01 (with KC-01-06): with central login on and NO session, every exempt route of the contract is served
// (never a 401 from the gate, never a redirect to /oauth2/start) and every gated route answers 401 to an
// API-style request. Driven by proxy/routes.yaml, so a new route is covered as soon as it is in the contract.

const SERVERS: Record<string, () => string> = { main: () => process.env.GATEWAY_URL ?? 'https://localhost', 'oe-api': () => oeBase };

/** A concrete path for a contract route: prefixes get a child, regexes their first alternative. */
function samplePath(r: Record<string, string>): string {
  if (r.match === 'exact') return r.path;
  if (r.match === 'regex') {
    return r.path.replace(/^\^/, '').replace(/\$$/, '').replace(/\(([^)]*)\)/g, (_, g: string) =>
      g === '.*' ? 'rest/x' : g.split('|')[0]);
  }
  return r.path === '/assets/' ? '/assets/launcher.js' : r.path.replace(/\/?$/, '/') + 'probe';
}

const routes = contractRoutes().filter((r) => r.server in SERVERS && r.gate !== 'deny');

test.describe('KC-14-01 exempt routes need no session; nothing else is reachable without one', () => {
  for (const r of routes.filter((x) => x.gate === 'exempt' && !/401 without session/.test(x.reason ?? ''))) {
    test(`exempt ${r.server} ${r.match} ${r.path} @KC-14-01 @KC-01-06 @security @sso-on-only`, async () => {
      const res = await rawResponse(SERVERS[r.server](), samplePath(r));
      expect(res.status, `${r.path} was gated`).not.toBe(401);
      expect(res.location, `${r.path} sent to login`).not.toContain('/oauth2/start');
    });
  }
  for (const r of routes.filter((x) => x.gate !== 'exempt')) {
    test(`gated ${r.server} ${r.match} ${r.path} (${r.gate}) @KC-14-01 @KC-01-06 @security @sso-on-only`, async () => {
      const base = SERVERS[r.server]();
      let res = await rawResponse(base, samplePath(r));
      if (r.kind === 'redirect' && res.status >= 300 && res.status < 400) {
        expect(res.location.startsWith('/') && !res.location.startsWith('//'), res.location).toBe(true);
        res = await rawResponse(base, res.location);
      }
      expect(res.status, `${samplePath(r)} reachable without a session`).toBe(401);
    });
  }
});
