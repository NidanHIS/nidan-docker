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
  async function loadPeople() {
    const state = $('list-state');
    state.textContent = 'Loading…';
    state.hidden = false;
    $('people').hidden = true;
    try {
      const filter = $('job-filter').value;
      const wanted = filter ? jobs.filter((j) => j.id === filter) : jobs;
      people = [];
      for (const job of wanted) {
        const members = (await api('GET', '/groups/' + job.id + '/members?first=0&max=500')) || [];
        for (const u of members) people.push({ user: u, job });
      }
      render();
    } catch (e) {
      if (e.status === 401) {               // the gateway session ended: sign in again and come back here
        state.textContent = 'Your session has ended. ';
        const a = document.createElement('a');
        a.href = '/oauth2/start?rd=/staff/';
        a.textContent = 'Sign in again';
        state.append(a);
      } else {
        state.textContent = e.status === 403 ? 'You are not allowed to manage staff.' : 'Could not load staff. Try again.';
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
        cell(user.enabled === false ? 'Disabled' : 'Active'));
      const actions = document.createElement('td');
      actions.append(button('Places', () => openPlaces(user)), button('Reset password', () => resetPassword(user)),
        button(user.enabled === false ? 'Enable' : 'Disable', () => toggle(user)),
        jobSelect(user, job));
      tr.append(actions);
      tbody.append(tr);
    }
    $('list-state').textContent = rows.length ? '' : 'No staff found.';
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
    s.setAttribute('aria-label', 'Change job for ' + user.username);
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
      notice(user.username + ' is now ' + to.name + '. It takes effect at their next login (at most 12 hours).');
      await loadPeople();
    } catch (e) {
      notice('Could not change the job: ' + (e.detail || e.message));
    }
  }

  async function toggle(user) {
    const disabling = user.enabled !== false;
    if (disabling && !confirm('Disable ' + user.username + '? They will be locked out of every application within 6 minutes.')) return;
    try {
      // Sign them out everywhere first: Keycloak sends the apps a back-channel logout (Odoo ends the session and an
      // open POS websocket closes). Disabling alone ends Keycloak's sessions silently (KC-11-02).
      if (disabling) await api('POST', '/users/' + user.id + '/logout');
      await api('PUT', '/users/' + user.id, { enabled: !disabling });
      notice(user.username + (disabling ? ' is disabled.' : ' is enabled again.'));
      await loadPeople();
    } catch (e) {
      notice('Could not change ' + user.username + ': ' + (e.detail || e.message));
    }
  }

  async function resetPassword(user) {
    const pw = tempPassword();
    try {
      await api('PUT', '/users/' + user.id + '/reset-password', { type: 'password', value: pw, temporary: true });
      showSlip(user, pw);
    } catch (e) {
      notice('Could not reset the password: ' + (e.detail || e.message));
    }
  }

  // R4 (KC-21-05): locations, lab units and POS shops are user attributes the apps read from the token.
  const PLACES = ['nidan_locations', 'nidan_lab_units', 'nidan_shops'];
  let placesFor = null;   // the full user as Keycloak returned it

  function parsePlaces(text) {
    const values = [...new Set(String(text || '').split(',').map((v) => v.trim()).filter(Boolean))];
    if (values.length > 30) throw new Error('At most 30 per field.');
    for (const v of values) {
      if (v.length > 64 || /[\u0000-\u001f<>]/.test(v)) throw new Error('"' + v.slice(0, 20) + '" is not a valid name.');
    }
    return values;
  }

  async function openPlaces(user) {
    try {
      placesFor = await api('GET', '/users/' + user.id);
    } catch (e) {
      notice('Could not load ' + user.username + ': ' + (e.detail || e.message));
      return;
    }
    const form = $('places-form');
    const attrs = placesFor.attributes || {};
    for (const name of PLACES) form.elements[name].value = (attrs[name] || []).join(', ');
    $('places-user').textContent = placesFor.username;
    $('places-error').hidden = true;
    $('places-dialog').showModal();
  }

  async function savePlaces(form) {
    const err = $('places-error');
    err.hidden = true;
    // Keycloak replaces the whole attribute map on update, so send every attribute it gave us (gender included)
    // with only the three place fields changed. Never roles, groups or credentials.
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
    const body = { username: placesFor.username, firstName: placesFor.firstName, lastName: placesFor.lastName, attributes };
    if (placesFor.email) body.email = placesFor.email;
    try {
      await api('PUT', '/users/' + placesFor.id, body);
      $('places-dialog').close();
      notice('Places saved for ' + placesFor.username + '. They apply at the next login.');
    } catch (e) {
      err.textContent = 'Could not save: ' + (e.detail || e.message);
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
    if (!f.firstName || !f.lastName) problem = 'First and last name are required.';
    else if (!/^[a-z][a-z0-9._-]{2,39}$/.test(username)) problem = 'Username: 3–40 lowercase letters, digits, . _ -';
    else if (RESERVED.has(username) || /^[0-9]+-[0-9]$/.test(username)) problem = 'That username is reserved.';
    else if (!f.gender) problem = 'Gender is required.';
    else if (!jobs.some((j) => j.id === f.job)) problem = 'Choose a job.';
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
      err.textContent = e.status === 409 ? 'That username already exists.'
        : e.detail === 'nidanUsernameReserved' ? 'That username is reserved.'  // realm user-profile pattern (KC-55-03)
          : 'Could not add the person: ' + (e.detail || e.message);
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
      notice('Could not load activity.');
    }
  }

  // ---------------------------------------------------------------- wiring
  /** Header: who is signed in; "All apps" goes to the launcher on 443 of this host (this page is on :9443). */
  async function showWho() {
    try {
      const res = await fetch('/oauth2/userinfo', { cache: 'no-cache', credentials: 'same-origin' });
      if (!res.ok || !(res.headers.get('content-type') || '').includes('application/json')) return;
      const info = await res.json();
      $('who-name').textContent = info.preferredUsername || info.user || '';
      $('to-apps').href = 'https://' + location.hostname + '/';
      $('who').hidden = false;
    } catch (e) { /* header stays without the name */ }
  }

  async function start() {
    showWho();
    try {
      const res = await fetch('/staff/staff-config.json', { cache: 'no-cache' });
      if (!res.ok) throw new Error('HTTP ' + res.status);
      // Only /staff/<name> jobs (KC-21-09); the gateway also refuses any other group id.
      jobs = ((await res.json()).jobs || []).filter((j) => j && j.name && /^[0-9a-f-]{36}$/.test(j.id)
        && j.path === '/staff/' + j.name);
    } catch (e) {
      $('list-state').textContent = 'Staff configuration is missing (staff-config.json).';
      return;
    }
    if (!jobs.length) {   // the placeholder shipped with the image, or a site that never ran scripts/staff-config.py
      $('list-state').textContent = 'No jobs are set up for this hospital yet (staff-config.json). Ask IT to run scripts/staff-config.py.';
      return;
    }
    const filter = $('job-filter');
    const all = document.createElement('option');
    all.value = '';
    all.textContent = 'All jobs';
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
