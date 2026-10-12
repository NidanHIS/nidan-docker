import { test, expect, APIRequestContext, Page } from '@playwright/test';
import { createHmac } from 'crypto';

/**
 * Optional second step (user decision 2026-10-12): nobody has to set one up; anyone can, from the launcher's
 * "Two-step sign-in" menu (authenticator app, passkey or security key, recovery codes), and from then on the login
 * asks for it. Full harness (harness/sso-openmrs.yml); runs with OPENMRS_SSO_FULL (run-keycloak-suite.sh full stage).
 */
const FULL = !!process.env.OPENMRS_SSO_FULL;
const KC = process.env.KEYCLOAK_URL ?? 'http://localhost:18180/auth';
const GW = 'https://localhost';
const PASSWORD = 'Harness-Pass-123';

async function admin(request: APIRequestContext, path: string, method = 'GET', data?: unknown) {
  const t = await request.post(`${KC}/realms/master/protocol/openid-connect/token`, {
    form: { grant_type: 'password', client_id: 'admin-cli', username: 'admin', password: 'harness-admin-password' } });
  return request.fetch(`${KC}/admin/realms/nidan${path}`, { method, data, headers: { Authorization: `Bearer ${(await t.json()).access_token}` } });
}

/** A nurse with OpenMRS + OpenELIS (several apps keep the launcher from opening one app straight away), with the
 *  email / phone a hospital admin would enter. */
async function person(request: APIRequestContext, contact: { email?: string; phone?: string } = {}) {
  const username = `two${Date.now()}${Math.floor(Math.random() * 1000)}`;
  const r = await admin(request, '/users', 'POST', { username, firstName: 'Two', lastName: 'Step', enabled: true,
    ...(contact.email ? { email: contact.email, emailVerified: true } : {}),
    attributes: { gender: ['F'], ...(contact.phone ? { phone: [contact.phone] } : {}) }, groups: ['/staff/Nurse'] });
  const id = r.headers()['location'].split('/').pop()!;
  await admin(request, `/users/${id}/reset-password`, 'PUT', { type: 'password', value: PASSWORD, temporary: false });
  const gw = (await (await admin(request, '/clients?clientId=nidan-gateway')).json())[0].id;
  const roles = await Promise.all(['app-openmrs', 'app-openelis'].map(async (n) => (await admin(request, `/clients/${gw}/roles/${n}`)).json()));
  await admin(request, `/users/${id}/role-mappings/clients/${gw}`, 'POST', roles);
  return { username, id };
}

async function passwordStep(page: Page, username: string) {
  await page.goto(`${GW}/?launcher`);
  await page.fill('#username', username);
  await page.fill('#password', PASSWORD);
  await page.click('#kc-login');
}

const onLauncher = async (page: Page) => {
  await page.waitForURL(`${GW}/**`, { timeout: 30_000 });
  await expect(page.locator('#identity-line')).toBeVisible();
};

async function openMenu(page: Page, item: string) {
  await page.locator('#two-step summary').click();
  await page.locator(`#${item}`).click();
}

const totp = (secret: string, step = Math.floor(Date.now() / 30000)) => {
  const m = Buffer.alloc(8); m.writeBigUInt64BE(BigInt(step));
  const d = createHmac('sha1', Buffer.from(secret)).update(m).digest(); const o = d[d.length - 1] & 15;
  return String((d.readUInt32BE(o) & 0x7fffffff) % 1000000).padStart(6, '0');
};

async function signOut(page: Page) {
  await page.goto(`${GW}/oauth2/sign_out`);
  await expect(page.locator('#kc-login')).toBeVisible();
}

/** The harness channels: SMTP -> mailpit, SMS "webhook" -> sms-sink (harness/sso-openmrs.yml). */
async function harnessChannels(request: APIRequestContext) {
  const r = await admin(request, '', 'PUT', {
    smtpServer: { host: 'mailpit', port: '1025', from: 'noreply@nidan.test', fromDisplayName: 'Nidan EHR', auth: 'false', starttls: 'false', ssl: 'false' },
    attributes: { nidanSmsProvider: 'webhook', nidanSmsUrl: 'http://sms-sink:8080/send', nidanSmsToken: 'harness-sms-token', nidanSmsFrom: '' } });
  expect(r.status(), 'harness email/SMS settings').toBe(204);
}

/** The newest code sent to this address (mailpit) or number (sms-sink), polled until one newer than `after` arrives. */
async function codeFor(request: APIRequestContext, to: string, after: string[] = []) {
  let code = '';
  await expect.poll(async () => {
    const texts = to.includes('@')
      ? ((await (await request.get('http://127.0.0.1:18025/api/v1/messages?limit=50')).json()).messages as Array<{ To: Array<{ Address: string }>; Snippet: string }>)
        .filter((m) => m.To.some((t) => t.Address === to)).map((m) => m.Snippet)
      : ((await (await request.get('http://127.0.0.1:18026/messages')).json()) as Array<{ to: string; text: string }>)
        .filter((m) => m.to === to).reverse().map((m) => m.text);
    code = texts.map((t) => /sign-in code is (\d{6})/.exec(t)?.[1] ?? '').find((c) => c && !after.includes(c)) ?? '';
    return code;
  }, { timeout: 20_000, message: `a code sent to ${to}` }).not.toBe('');
  return code;
}

const credentialTypes = async (request: APIRequestContext, id: string) =>
  ((await (await admin(request, `/users/${id}/credentials`)).json()) as Array<{ type: string }>).map((c) => c.type).sort();

test.describe('optional second step', () => {
  test.skip(!FULL, 'needs the full harness (OPENMRS_SSO_FULL=1)');
  test.describe.configure({ mode: 'serial' });
  test.setTimeout(150_000);

  test('nobody is asked for a second step until they set one up; the launcher offers all three @KC-09-09 @security @sso-on-only', async ({ page, request }) => {
    const p = await person(request);
    await passwordStep(page, p.username);
    await onLauncher(page);                              // password alone: no second-step page in between
    await page.locator('#two-step summary').click();
    for (const id of ['two-step-app', 'two-step-passkey', 'two-step-codes']) await expect(page.locator(`#${id}`)).toBeVisible();
    expect(await credentialTypes(request, p.id)).toEqual(['password']);
  });

  test('authenticator app: set up from the launcher, then every sign-in asks for its code @KC-09-09 @security @sso-on-only', async ({ page, request }) => {
    const p = await person(request);
    await passwordStep(page, p.username);
    await onLauncher(page);
    await openMenu(page, 'two-step-app');
    const secret = await page.locator('input[name="totpSecret"]').inputValue();
    const step = Math.floor(Date.now() / 30000);
    await page.fill('#totp', totp(secret, step));
    if (await page.locator('#userLabel').count()) await page.fill('#userLabel', 'phone');
    await page.locator('#kc-totp-settings-form [type="submit"]').first().click();
    await onLauncher(page);
    expect(await credentialTypes(request, p.id)).toEqual(['otp', 'password']);

    await signOut(page);
    await passwordStep(page, p.username);
    await expect(page.locator('#otp'), 'asks for the app code').toBeVisible();
    await page.fill('#otp', '000000');
    await page.click('#kc-login');
    await expect(page.locator('#otp'), 'a wrong code is refused').toBeVisible();
    // a code is accepted once: wait for the next 30-second step after the set-up code
    const wait = (step + 1) * 30000 - Date.now() + 300;
    if (wait > 0) await page.waitForTimeout(wait);
    await page.fill('#otp', totp(secret));
    await page.click('#kc-login');
    await onLauncher(page);
  });

  test('recovery codes: set up from the launcher, then a code signs in once @KC-09-09 @security @sso-on-only', async ({ page, request }) => {
    const p = await person(request);
    await passwordStep(page, p.username);
    await onLauncher(page);
    await openMenu(page, 'two-step-codes');
    await expect(page.getByRole('heading', { name: 'Recovery Authentication Codes' })).toBeVisible();
    const codes = (await page.getByRole('listitem').allInnerTexts()).map((c) => c.trim()).filter((c) => /^[A-Z0-9]{4}(-[A-Z0-9]{4})+$/.test(c));
    expect(codes.length, 'Keycloak shows the codes once').toBeGreaterThan(5);
    await page.getByRole('checkbox', { name: 'I have saved these codes somewhere safe' }).check();
    await page.getByRole('button', { name: 'Complete setup' }).click();
    await onLauncher(page);
    expect(await credentialTypes(request, p.id)).toEqual(['password', 'recovery-authn-codes']);

    await signOut(page);
    await passwordStep(page, p.username);
    const input = page.locator('#recoveryCodeInput');
    await expect(input, 'asks for a recovery code').toBeVisible();
    await input.fill(codes[0]);
    await page.click('#kc-login');
    await onLauncher(page);

    await signOut(page);                                  // the same code does not work twice
    await passwordStep(page, p.username);
    await input.fill(codes[0]);
    await page.click('#kc-login');
    await expect(input, 'a used code is refused').toBeVisible();
  });

  test('passkey or security key: set up from the launcher, then the sign-in asks for it @KC-09-09 @security @sso-on-only', async ({ page, request }) => {
    // Chromium's virtual authenticator stands in for a phone or USB key (touch and PIN pass automatically)
    const cdp = await page.context().newCDPSession(page);
    await cdp.send('WebAuthn.enable');
    await cdp.send('WebAuthn.addVirtualAuthenticator', { options: { protocol: 'ctap2', transport: 'usb', hasResidentKey: true,
      hasUserVerification: true, isUserVerified: true, automaticPresenceSimulation: true } });
    page.on('dialog', (d) => d.accept('test key'));       // Keycloak asks for a name for the new key
    const p = await person(request);
    await passwordStep(page, p.username);
    await onLauncher(page);
    await openMenu(page, 'two-step-passkey');
    await page.locator('#registerWebAuthn').click();
    await onLauncher(page);
    expect(await credentialTypes(request, p.id)).toEqual(['password', 'webauthn']);

    await signOut(page);
    await passwordStep(page, p.username);
    await page.locator('#authenticateWebAuthnButton').click();
    await onLauncher(page);
  });

  for (const [channel, item, flag] of [['email', 'two-step-email', 'nidan_2fa_email'], ['sms', 'two-step-sms', 'nidan_2fa_sms']] as const) {
    test(`code by ${channel}: turned on from the launcher with a code sent to the admin-entered ${channel === 'email' ? 'address' : 'phone'}, then asked at every sign-in @KC-09-09 @security @sso-on-only`,
      async ({ page, request }) => {
        await harnessChannels(request);
        const to = channel === 'email' ? `two${Date.now()}@nidan.test` : `+977 98${String(Date.now()).slice(-8)}`;
        const p = await person(request, channel === 'email' ? { email: to } : { phone: to });
        await passwordStep(page, p.username);
        await onLauncher(page);
        await openMenu(page, item);
        await expect(page.locator('#nidan-code-sent')).toContainText(channel === 'email' ? '@nidan.test' : to.slice(-4));
        const first = await codeFor(request, to);
        await page.fill('#code', first);
        await page.click('#kc-login');
        await onLauncher(page);
        const user = await (await admin(request, `/users/${p.id}`)).json();
        expect(user.attributes[flag], 'turned on only after the code came back').toEqual(['true']);

        await signOut(page);
        await passwordStep(page, p.username);
        await expect(page.locator('#code'), 'the sign-in asks for a code').toBeVisible();
        const second = await codeFor(request, to, [first]);
        await page.fill('#code', second === '000000' ? '111111' : '000000');
        await page.click('#kc-login');
        await expect(page.locator('#code'), 'a wrong code is refused').toBeVisible();
        await page.fill('#code', second);
        await page.click('#kc-login');
        await onLauncher(page);
      });
  }

  test('no phone entered by the admin: SMS codes cannot be turned on, the person is told to ask the admin @KC-09-09 @security @sso-on-only', async ({ page, request }) => {
    await harnessChannels(request);
    const p = await person(request);                       // no phone
    await passwordStep(page, p.username);
    await onLauncher(page);
    await openMenu(page, 'two-step-sms');
    await expect(page.getByText('Your hospital admin has not added your email address or phone number yet')).toBeVisible();
    await expect(page.locator('#code'), 'nothing to type').toHaveCount(0);
    await page.locator('#nidan-code-cancel').click();
    await onLauncher(page);
    const user = await (await admin(request, `/users/${p.id}`)).json();
    expect(user.attributes?.nidan_2fa_sms, 'still off').toBeUndefined();
  });
});

