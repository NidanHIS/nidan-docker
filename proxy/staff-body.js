// Staff page admin API: the request bodies a hospital admin may send to Keycloak (review 2026-10-13). The page's
// own rules (a new person gets one job and must choose a password; a reset is temporary) were advisory: from the
// browser's developer tools an admin could create a person with a permanent password they know, or reset someone's
// password to a permanent one, and then use that account. Loaded by nginx (njs, js_import in nginx.conf); the gateway
// already restricts methods and paths ($staff_api_target), this restricts the bodies. Anything else: 403.
// Self-test: node scripts/test_staff_body.mjs

// the user-profile attributes the Staff page reads and writes (keycloak/realm/base/nidan.yaml)
var ATTRIBUTES = ['gender', 'phone', 'nidan_shops', 'nidan_locations', 'nidan_lab_units', 'nidan_2fa_email', 'nidan_2fa_sms'];
var UUID = '[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}';

function onlyKeys(o, keys) {
    return o !== null && typeof o === 'object' && !Array.isArray(o)
        && Object.keys(o).every(function (k) { return keys.indexOf(k) >= 0; });
}

function attributesOk(a) {
    return a === undefined || (onlyKeys(a, ATTRIBUTES) && Object.keys(a).every(function (k) {
        return Array.isArray(a[k]) && a[k].every(function (v) { return typeof v === 'string'; });
    }));
}

function strings(o, keys) {
    return keys.every(function (k) { return o[k] === undefined || typeof o[k] === 'string'; });
}

// method, the Keycloak path the allow-list produced ($staff_api_target), the raw body -> may it go to Keycloak?
function verdict(method, target, text) {
    var empty = !text || text.length === 0;
    if (method === 'GET' || method === 'DELETE' || new RegExp('^/users/' + UUID + '/(logout|groups/' + UUID + ')$').test(target)) {
        return empty;                       // reads, job moves and sign-outs carry no body
    }
    var b;
    try {
        b = JSON.parse(text);
    } catch (e) {
        return false;
    }
    if (method === 'POST' && target === '/users') {
        // a new person: names, gender/phone, one /staff job, and the password is theirs to choose
        return onlyKeys(b, ['username', 'firstName', 'lastName', 'enabled', 'email', 'attributes', 'groups', 'requiredActions'])
            && strings(b, ['username', 'firstName', 'lastName', 'email']) && b.enabled === true
            && Array.isArray(b.groups) && b.groups.length === 1 && /^\/staff\/[^/]+$/.test(b.groups[0])
            && JSON.stringify(b.requiredActions) === '["UPDATE_PASSWORD"]' && attributesOk(b.attributes);
    }
    if (method === 'PUT' && new RegExp('^/users/' + UUID + '/reset-password$').test(target)) {
        return onlyKeys(b, ['type', 'value', 'temporary']) && b.type === 'password' && b.temporary === true
            && typeof b.value === 'string' && b.value.length > 0;
    }
    if (method === 'PUT' && new RegExp('^/users/' + UUID + '$').test(target)) {
        // enable / disable, or the details (names, email, phone, places)
        return (onlyKeys(b, ['enabled']) && typeof b.enabled === 'boolean')
            || (onlyKeys(b, ['username', 'firstName', 'lastName', 'email', 'attributes'])
                && strings(b, ['username', 'firstName', 'lastName', 'email']) && attributesOk(b.attributes));
    }
    return false;
}

function check(r) {
    if (verdict(r.method, r.variables.staff_api_target, r.requestText)) {
        r.internalRedirect('@staff_api');
    } else {
        r.return(403);
    }
}

export default { check: check, verdict: verdict };
