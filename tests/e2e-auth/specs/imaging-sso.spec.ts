import { test, expect, APIRequestContext } from '@playwright/test';
import { execFileSync } from 'node:child_process';

/**
 * The image viewer behind central login (backlog KC-30): tests/e2e-auth/harness/sso-openmrs.yml --profile orthanc
 * (real Orthanc with its OHIF + DICOMweb plugins). Runs when IMAGING_SSO_FULL is set.
 */
const FULL = !!process.env.IMAGING_SSO_FULL;
// OHIF draws with WebGL (Cornerstone3D); headless Chromium has none unless software rendering is allowed
test.use({ launchOptions: { args: ['--use-angle=swiftshader', '--enable-unsafe-swiftshader', '--ignore-gpu-blocklist'] } });
const KC = process.env.KEYCLOAK_URL ?? 'http://localhost:18180/auth';
const GW = 'https://localhost';

async function admin(request: APIRequestContext, path: string, method = 'GET', data?: unknown) {
  const t = await request.post(`${KC}/realms/master/protocol/openid-connect/token`, {
    form: { grant_type: 'password', client_id: 'admin-cli', username: 'admin', password: 'harness-admin-password' } });
  return request.fetch(`${KC}/admin/realms/nidan${path}`, { method, data, headers: { Authorization: `Bearer ${(await t.json()).access_token}` } });
}

/** One study in Orthanc, made with its own /tools/create-dicom (a 64x64 grey PNG), as the operator inside the network. */
function createStudy(): string {
  const py = 'import base64, json, struct, urllib.request, zlib\n' +
    'def png(w, h):\n' +
    '    raw = b"".join(b"\\x00" + bytes((x * 4) % 256 for x in range(w)) for _ in range(h))\n' +
    '    c = lambda t, d: struct.pack(">I", len(d)) + t + d + struct.pack(">I", zlib.crc32(t + d))\n' +
    '    return b"\\x89PNG\\r\\n\\x1a\\n" + c(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 0, 0, 0, 0)) + c(b"IDAT", zlib.compress(raw)) + c(b"IEND", b"")\n' +
    'body = json.dumps({"Tags": {"PatientName": "Harness^Imaging", "PatientID": "HARN-IMG-1", "StudyDescription": "KC-30-01",\n' +
    '    "Modality": "CR"}, "Content": "data:image/png;base64," + base64.b64encode(png(64, 64)).decode()}).encode()\n' +
    'r = urllib.request.Request("http://localhost:8042/tools/create-dicom", data=body, method="POST",\n' +
    '    headers={"Authorization": "Basic " + base64.b64encode(b"harness-orthanc:harness-orthanc-password").decode()})\n' +
    'inst = json.load(urllib.request.urlopen(r))["ID"]\n' +
    'r = urllib.request.Request("http://localhost:8042/instances/%s/study" % inst,\n' +
    '    headers={"Authorization": "Basic " + base64.b64encode(b"harness-orthanc:harness-orthanc-password").decode()})\n' +
    'print(json.load(urllib.request.urlopen(r))["MainDicomTags"]["StudyInstanceUID"])\n';
  return execFileSync('docker', ['exec', 'nidan-sso-openmrs-harness-orthanc-1', 'python3', '-c', py]).toString().trim();
}

test.describe('image viewer with central login', () => {
  test.skip(!FULL, 'needs tests/e2e-auth/harness/sso-openmrs.yml --profile orthanc (IMAGING_SSO_FULL=1)');

  // KC-30-01: a doctor (app-orthanc) opens a study in OHIF through the gateway and the image renders. (Opened by
  // StudyInstanceUID, the link an imaging order carries; the O3 order screen itself is not part of this harness.)
  test('a doctor opens a study and OHIF renders it @KC-30-01 @sso-on-only', async ({ page, request }) => {
    test.setTimeout(120_000);
    const uid = createStudy();
    const username = `doc${Date.now()}`;
    const r = await admin(request, '/users', 'POST', { username, firstName: 'Doc', lastName: 'Tor', enabled: true,
      attributes: { gender: ['M'] }, groups: ['/staff/Doctor'] });
    const id = r.headers()['location'].split('/').pop()!;
    await admin(request, `/users/${id}/reset-password`, 'PUT', { type: 'password', value: 'Harness-Pass-123', temporary: false });
    const gw = (await (await admin(request, '/clients?clientId=nidan-gateway')).json())[0].id;
    await admin(request, `/users/${id}/role-mappings/clients/${gw}`, 'POST', [await (await admin(request, `/clients/${gw}/roles/app-orthanc`)).json()]);

    await page.goto(`${GW}/ohif/viewer?StudyInstanceUIDs=${uid}`);
    await page.fill('#username', username);
    await page.fill('#password', 'Harness-Pass-123');
    await page.click('#kc-login');
    await page.waitForURL(/\/ohif\/viewer/, { timeout: 60_000 });
    const canvas = page.locator('canvas').first();
    await expect(canvas, 'OHIF draws the image').toBeVisible({ timeout: 60_000 });
    // something was painted: the canvas is not all one colour
    await expect.poll(async () => canvas.evaluate((c: HTMLCanvasElement) => {
      const d = c.getContext('2d')?.getImageData(0, 0, c.width, c.height).data;
      if (!d) return 0;
      const seen = new Set<number>();
      for (let i = 0; i < d.length; i += 4 * 97) seen.add(d[i]);
      return seen.size;
    }), { timeout: 60_000 }).toBeGreaterThan(1);
  });
});
