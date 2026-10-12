package org.nidan.keycloak.code;

import java.nio.charset.StandardCharsets;
import java.security.MessageDigest;
import java.security.NoSuchAlgorithmException;
import java.security.SecureRandom;
import java.util.HexFormat;

/**
 * One-time sign-in codes kept in the login session's notes (server side, never in the browser): only a salted
 * SHA-256 of the code is stored, with its expiry, tries and send count. No Keycloak types, so CodeLogicCheck can
 * test it directly.
 */
final class Codes {
    static final int LENGTH = 6;
    static final long TTL_MS = 5 * 60_000;      // a code lives 5 minutes
    static final int MAX_TRIES = 5;             // wrong entries per code, then a new sign-in is needed
    static final long RESEND_MS = 60_000;       // at most one new code a minute
    static final int MAX_SENDS = 3;             // per sign-in: limits SMS cost and mail-bombing

    enum Result { OK, WRONG, EXPIRED, LOCKED, NONE }

    /** The session notes, as a tiny interface (AuthenticationSessionModel notes in Keycloak). */
    interface Notes {
        String get(String key);
        void set(String key, String value);
        void remove(String key);
    }

    private Codes() {
    }

    /** A new code, or null when one was sent less than a minute ago or the send budget is used up. */
    static String issue(Notes n, String prefix, long now, SecureRandom random) {
        long sentAt = num(n.get(prefix + "sentAt"));
        int sends = (int) num(n.get(prefix + "sends"));
        if (sends >= MAX_SENDS || (sentAt > 0 && now - sentAt < RESEND_MS)) {
            return null;
        }
        StringBuilder code = new StringBuilder(LENGTH);
        for (int i = 0; i < LENGTH; i++) {
            code.append(random.nextInt(10));
        }
        byte[] salt = new byte[16];
        random.nextBytes(salt);
        String saltHex = HexFormat.of().formatHex(salt);
        n.set(prefix + "salt", saltHex);
        n.set(prefix + "hash", hash(code.toString(), saltHex));
        n.set(prefix + "exp", Long.toString(now + TTL_MS));
        n.set(prefix + "tries", "0");
        n.set(prefix + "sentAt", Long.toString(now));
        n.set(prefix + "sends", Integer.toString(sends + 1));
        return code.toString();
    }

    /** Checks an entered code. OK clears it (a code works once). */
    static Result check(Notes n, String prefix, long now, String entered) {
        String hash = n.get(prefix + "hash");
        if (hash == null) {
            return Result.NONE;
        }
        int tries = (int) num(n.get(prefix + "tries"));
        if (tries >= MAX_TRIES) {
            return Result.LOCKED;
        }
        if (now > num(n.get(prefix + "exp"))) {
            return Result.EXPIRED;
        }
        String clean = entered == null ? "" : entered.replaceAll("\\s", "");
        boolean same = clean.length() == LENGTH && MessageDigest.isEqual(
                hash.getBytes(StandardCharsets.US_ASCII),
                hash(clean, n.get(prefix + "salt")).getBytes(StandardCharsets.US_ASCII));
        if (same) {
            for (String k : new String[] {"hash", "salt", "exp", "tries"}) {
                n.remove(prefix + k);
            }
            return Result.OK;
        }
        n.set(prefix + "tries", Integer.toString(tries + 1));
        return tries + 1 >= MAX_TRIES ? Result.LOCKED : Result.WRONG;
    }

    static String hash(String code, String saltHex) {
        try {
            MessageDigest d = MessageDigest.getInstance("SHA-256");
            d.update(HexFormat.of().parseHex(saltHex == null ? "" : saltHex));
            return HexFormat.of().formatHex(d.digest(code.getBytes(StandardCharsets.US_ASCII)));
        } catch (NoSuchAlgorithmException e) {
            throw new IllegalStateException(e);
        }
    }

    /** "s***@example.org" / "******5678": enough to recognise, not enough to read off a screen. */
    static String mask(String destination) {
        if (destination == null || destination.isBlank()) {
            return "";
        }
        int at = destination.indexOf('@');
        if (at > 0) {
            return destination.charAt(0) + "***" + destination.substring(at);
        }
        String digits = destination.replaceAll("\\D", "");
        return digits.length() <= 4 ? "****" : "*".repeat(digits.length() - 4) + digits.substring(digits.length() - 4);
    }

    private static long num(String s) {
        try {
            return s == null ? 0 : Long.parseLong(s);
        } catch (NumberFormatException e) {
            return 0;
        }
    }
}
