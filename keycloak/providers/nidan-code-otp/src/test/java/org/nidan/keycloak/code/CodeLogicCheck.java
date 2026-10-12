package org.nidan.keycloak.code;

import java.security.SecureRandom;
import java.util.HashMap;
import java.util.Map;

/** Self-check for Codes (run by build.sh with -ea): issue, resend limits, tries, expiry, single use, masking. */
public final class CodeLogicCheck {
    public static void main(String[] args) {
        Map<String, String> m = new HashMap<>();
        Codes.Notes n = new Codes.Notes() {
            public String get(String k) { return m.get(k); }
            public void set(String k, String v) { m.put(k, v); }
            public void remove(String k) { m.remove(k); }
        };
        SecureRandom r = new SecureRandom();
        long t = 1_000_000;
        String p = "nidan.code.sms.";

        String code = Codes.issue(n, p, t, r);
        check(code != null && code.matches("\\d{6}"), "a 6-digit code");
        check(!m.containsValue(code), "the code itself is never stored");
        check(Codes.issue(n, p, t + 1_000, r) == null, "no new code within a minute");

        check(Codes.check(n, p, t, "000000".equals(code) ? "111111" : "000000") == Codes.Result.WRONG, "wrong code");
        check(Codes.check(n, p, t, " " + code.substring(0, 3) + " " + code.substring(3)) == Codes.Result.OK, "spaces ignored");
        check(Codes.check(n, p, t, code) == Codes.Result.NONE, "a code works once");

        String c2 = Codes.issue(n, p, t + 61_000, r);
        check(c2 != null, "a new code after a minute");
        check(Codes.check(n, p, t + 61_000 + Codes.TTL_MS + 1, c2) == Codes.Result.EXPIRED, "expires after 5 minutes");

        String c3 = Codes.issue(n, p, t + 200_000, r);
        check(c3 != null, "third send allowed");
        check(Codes.issue(n, p, t + 400_000, r) == null, "no fourth send in one sign-in");
        String bad = "000000".equals(c3) ? "111111" : "000000";
        for (int i = 0; i < Codes.MAX_TRIES - 1; i++) {
            check(Codes.check(n, p, t + 200_000, bad) == Codes.Result.WRONG, "wrong " + i);
        }
        check(Codes.check(n, p, t + 200_000, bad) == Codes.Result.LOCKED, "locked after 5 wrong");
        check(Codes.check(n, p, t + 200_000, c3) == Codes.Result.LOCKED, "even the right code once locked");

        check(Codes.mask("sita@example.org").equals("s***@example.org"), "mask email");
        check(Codes.mask("+977 9812345678").equals("*********5678"), "mask phone: " + Codes.mask("+977 9812345678"));
        // the "channel on" flag: keyed, so an admin who knows the address cannot compute it (review 2026-10-13)
        String plain = CodeChannel.confirmedValue("Sita@Example.org", "");
        String keyed = CodeChannel.confirmedValue("Sita@Example.org", "site-secret-0123456789abcdef0123456789");
        check(plain.equals(Codes.hash("sita@example.org", "")), "no key: the plain hash, as before");
        check(keyed.matches("[0-9a-f]{64}") && !keyed.equals(plain), "keyed flag differs and fits the profile pattern");
        check(keyed.equals(CodeChannel.confirmedValue(" sita@example.org ", "site-secret-0123456789abcdef0123456789")),
                "same address, same flag");
        check(!keyed.equals(CodeChannel.confirmedValue("sita@example.org", "another-secret-0123456789abcdef01234")),
                "another key, another flag");
        System.out.println("nidan-code-otp logic: OK");
    }

    private static void check(boolean ok, String what) {
        if (!ok) {
            throw new AssertionError(what);
        }
    }
}
