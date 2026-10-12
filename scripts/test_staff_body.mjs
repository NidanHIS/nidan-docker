// Self-test for proxy/staff-body.js (the Staff page body allow-list): node scripts/test_staff_body.mjs
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';

// staff-body.js is an njs module ("export default"); load it the same way in node
const src = readFileSync(new URL('../proxy/staff-body.js', import.meta.url), 'utf8');
const { verdict } = (await import('data:text/javascript;base64,' + Buffer.from(src).toString('base64'))).default;

const U = '0f8e9a4c-1b2d-4e3f-8a9b-0c1d2e3f4a5b', G = '9e1b374d-7e85-4b7b-ae6e-7f96dffb4af0';
const j = (o) => JSON.stringify(o);
const person = { username: 'sita', firstName: 'Sita', lastName: 'Rai', enabled: true, attributes: { gender: ['F'] },
  groups: ['/staff/Nurse'], requiredActions: ['UPDATE_PASSWORD'] };

// what the page sends: allowed
assert.ok(verdict('POST', '/users', j(person)));
assert.ok(verdict('POST', '/users', j({ ...person, email: 'a@b.org', attributes: { gender: ['F'], phone: ['+977 98'] } })));
assert.ok(verdict('PUT', `/users/${U}/reset-password`, j({ type: 'password', value: 'x-y-z-1', temporary: true })));
assert.ok(verdict('PUT', `/users/${U}`, j({ enabled: false })));
assert.ok(verdict('PUT', `/users/${U}`, j({ username: 'sita', firstName: 'S', lastName: 'R', email: 'a@b.org',
  attributes: { gender: ['F'], nidan_shops: ['Shop A'], nidan_2fa_sms: ['ab'] } })));
assert.ok(verdict('PUT', `/users/${U}/groups/${G}`, ''));
assert.ok(verdict('DELETE', `/users/${U}/groups/${G}`, ''));
assert.ok(verdict('POST', `/users/${U}/logout`, ''));
assert.ok(verdict('GET', `/users/${U}`, ''));

// what only developer tools could send: refused
assert.ok(!verdict('POST', '/users', j({ ...person, credentials: [{ type: 'password', value: 'mine', temporary: false }] })), 'own password');
assert.ok(!verdict('POST', '/users', j({ ...person, requiredActions: [] })), 'no password change');
assert.ok(!verdict('POST', '/users', j({ ...person, emailVerified: true })), 'extra field');
assert.ok(!verdict('POST', '/users', j({ ...person, groups: ['/staff/Nurse', '/hospital-admin'] })), 'second group');
assert.ok(!verdict('POST', '/users', j({ ...person, groups: ['/hospital-admin'] })), 'admin group');
assert.ok(!verdict('POST', '/users', j({ ...person, groups: ['/staff/Nurse/x'] })), 'nested path');
assert.ok(!verdict('POST', '/users', j({ ...person, realmRoles: ['admin'] })), 'roles');
assert.ok(!verdict('POST', '/users', j({ ...person, attributes: { gender: ['F'], nidan_extra: ['x'] } })), 'unknown attribute');
assert.ok(!verdict('POST', '/users', j({ ...person, attributes: { gender: 'F' } })), 'attribute not a list');
assert.ok(!verdict('POST', '/users', j({ ...person, enabled: false })), 'created disabled');
assert.ok(!verdict('PUT', `/users/${U}/reset-password`, j({ type: 'password', value: 'mine', temporary: false })), 'permanent reset');
assert.ok(!verdict('PUT', `/users/${U}/reset-password`, j({ type: 'otp', value: '1', temporary: true })), 'other credential');
assert.ok(!verdict('PUT', `/users/${U}`, j({ enabled: false, requiredActions: [] })), 'enable + more');
assert.ok(!verdict('PUT', `/users/${U}`, j({ firstName: 'S', credentials: [] })), 'credentials on update');
assert.ok(!verdict('PUT', `/users/${U}`, j({ firstName: 'S', groups: ['/hospital-admin'] })), 'groups on update');
assert.ok(!verdict('PUT', `/users/${U}/groups/${G}`, j({ x: 1 })), 'body on a job move');
assert.ok(!verdict('POST', `/users/${U}/logout`, '{}'), 'body on logout');
assert.ok(!verdict('POST', '/users', 'not json'), 'not JSON');
assert.ok(!verdict('POST', '/users', j([person])), 'array');
assert.ok(!verdict('PATCH', `/users/${U}`, j({ enabled: true })), 'other method');
console.log('staff-body: OK');
