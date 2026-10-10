// Not-allowed and outage pages (backlog KC-16). A file, not inline, so the pages run under the
// launcher's strict CSP. Nothing from the URL is ever written into the page: the app label comes
// from a fixed map (KC-16-04) and names are set with textContent.
(function () {
  'use strict';

  const APPS = ['openmrs', 'openelis', 'odoo', 'orthanc', 'superset', 'dhis2', 'cis'];
  const I = window.NidanI18n;
  const t = (k, v) => (I ? I.t(k, v) : k);
  const params = new URLSearchParams(location.search);

  /** Same-origin path only (no scheme, no //host, no backslash tricks); anything else -> '/'. */
  function safeReturnPath(p) {
    return typeof p === 'string' && /^\/(?![\/\\])[^\s\\]*$/.test(p) ? p : '/';
  }

  async function notAllowed() {
    const key = params.get('app');
    const label = APPS.includes(key) ? t('app.' + key) : null;
    if (label) document.getElementById('np-title').textContent = t('na.titleApp', { app: label });
    try {
      const res = await fetch('/oauth2/userinfo', { cache: 'no-cache', credentials: 'same-origin' });
      if (!res.ok || !(res.headers.get('content-type') || '').includes('application/json')) return;
      const info = await res.json();
      const name = info.preferredUsername || info.user || info.email;
      if (!name) return;
      const who = document.getElementById('np-who');
      const [before, after] = I ? I.parts('na.who', 'name') : ['You are signed in as ', '.'];
      const b = document.createElement('strong');
      b.textContent = name;
      who.textContent = '';
      who.append(before, b, after);
      who.hidden = false;
      const sw = document.getElementById('np-switch');
      sw.textContent = t('na.switch', { name });
      sw.hidden = false;
    } catch (e) { /* page still useful without the name */ }
  }

  function outage(root) {
    const phone = (root.dataset.supportPhone || '').trim();
    if (phone && !phone.startsWith('${')) {
      const p = document.getElementById('outage-phone');
      p.textContent = t('outage.support') + ' ';
      const a = document.createElement('a');
      a.href = 'tel:' + phone.replace(/[^\d+]/g, '');
      a.textContent = phone;
      p.append(a);
      p.hidden = false;
    }
    const back = safeReturnPath(params.get('rd'));
    const status = document.getElementById('outage-retry');
    async function probe() {
      try {
        // Ask for the page the person wanted: through the gateway it needs the login service, so a
        // 5xx means still down and anything else (page, login redirect, 401/403) means it is back.
        const res = await fetch(back, { method: 'GET', cache: 'no-cache', credentials: 'same-origin', redirect: 'manual' });
        if (res.type === 'opaqueredirect' || (res.status > 0 && res.status < 500)) {
          location.replace(back);
          return;
        }
      } catch (e) { /* still down */ }
      status.textContent = t('outage.still');
    }
    setInterval(probe, 30000);
  }

  window.NidanStatusPages = { safeReturnPath };
  const root = document.getElementById('outage');
  (I ? I.ready : Promise.resolve()).then(() => { if (root) outage(root); else notAllowed(); });
})();
