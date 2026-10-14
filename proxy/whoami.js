// The launcher's "Signed in as <name>": the person's display name from the access token the gateway's auth check
// receives from oauth2-proxy (X-Auth-Request-Access-Token, never sent to the browser). Only the name leaves: given
// and family name (or Keycloak's "name"), at most 120 characters; {} when there is none (central login off, no
// session). The token is not verified here: it came from oauth2-proxy on this request, not from the browser.
// Loaded by nginx (njs, js_import in nginx.conf).

function displayName(token) {
    try {
        var payload = JSON.parse(Buffer.from((token || '').split('.')[1] || '', 'base64url').toString());
        var name = [payload.given_name, payload.family_name].filter(function (v) { return typeof v === 'string' && v; })
            .join(' ') || (typeof payload.name === 'string' ? payload.name : '');
        return name ? { name: name.slice(0, 120) } : {};
    } catch (e) {
        return {};
    }
}

function name(r) {
    r.headersOut['Content-Type'] = 'application/json';
    r.return(200, JSON.stringify(displayName(r.variables.nidan_access_token)));
}

export default { name: name, displayName: displayName };
