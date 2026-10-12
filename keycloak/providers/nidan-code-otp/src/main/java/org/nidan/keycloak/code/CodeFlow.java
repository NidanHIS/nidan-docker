package org.nidan.keycloak.code;

import jakarta.ws.rs.core.Response;
import java.security.SecureRandom;
import org.jboss.logging.Logger;
import org.keycloak.forms.login.LoginFormsProvider;
import org.keycloak.models.KeycloakSession;
import org.keycloak.models.RealmModel;
import org.keycloak.models.UserModel;
import org.keycloak.sessions.AuthenticationSessionModel;

/** What the login step and the set-up step share: session notes, sending, the code page. */
final class CodeFlow {
    static final String WAIT = "nidanCodeWait";
    private static final Logger LOG = Logger.getLogger(CodeFlow.class);
    private static final SecureRandom RANDOM = new SecureRandom();

    private CodeFlow() {
    }

    static Codes.Notes notes(AuthenticationSessionModel s) {
        return new Codes.Notes() {
            public String get(String k) { return s.getAuthNote(k); }
            public void set(String k, String v) { s.setAuthNote(k, v); }
            public void remove(String k) { s.removeAuthNote(k); }
        };
    }

    static String prefix(CodeChannel ch) {
        return "nidan.code." + ch.id + ".";
    }

    /** Sends a new code; returns a message key on failure (WAIT = one was sent less than a minute ago). */
    static String sendNew(KeycloakSession session, RealmModel realm, UserModel user, AuthenticationSessionModel as, CodeChannel ch) {
        String code = Codes.issue(notes(as), prefix(ch), System.currentTimeMillis(), RANDOM);
        if (code == null) {
            return WAIT;
        }
        try {
            ch.send(session, realm, user, code);
            return null;
        } catch (Exception e) {
            // no code, token or address in the log: the user id and what failed are enough to follow up
            LOG.warnf("nidan-code-otp: could not send the %s code for user %s (%s)", ch.id, user.getId(), e.getClass().getSimpleName());
            return "nidanCodeSendFailed";
        }
    }

    static Codes.Result check(AuthenticationSessionModel as, CodeChannel ch, String entered) {
        return Codes.check(notes(as), prefix(ch), System.currentTimeMillis(), entered);
    }

    static Response page(LoginFormsProvider form, CodeChannel ch, UserModel user, boolean setup, String error) {
        if (error != null) {
            form.setError(error);
        }
        return form.setAttribute("nidanChannel", ch.id)
                .setAttribute("nidanDestination", Codes.mask(ch.destination(user)))
                .setAttribute("nidanSetup", setup)
                .setAttribute("nidanCanEnter", ch.destination(user) != null)
                .createForm("nidan-code.ftl");
    }
}
