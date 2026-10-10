"""Restore drill helper (KC-28-06): create a staff user, or prove she can log in through Keycloak's login form.

  python3 -I drill-login.py create BASE USERNAME
  python3 -I drill-login.py login  BASE USERNAME      (exit 0 = Keycloak returned an authorization code)
  python3 -I drill-login.py disable BASE USERNAME
"""
import json
import re
import sys
import urllib.error
import urllib.parse
import urllib.request

PASSWORD = "Drill-Pass-2026"


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *a, **k):
        return None


OPEN = urllib.request.build_opener(NoRedirect)


def http(method, url, body=None, token=None, form=None, cookie=None):
    h, data = {}, None
    if form is not None:
        data, h["Content-Type"] = urllib.parse.urlencode(form).encode(), "application/x-www-form-urlencoded"
    elif body is not None:
        data, h["Content-Type"] = json.dumps(body).encode(), "application/json"
    if token:
        h["Authorization"] = "Bearer " + token
    if cookie:
        h["Cookie"] = cookie
    try:
        r = OPEN.open(urllib.request.Request(url, data=data, method=method, headers=h))
    except urllib.error.HTTPError as e:
        r = e
    return getattr(r, "status", None) or r.code, r.read().decode(), r.headers


def main(argv):
    action, base, username = argv[1], argv[2].rstrip("/"), argv[3]
    if action in ("create", "disable"):
        tok = json.loads(http("POST", base + "/realms/master/protocol/openid-connect/token", form={
            "grant_type": "password", "client_id": "admin-cli", "username": "admin", "password": "harness-admin-password"})[1])["access_token"]
    if action == "disable":
        uid = json.loads(http("GET", base + f"/admin/realms/nidan/users?username={username}&exact=true", token=tok)[1])[0]["id"]
        st = http("PUT", base + f"/admin/realms/nidan/users/{uid}", {"enabled": False}, tok)[0]
        print(f"disabled {username}: {st}")
        return 0 if st == 204 else 1
    if action == "create":
        st, _, h = http("POST", base + "/admin/realms/nidan/users", {"username": username, "firstName": "Sita", "lastName": "Rai",
                        "enabled": True, "attributes": {"gender": ["F"]}, "groups": ["/staff/Nurse"]}, tok)
        uid = h["Location"].rsplit("/", 1)[1]
        st2 = http("PUT", base + f"/admin/realms/nidan/users/{uid}/reset-password", {"type": "password", "value": PASSWORD, "temporary": False}, tok)[0]
        print(f"created {username}: {st}, password {st2}")
        return 0 if (st, st2) == (201, 204) else 1
    q = urllib.parse.urlencode({"client_id": "nidan-gateway", "redirect_uri": "https://nidan.example/oauth2/callback", "response_type": "code",
                                "scope": "openid", "state": "drill", "code_challenge": "E9Melhoa2OwvFrEMTJguCHaoeK1t8URWbuGJSstw-cM",
                                "code_challenge_method": "S256"})
    st, body, h = http("GET", base + "/realms/nidan/protocol/openid-connect/auth?" + q)
    cookie = "; ".join(c.split(";")[0] for c in h.get_all("Set-Cookie") or [])
    action_url = re.search(r'<form[^>]*action="([^"]+)"', body).group(1).replace("&amp;", "&")
    st, _, h = http("POST", action_url, form={"username": username, "password": PASSWORD}, cookie=cookie)
    ok = st == 302 and "code=" in (h.get("Location") or "")
    print(f"login {username} at {base}: {'OK' if ok else 'FAILED'} ({st})")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv))
