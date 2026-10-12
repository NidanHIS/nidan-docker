// Gateway page translations (backlog KC-18). Locale = cookie `nidan_locale` (en | ne), shared with
// the Keycloak login theme; default en. Elements with data-i18n="key" get the text; scripts use
// NidanI18n.t(key, {var}) and NidanI18n.parts(key, 'name') to build text around a name node.
// Unknown keys fall back to English, never to the raw key.
(function () {
  'use strict';
  const SUPPORTED = ['en', 'ne'];
  const m = document.cookie.match(/(?:^|;\s*)nidan_locale=([a-z]+)/);
  const locale = m && SUPPORTED.includes(m[1]) ? m[1] : 'en';
  let msgs = {};
  let en = {};

  const get = (url) => fetch(url, { cache: 'no-cache' }).then((r) => (r.ok ? r.json() : {})).catch(() => ({}));
  // the hospital's branding folder may override any text (assets/brand/README.md)
  const load = (loc) => Promise.all([get('/assets/i18n/messages_' + loc + '.json'), get('/assets/brand/messages_' + loc + '.json')])
    .then(([base, brand]) => Object.assign({}, base, brand));

  function t(key, vars) {
    let s = (msgs && msgs[key]) || en[key] || '';
    for (const [k, v] of Object.entries(vars || {})) s = s.split('{' + k + '}').join(v);
    return s;
  }
  /** [before, after] around {name}, so callers can insert a text node or <strong> in between. */
  function parts(key, name) {
    const s = t(key);
    const i = s.indexOf('{' + name + '}');
    return i < 0 ? [s, ''] : [s.slice(0, i), s.slice(i + name.length + 2)];
  }
  function apply(root) {
    (root || document).querySelectorAll('[data-i18n]').forEach((el) => {
      const s = t(el.getAttribute('data-i18n'));
      if (s) el.textContent = s;
    });
  }
  function setLocale(loc) {
    if (!SUPPORTED.includes(loc)) return;
    document.cookie = 'nidan_locale=' + loc + '; Path=/; Max-Age=31536000; SameSite=Lax' +
      (location.protocol === 'https:' ? '; Secure' : '');
    location.reload();
  }

  const ready = Promise.all([load('en'), locale === 'en' ? null : load(locale)]).then(([e, l]) => {
    en = e || {};
    msgs = l || en;
    document.documentElement.lang = locale;
    apply();
    const picker = document.getElementById('locale-picker');
    if (picker) {
      picker.value = locale;
      picker.addEventListener('change', () => setLocale(picker.value));
    }
  });

  window.NidanI18n = { locale, ready, t, parts, apply, setLocale };
})();
