// NidanEHR launcher (backlog KC-15). Served as a file so the page runs under
// Content-Security-Policy default-src 'self' (no inline script).
//
// * Health tiles: poll each app's health URL; 502/503/504 or a network error = Offline.
// * Role filter: with central login on, /oauth2/userinfo (oauth2-proxy) returns the user's groups
//   ("role:nidan-gateway:<role>"); cards the user may not open are hidden. With login off that URL
//   is not JSON (the gateway falls back to this page), so every card stays.
// * Names are inserted with textContent only, never innerHTML (KC-15-09).

(function () {
  'use strict';

  const services = [
    { id: 'openmrs', url: '/openmrs/spa/config.json', role: 'app-openmrs' },
    { id: 'openelis', url: '/openelis/', role: 'app-openelis' },
    { id: 'odoo', url: '/odoo-health/', role: 'app-odoo' },
    { id: 'orthanc', url: '/auth-ping/orthanc', role: 'orthanc-admin' },
    { id: 'cis', url: '/cis/actuator/health', role: 'cis-admin' },
    { id: 'superset', url: '/superset/health', role: 'app-superset' },   // not /superset/: it redirects to Keycloak
    { id: 'dhis2', url: '/dhis2/auth/login', role: 'app-dhis2' },
  ];
  const ROLE_PREFIX = 'role:nidan-gateway:';
  const I = window.NidanI18n;
  const t = (k, v) => (I ? I.t(k, v) : k);

  function setStatus(id, online) {
    const card = document.getElementById('card-' + id);
    const badge = document.getElementById('status-' + id);
    if (!card || !badge) return;
    card.classList.toggle('offline', !online);
    badge.className = 'status-badge ' + (online ? 'status-online' : 'status-offline');
    badge.removeAttribute('data-i18n');                // no longer the "checking" text
    badge.textContent = online ? t('status.online') : t('status.offline');
  }

  const HEALTH_TIMEOUT_MS = 5000;

  /** True when the app answered (not 502/503/504) within HEALTH_TIMEOUT_MS. */
  async function isUp(s) {
    const ctl = new AbortController();
    const timer = setTimeout(() => ctl.abort(), HEALTH_TIMEOUT_MS);
    try {
      const res = await fetch(s.url, { method: 'GET', cache: 'no-cache', credentials: 'same-origin', signal: ctl.signal });
      // nginx answers 502/503/504 when the container is down or starting; anything else means it answered
      return !(res.status === 502 || res.status === 503 || res.status === 504);
    } catch (e) {
      return false;
    } finally {
      clearTimeout(timer);
    }
  }

  /** Probes every visible app in parallel; resolves to the ids that are up. */
  async function checkHealth() {
    const shown = services.filter((s) => { const c = document.getElementById('card-' + s.id); return c && !c.hidden; });
    const results = await Promise.all(shown.map(async (s) => { const up = await isUp(s); setStatus(s.id, up); return up ? s.id : null; }));
    const online = results.filter(Boolean);
    const banner = document.getElementById('all-down');
    if (shown.length && !shown.some((s) => online.includes(s.id))) {
      if (!banner) {
        const b = document.createElement('div');
        b.id = 'all-down';
        b.className = 'state-banner';
        b.setAttribute('role', 'alert');
        b.textContent = t('launcher.allDown');
        messages().append(b);
      }
    } else if (banner) {
      banner.remove();
    }
    return online;
  }

  /**
   * {name, roles:Set} when central login is on; null when it is off (the URL falls through to this
   * page); {expired:true} on 401; {error:true} when the login service answers 5xx or cannot be reached.
   */
  async function whoAmI() {
    try {
      const res = await fetch('/oauth2/userinfo', { cache: 'no-cache', credentials: 'same-origin' });
      if (res.status === 401) return { expired: true };
      if (res.status >= 500) return { error: true };
      const type = res.headers.get('content-type') || '';
      if (!res.ok || !type.includes('application/json')) return null;
      const info = await res.json();
      const roles = new Set((info.groups || [])
        .filter((g) => typeof g === 'string' && g.startsWith(ROLE_PREFIX))
        .map((g) => g.slice(ROLE_PREFIX.length)));
      return { name: info.preferredUsername || info.user || info.email || '', roles };
    } catch (e) {
      return { error: true };
    }
  }

  const messages = () => document.getElementById('launcher-messages') || document.body;

  function showMessage(cls, text, withRetry) {
    const box = document.createElement('div');
    box.className = cls;
    box.setAttribute('role', 'alert');
    box.textContent = text;
    if (withRetry) {
      const btn = document.createElement('button');
      btn.type = 'button';
      btn.textContent = t('launcher.retry');
      btn.addEventListener('click', () => location.reload());
      box.append(' ', btn);
    }
    messages().append(box);
  }

  function applyIdentity(me) {
    for (const s of services) {
      const card = document.getElementById('card-' + s.id);
      if (card) card.hidden = !me.roles.has(s.role);
    }
    const line = document.getElementById('identity-line');
    if (!line) return;
    line.textContent = '';
    const [before, after] = I ? I.parts('identity.signedInAs', 'name') : ['Signed in as ', ''];
    const who = document.createElement('span');
    const name = document.createElement('strong');
    name.textContent = me.name;
    who.append(before, name, after);
    line.append(who);
    const kc = line.dataset.keycloak;
    if (kc) {                       // KC-55-05: Keycloak's own update-password step, then back to the launcher
      const pw = document.createElement('a');
      pw.href = '#';
      pw.id = 'change-password';
      pw.textContent = t('identity.changePassword');
      pw.addEventListener('click', (e) => { e.preventDefault(); keycloakAction(kc, 'UPDATE_PASSWORD'); });
      line.append(pw);
      // Optional second step (decision 2026-10-12): each entry runs Keycloak's own set-up page, then back here
      const two = document.createElement('details');
      two.className = 'two-step';
      two.id = 'two-step';
      const sum = document.createElement('summary');
      sum.textContent = t('identity.twoStep');
      two.append(sum);
      const menu = document.createElement('div');
      menu.className = 'two-step-menu';
      for (const [id, action, key] of [['two-step-app', 'CONFIGURE_TOTP', 'twoStep.app'],
        ['two-step-passkey', 'webauthn-register', 'twoStep.passkey'], ['two-step-codes', 'CONFIGURE_RECOVERY_AUTHN_CODES', 'twoStep.codes'],
        // to the email / phone the hospital admin entered (keycloak/providers/nidan-code-otp)
        ['two-step-email', 'nidan-email-code-setup', 'twoStep.email'], ['two-step-sms', 'nidan-sms-code-setup', 'twoStep.sms']]) {
        const a = document.createElement('a');
        a.href = '#';
        a.id = id;
        a.textContent = t(key);
        a.addEventListener('click', (e) => { e.preventDefault(); keycloakAction(kc, action); });
        menu.append(a);
      }
      two.append(menu);
      line.append(two);
    }
    const out = document.createElement('a');
    out.href = '/oauth2/sign_out';
    out.className = 'sign-out';
    out.textContent = t('identity.signOut');
    line.append(out);
    line.hidden = false;
    if (![...services].some((s) => me.roles.has(s.role))) {
      const none = document.createElement('p');
      none.className = 'no-apps';
      none.textContent = t('launcher.noApps');
      messages().append(none);
    }
  }

  function showSignedOut(s) {
    const box = document.createElement('div');
    box.className = 'state-banner signed-out';
    box.id = 'signed-out';
    box.setAttribute('role', 'status');
    const title = document.querySelector('#card-' + s.id + ' .card-title');
    box.textContent = t('launcher.signedOutOf', { app: title ? title.textContent : s.id }) + ' ';
    const all = document.createElement('a');
    all.href = '/oauth2/sign_out';
    all.textContent = t('launcher.signOutAll');
    box.append(all);
    messages().append(box);
  }

  /** Runs one of Keycloak's own account steps (kc_action) and comes back to the launcher. */
  async function keycloakAction(kc, action) {
    // The nidan-password client has no roles and its code is never redeemed (nobody holds its secret): it only
    // carries kc_action. PKCE is required by the realm, so a challenge is sent; the verifier is not needed.
    const v = new Uint8Array(32);
    crypto.getRandomValues(v);
    const digest = new Uint8Array(await crypto.subtle.digest('SHA-256', new TextEncoder().encode(btoa(String.fromCharCode(...v)))));
    const challenge = btoa(String.fromCharCode(...digest)).replace(/\+/g, '-').replace(/\//g, '_').replace(/=+$/, '');
    const q = new URLSearchParams({ client_id: 'nidan-password', redirect_uri: location.origin + '/', response_type: 'code',
      scope: 'openid', state: 'nidan-password', kc_action: action, code_challenge: challenge, code_challenge_method: 'S256' });
    if (I && I.locale) q.set('ui_locales', I.locale);
    location.href = kc.replace(/\/$/, '') + '/realms/nidan/protocol/openid-connect/auth?' + q;
  }

  // Back from Keycloak's update-password step: drop the unused code from the address bar.
  if (new URLSearchParams(location.search).get('state') === 'nidan-password') history.replaceState(null, '', location.pathname);

  function wireDemoCredentials() {
    const btn = document.getElementById('credentialBtn');
    const modal = document.getElementById('credentialModal');
    const close = document.getElementById('closeCredential');
    if (!btn || !modal || !close) return; // removed unless GATEWAY_SHOW_DEMO_CREDENTIALS=true
    btn.addEventListener('click', (e) => { e.preventDefault(); modal.classList.add('active'); });
    close.addEventListener('click', () => modal.classList.remove('active'));
    modal.addEventListener('click', (e) => { if (e.target === modal) modal.classList.remove('active'); });
  }

  async function start() {
    if (I) await I.ready;
    wireDemoCredentials();
    const me = await whoAmI();
    if (me && me.expired) {
      location.replace('/oauth2/start?rd=' + encodeURIComponent('/'));
      return;
    }
    if (me && me.error) {
      for (const s of services) { const c = document.getElementById('card-' + s.id); if (c) c.hidden = true; }
      showMessage('state-banner', t('launcher.loadError'), true);
      return;
    }
    if (me) applyIdentity(me);
    // Back from an app's own logout (gateway logout chain, ?signed_out=<app>): that was the person's only app ->
    // sign out of everything (a one-app launcher would open it again); else stay here with the other apps.
    const params = new URLSearchParams(location.search);
    const gone = services.find((s) => s.id === params.get('signed_out'));   // only known ids, never free text
    if (params.has('signed_out')) history.replaceState(null, '', location.pathname);
    if (me && gone) {                     // an unknown value (a crafted link) signs nobody out
      const line = document.getElementById('identity-line');
      const endsAll = line && line.dataset.logoutAll === 'true';      // NIDAN_APP_LOGOUT_ENDS_ALL (shared PCs)
      if (endsAll || services.filter((s) => me.roles.has(s.role)).length <= 1) {
        location.replace('/oauth2/sign_out');
        return;
      }
      showSignedOut(gone);
      checkHealth();
      setInterval(checkHealth, 15000);
      return;
    }
    // Exactly one permitted app that is up: go straight in (KC-15-03). /?launcher keeps the launcher.
    if (me && !params.has('launcher')) {     // read before ?signed_out= was cleared from the address bar
      const mine = services.filter((s) => me.roles.has(s.role));
      if (mine.length === 1 && await isUp(mine[0])) {
        location.replace(document.getElementById('card-' + mine[0].id).getAttribute('href'));
        return;
      }
    }
    checkHealth();
    setInterval(checkHealth, 15000);
  }

  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', start);
  else start();
})();
