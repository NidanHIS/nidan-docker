// Staff page (backlog KC-20/21/22/23). Plain JS, no framework, served under a strict CSP.
// Rules: talks only to /staff-api on this origin, always with X-Nidan-Staff: 1 (CSRF guard);
// never sends roles or credentials when creating a person; never calls GET /users (listing is per
// job group); every value from the server or the user is rendered with textContent.
(function () {
  'use strict';

  const API = '/staff-api';
  // Client-side hint only; Keycloak's username validator is the real check (KC-55-03).
  const RESERVED = new Set(['admin', 'nidan', 'nidan-sync', 'daemon', '__system__', 'public', 'default',
    'cis', 'odoo-sync', 'portal-sync', 'dead-letter-admin']);
  const WORDS = ['river', 'mango', 'tiger', 'cloud', 'lotus', 'stone', 'maple', 'ember', 'panda', 'cedar', 'delta',
    'falcon', 'garden', 'harbor', 'island', 'jasmine', 'kettle', 'lantern', 'meadow', 'nectar', 'orchid', 'pepper',
    'quartz', 'rocket', 'saffron', 'timber', 'umbrella', 'velvet', 'walnut', 'yarrow', 'zephyr', 'bamboo'];

  let jobs = [];          // [{name, id}] from staff-config.json
  let people = [];        // [{user, job}] loaded per job
  const $ = (id) => document.getElementById(id);
  // texts from /staff/i18n/messages_<lang>.json (i18n.js); English is the fallback for any missing key
  const I = window.NidanI18n;
  const t = (key, vars) => (I && I.t(key, vars)) || key;

  async function api(method, path, body) {
    const res = await fetch(API + path, {
      method,
      credentials: 'same-origin',
      headers: Object.assign({ 'X-Nidan-Staff': '1' }, body ? { 'Content-Type': 'application/json' } : {}),
      body: body ? JSON.stringify(body) : undefined,
    });
    if (!res.ok) {
      const err = new Error('HTTP ' + res.status);
      err.status = res.status;
      try { err.detail = (await res.json()).errorMessage; } catch (e) { /* no body */ }
      throw err;
    }
    return res.status === 204 || res.headers.get('content-length') === '0' ? null
      : (res.headers.get('content-type') || '').includes('json') ? res.json() : null;
  }

  function notice(text) {
    const n = $('notice');
    n.textContent = text;
    n.hidden = !text;
  }

  function cell(text) {
    const td = document.createElement('td');
    td.textContent = text == null ? '' : String(text);
    return td;
  }

  function tempPassword() {
    const r = new Uint32Array(4);
    crypto.getRandomValues(r);
    return WORDS[r[0] % WORDS.length] + '-' + WORDS[r[1] % WORDS.length] + '-' + WORDS[r[2] % WORDS.length] + '-' + (r[3] % 90 + 10);
  }

  // ---------------------------------------------------------------- list
  let loadSeq = 0;      // a filter change while an earlier load runs: only the newest load may show its list
  async function loadPeople() {
    const seq = ++loadSeq;
    const state = $('list-state');
    state.textContent = t('staff.loading');
    state.hidden = false;
    $('people').hidden = true;
    try {
      const filter = $('job-filter').value;
      const wanted = filter ? jobs.filter((j) => j.id === filter) : jobs;
      const found = [];
      for (const job of wanted) {
        const members = (await api('GET', '/groups/' + job.id + '/members?first=0&max=500')) || [];
        for (const u of members) found.push({ user: u, job });
      }
      if (seq !== loadSeq) return;
      people = found;
      render();
    } catch (e) {
      if (seq !== loadSeq) return;
      if (e.status === 401) {               // the gateway session ended: sign in again and come back here
        state.textContent = t('staff.sessionEnded');
        const a = document.createElement('a');
        a.href = '/oauth2/start?rd=/staff/';
        a.textContent = t('staff.signInAgain');
        state.append(a);
      } else {
        state.textContent = e.status === 403 ? t('staff.notAllowed') : t('staff.loadFailed');
      }
    }
  }

  function render() {
    const q = $('search').value.trim().toLowerCase();
    const rows = people.filter(({ user }) => !q || [user.username, user.firstName, user.lastName]
      .some((v) => (v || '').toLowerCase().includes(q)));
    const tbody = $('people').querySelector('tbody');
    tbody.textContent = '';
    for (const { user, job } of rows) {
      const tr = document.createElement('tr');
      if (user.enabled === false) tr.className = 'disabled-row';
      tr.append(cell(((user.firstName || '') + ' ' + (user.lastName || '')).trim()), cell(user.username), cell(job.name),
        cell(user.enabled === false ? t('staff.disabled') : t('staff.active')));
      const actions = document.createElement('td');
      actions.append(button(t('staff.details'), () => openPlaces(user)), button(t('staff.resetPassword'), () => resetPassword(user)),
        button(user.enabled === false ? t('staff.enable') : t('staff.disable'), () => toggle(user)),
        jobSelect(user, job));
      tr.append(actions);
      tbody.append(tr);
    }
    $('list-state').textContent = rows.length ? '' : t('staff.noStaff');
    $('list-state').hidden = !!rows.length;
    $('people').hidden = !rows.length;
  }

  function button(label, onClick) {
    const b = document.createElement('button');
    b.type = 'button';
    b.textContent = label;
    b.addEventListener('click', onClick);
    return b;
  }

  function jobSelect(user, current) {
    const s = document.createElement('select');
    s.setAttribute('aria-label', t('staff.changeJobFor', { name: user.username }));
    for (const j of jobs) {
      const o = document.createElement('option');
      o.value = j.id;
      o.textContent = j.name;
      o.selected = j.id === current.id;
      s.append(o);
    }
    s.addEventListener('change', () => changeJob(user, current, jobs.find((j) => j.id === s.value)));
    return s;
  }

  // ---------------------------------------------------------------- actions
  async function changeJob(user, from, to) {
    if (!to || to.id === from.id) return;
    try {
      await api('PUT', '/users/' + user.id + '/groups/' + to.id);
      await api('DELETE', '/users/' + user.id + '/groups/' + from.id);
      notice(t('staff.jobChanged', { name: user.username, job: to.name }));
      await loadPeople();
    } catch (e) {
      notice(t('staff.jobChangeFailed', { error: e.detail || e.message }));
    }
  }

  async function toggle(user) {
    const disabling = user.enabled !== false;
    if (disabling && !confirm(t('staff.confirmDisable', { name: user.username }))) return;
    try {
      // Sign them out everywhere first: Keycloak sends the apps a back-channel logout (Odoo ends the session and an
      // open POS websocket closes). Disabling alone ends Keycloak's sessions silently (KC-11-02).
      if (disabling) await api('POST', '/users/' + user.id + '/logout');
      await api('PUT', '/users/' + user.id, { enabled: !disabling });
      notice(t(disabling ? 'staff.isDisabled' : 'staff.isEnabled', { name: user.username }));
      await loadPeople();
    } catch (e) {
      notice(t('staff.changeFailed', { name: user.username, error: e.detail || e.message }));
    }
  }

  async function resetPassword(user) {
    const pw = tempPassword();
    try {
      await api('PUT', '/users/' + user.id + '/reset-password', { type: 'password', value: pw, temporary: true });
      showSlip(user, pw);
    } catch (e) {
      notice(t('staff.resetFailed', { error: e.detail || e.message }));
    }
  }

  // R4 (KC-21-05): locations, lab units and POS shops are user attributes the apps read from the token.
  const PLACES = ['nidan_locations', 'nidan_lab_units', 'nidan_shops'];
  let placesFor = null;   // the full user as Keycloak returned it

  function parsePlaces(text) {
    const values = [...new Set(String(text || '').split(',').map((v) => v.trim()).filter(Boolean))];
    if (values.length > 30) throw new Error(t('staff.tooManyPlaces'));
    for (const v of values) {
      if (v.length > 64 || /[\u0000-\u001f<>]/.test(v)) throw new Error(t('staff.badPlace', { value: v.slice(0, 20) }));
    }
    return values;
  }

  async function openPlaces(user) {
    try {
      placesFor = await api('GET', '/users/' + user.id);
    } catch (e) {
      notice(t('staff.loadOneFailed', { name: user.username, error: e.detail || e.message }));
      return;
    }
    const form = $('places-form');
    const attrs = placesFor.attributes || {};
    for (const name of PLACES) form.elements[name].value = (attrs[name] || []).join(', ');
    form.elements.email.value = placesFor.email || '';
    form.elements.phone.value = (attrs.phone || [])[0] || '';
    const [before, after] = I ? I.parts('staff.details.title', 'name') : ['Details for ', ''];
    const who = document.createElement('span');
    who.id = 'places-user';
    who.textContent = placesFor.username;
    $('places-title').replaceChildren(before, who, after);
    $('places-error').hidden = true;
    $('places-dialog').showModal();
  }

  async function savePlaces(form) {
    const err = $('places-error');
    err.hidden = true;
    // Keycloak replaces the whole attribute map on update, so send every attribute it has (gender included) with only
    // the fields of this dialog changed. Read the person again first: a sign-in code turned on while the dialog was
    // open must not be overwritten by the copy loaded when it opened. Never roles, groups or credentials.
    try {
      placesFor = Object.assign(await api('GET', '/users/' + placesFor.id), { opened: placesFor.opened || placesFor });
    } catch (e) {
      err.textContent = t('staff.saveFailed', { error: e.detail || e.message });
      err.hidden = false;
      return;
    }
    const opened = placesFor.opened;
    const attributes = Object.assign({}, placesFor.attributes || {});
    try {
      for (const name of PLACES) {
        const values = parsePlaces(form.elements[name].value);
        if (values.length) attributes[name] = values; else delete attributes[name];
      }
    } catch (e) {
      err.textContent = e.message;
      err.hidden = false;
      return;
    }
    const email = form.elements.email.value.trim();
    const phone = form.elements.phone.value.trim();
    if (phone && !/^\+?[0-9 ]{7,16}$/.test(phone)) {
      err.textContent = t('staff.badPhone');
      err.hidden = false;
      return;
    }
    // a new address or number must be confirmed by the person before codes go there: turn that channel off
    if (email !== (opened.email || '')) delete attributes.nidan_2fa_email;
    if (phone !== (((opened.attributes || {}).phone || [])[0] || '')) delete attributes.nidan_2fa_sms;
    if (phone) attributes.phone = [phone]; else delete attributes.phone;
    const body = { username: placesFor.username, firstName: placesFor.firstName, lastName: placesFor.lastName, attributes };
    if (email || placesFor.email) body.email = email;   // only when there is one, or the admin cleared it
    try {
      await api('PUT', '/users/' + placesFor.id, body);
      $('places-dialog').close();
      notice(t('staff.detailsSaved', { name: placesFor.username }));
    } catch (e) {
      err.textContent = t('staff.saveFailed', { error: e.detail || e.message });
      err.hidden = false;
    }
  }

  function showSlip(user, pw) {
    $('slip-name').textContent = ((user.firstName || '') + ' ' + (user.lastName || '')).trim();
    $('slip-user').textContent = user.username;
    $('slip-password').textContent = pw;
    $('slip-dialog').showModal();
  }

  async function addPerson(form) {
    const f = Object.fromEntries(new FormData(form).entries());
    const err = $('add-error');
    err.hidden = true;
    const username = (f.username || '').trim().toLowerCase();
    let problem = '';
    if (!f.firstName || !f.lastName) problem = t('staff.namesRequired');
    else if (!/^[a-z][a-z0-9._-]{2,39}$/.test(username)) problem = t('staff.badUsername');
    else if (RESERVED.has(username) || /^[0-9]+-[0-9]$/.test(username)) problem = t('staff.reserved');
    else if (!f.gender) problem = t('staff.genderRequired');
    else if (!jobs.some((j) => j.id === f.job)) problem = t('staff.chooseJob');
    if (problem) {
      err.textContent = problem;
      err.hidden = false;
      return false;
    }
    const job = jobs.find((j) => j.id === f.job);
    // No roles and no credentials in the create call: the job group carries the roles, and the
    // temporary password goes through reset-password so admin events never contain it.
    const body = {
      username, firstName: f.firstName.trim(), lastName: f.lastName.trim(), enabled: true,
      attributes: { gender: [f.gender] }, groups: ['/staff/' + job.name], requiredActions: ['UPDATE_PASSWORD'],
    };
    if (f.email) body.email = f.email.trim();
    if (f.phone) {
      if (!/^\+?[0-9 ]{7,16}$/.test(f.phone.trim())) {
        err.textContent = t('staff.badPhone');
        err.hidden = false;
        return false;
      }
      body.attributes.phone = [f.phone.trim()];
    }
    try {
      await api('POST', '/users', body);
      // find the new user's id through the job group (GET /users is not allowed)
      const members = (await api('GET', '/groups/' + job.id + '/members?first=0&max=500')) || [];
      const created = members.find((u) => u.username === username);
      if (!created) throw new Error('created, but not found in ' + job.name);
      const pw = tempPassword();
      await api('PUT', '/users/' + created.id + '/reset-password', { type: 'password', value: pw, temporary: true });
      $('add-dialog').close();
      form.reset();
      showSlip(created, pw);
      await loadPeople();
      return true;
    } catch (e) {
      err.textContent = e.status === 409 ? t('staff.usernameTaken')
        : e.detail === 'nidanUsernameReserved' ? t('staff.reserved')  // realm user-profile pattern (KC-55-03)
          : t('staff.addFailed', { error: e.detail || e.message });
      err.hidden = false;
      return false;
    }
  }

  // ---------------------------------------------------------------- activity
  async function loadActivity() {
    const tbody = $('events').querySelector('tbody');
    tbody.textContent = '';
    try {
      const [logins, admin] = await Promise.all([
        api('GET', '/events?max=100'), api('GET', '/admin-events?max=100'),
      ]);
      const rows = [
        ...(logins || []).map((e) => ({ time: e.time, what: e.type, who: (e.details && e.details.username) || e.userId, details: e.ipAddress })),
        ...(admin || []).filter((e) => ['USER', 'GROUP_MEMBERSHIP'].includes(e.resourceType))
          .map((e) => ({ time: e.time, what: e.operationType + ' ' + e.resourceType, who: e.authDetails && e.authDetails.userId, details: e.resourcePath })),
      ].sort((a, b) => b.time - a.time);
      for (const r of rows) {
        const tr = document.createElement('tr');
        tr.append(cell(new Date(r.time).toLocaleString()), cell(r.what), cell(r.who), cell(r.details));
        tbody.append(tr);
      }
    } catch (e) {
      notice(t('staff.activityFailed'));
    }
  }

  // ---------------------------------------------------------------- wiring
  /** Header: who is signed in; "All apps" goes to the launcher on 443 of this host (this page is on :9443). */
  async function showWho() {
    try {
      const res = await fetch('/oauth2/userinfo', { cache: 'no-cache', credentials: 'same-origin' });
      if (!res.ok || !(res.headers.get('content-type') || '').includes('application/json')) return;
      const info = await res.json();
      const [before, after] = I ? I.parts('staff.signedInAs', 'name') : ['Signed in as ', ''];
      const name = document.createElement('strong');
      name.id = 'who-name';
      name.textContent = info.preferredUsername || info.user || '';
      $('who-line').replaceChildren(before, name, after);
      $('to-apps').href = 'https://' + location.hostname + '/';
      $('who').hidden = false;
    } catch (e) { /* header stays without the name */ }
  }

  async function start() {
    if (I) await I.ready;
    showWho();
    try {
      const res = await fetch('/staff/staff-config.json', { cache: 'no-cache' });
      if (!res.ok) throw new Error('HTTP ' + res.status);
      // Only /staff/<name> jobs (KC-21-09); the gateway also refuses any other group id.
      jobs = ((await res.json()).jobs || []).filter((j) => j && j.name && /^[0-9a-f-]{36}$/.test(j.id)
        && j.path === '/staff/' + j.name);
    } catch (e) {
      $('list-state').textContent = t('staff.configMissing');
      return;
    }
    if (!jobs.length) {   // the placeholder shipped with the image, or a site that never ran scripts/staff-config.py
      $('list-state').textContent = t('staff.noJobs');
      return;
    }
    const filter = $('job-filter');
    const all = document.createElement('option');
    all.value = '';
    all.textContent = t('staff.allJobs');
    filter.append(all);
    for (const j of jobs) {
      for (const sel of [filter, $('add-job')]) {
        const o = document.createElement('option');
        o.value = j.id;
        o.textContent = j.name;
        sel.append(o);
      }
    }
    filter.addEventListener('change', loadPeople);
    $('search').addEventListener('input', render);
    $('add-open').addEventListener('click', () => $('add-dialog').showModal());
    $('add-cancel').addEventListener('click', () => $('add-dialog').close());
    $('add-form').addEventListener('submit', (e) => { e.preventDefault(); addPerson(e.target); });
    $('places-form').addEventListener('submit', (e) => { e.preventDefault(); savePlaces(e.target); });
    $('places-cancel').addEventListener('click', () => $('places-dialog').close());
    $('slip-print').addEventListener('click', () => window.print());
    $('slip-close').addEventListener('click', () => { $('slip-password').textContent = ''; $('slip-dialog').close(); });
    for (const t of document.querySelectorAll('.tab')) {
      t.addEventListener('click', () => {
        for (const o of document.querySelectorAll('.tab')) o.setAttribute('aria-pressed', String(o === t));
        $('tab-people').hidden = t.dataset.tab !== 'people';
        $('tab-activity').hidden = t.dataset.tab !== 'activity';
        if (t.dataset.tab === 'activity') loadActivity();
      });
    }
    await loadPeople();
  }

  document.addEventListener('DOMContentLoaded', start);
})();
