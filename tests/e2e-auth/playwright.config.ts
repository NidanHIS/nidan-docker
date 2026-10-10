import { defineConfig } from '@playwright/test';

/**
 * Nidan security acceptance tests (backlog KC-42).
 *
 * Environment:
 *   GATEWAY_URL   https base of the gateway         (default https://localhost)
 *   GATEWAY_HTTP  http base for port-80 checks      (default http://localhost)
 *   LAN_HOST      host/IP to port-scan from outside the Docker network (KC-02); unset = skip
 *   COMPOSE_DIR   nidan-docker checkout for docker exec helpers (default ../..)
 *   DEAD_LETTER_ADMIN_PASSWORD  for the dead-letter admin checks (KC-53-06)
 *
 * Projects: sso-off runs today; sso-on and prod-like are filled in as KC-10 / KC-42-05 land.
 */
export default defineConfig({
  testDir: './specs',
  timeout: 30_000,
  fullyParallel: true,
  // json: read by scripts/release-gate.py (KC-49-03)
  reporter: [['list'], ['html', { open: 'never' }], ['json', { outputFile: 'test-results/report.json' }]],
  use: {
    baseURL: process.env.GATEWAY_URL ?? 'https://localhost',
    ignoreHTTPSErrors: true, // per-site CA or self-signed cert; TLS itself is checked in KC-12 tests
  },
  projects: [
    { name: 'sso-off', grepInvert: /@sso-on-only/ },
    { name: 'sso-on', grep: /@sso-on-only|@both-modes/ },
    { name: 'prod-like', grep: /@security/ },
  ],
});
