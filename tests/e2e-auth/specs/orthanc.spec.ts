import { test, expect } from '@playwright/test';

/**
 * Orthanc's own read-only layer (backlog KC-30-02/05), against the real Orthanc image with orthanc/orthanc.json and
 * orthanc/viewer-readonly.lua (tests/e2e-auth/harness/orthanc.yml), probed directly as a client inside the Docker
 * network would. Runs when ORTHANC_URL is set.
 */
const O = process.env.ORTHANC_URL ?? '';
const basic = (u: string, p: string) => ({ Authorization: 'Basic ' + Buffer.from(`${u}:${p}`).toString('base64') });
const VIEWER = basic('nidan-viewer', process.env.ORTHANC_VIEWER_PASSWORD ?? 'harness-orthanc-viewer');
const ADMIN = basic('nidan', process.env.ORTHANC_ADMIN_PASSWORD ?? 'harness-orthanc-admin');

test.describe('Orthanc read-only layer', () => {
  test.skip(!O, 'set ORTHANC_URL (tests/e2e-auth/harness/orthanc.yml)');

  test('the viewer account reads and cannot write, even straight to Orthanc @KC-30-02 @security', async ({ request }) => {
    for (const path of ['/system', '/dicom-web/studies', '/studies']) {
      expect((await request.get(O + path, { headers: VIEWER })).status(), `GET ${path}`).toBe(200);
    }
    for (const [method, path] of [['DELETE', '/studies/0000'], ['POST', '/instances'], ['POST', '/dicom-web/studies'],
      ['PUT', '/tools/default-encoding'], ['POST', '/modalities/x/store'], ['DELETE', '/patients/0000']] as const) {
      expect((await request.fetch(O + path, { method, headers: VIEWER, data: 'x' })).status(), `${method} ${path}`).toBe(403);
    }
  });

  test('nobody runs scripts over REST; Lua and filesystem writes are off @KC-30-05 @security', async ({ request }) => {
    for (const path of ['/tools/execute-script', '/TOOLS/Execute-Script']) {
      expect((await request.post(O + path, { headers: ADMIN, data: 'print(1)' })).status(), path).toBe(403);
    }
    expect((await request.delete(O + '/studies/0000', { headers: ADMIN })).status(), 'admin writes still pass the filter').toBe(404);
    expect((await request.get(O + '/system')).status(), 'no credentials').toBe(401);
  });
});
