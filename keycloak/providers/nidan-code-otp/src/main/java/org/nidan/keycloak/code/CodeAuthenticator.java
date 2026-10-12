package org.nidan.keycloak.code;

import jakarta.ws.rs.core.MultivaluedMap;
import org.keycloak.authentication.AuthenticationFlowContext;
import org.keycloak.authentication.AuthenticationFlowError;
import org.keycloak.authentication.Authenticator;
import org.keycloak.models.KeycloakSession;
import org.keycloak.models.RealmModel;
import org.keycloak.models.UserModel;

/** Login step: a code by email or SMS, for a person who turned the channel on (CodeSetupAction). */
public class CodeAuthenticator implements Authenticator {
    private final CodeChannel ch;

    CodeAuthenticator(CodeChannel ch) {
        this.ch = ch;
    }

    @Override
    public void authenticate(AuthenticationFlowContext ctx) {
        if (!ch.available(ctx.getRealm())) {
            // turned on, but the hospital's email / SMS settings are gone: fail closed, never password-only
            ctx.challenge(CodeFlow.page(ctx.form(), ch, ctx.getUser(), false, "nidanCodeUnavailable", false));
            return;
        }
        String err = CodeFlow.sendNew(ctx.getSession(), ctx.getRealm(), ctx.getUser(), ctx.getAuthenticationSession(), ch, false);
        // a reload within the minute keeps the code already sent: not an error on first view
        ctx.challenge(CodeFlow.page(ctx.form(), ch, ctx.getUser(), false, CodeFlow.WAIT.equals(err) ? null : err));
    }

    @Override
    public void action(AuthenticationFlowContext ctx) {
        MultivaluedMap<String, String> f = ctx.getHttpRequest().getDecodedFormParameters();
        if (f.containsKey("resend")) {
            String err = CodeFlow.sendNew(ctx.getSession(), ctx.getRealm(), ctx.getUser(), ctx.getAuthenticationSession(), ch, false);
            ctx.challenge(CodeFlow.page(ctx.form(), ch, ctx.getUser(), false, err));
            return;
        }
        switch (CodeFlow.check(ctx.getAuthenticationSession(), ch, false, f.getFirst("code"))) {
            case OK -> ctx.success();
            // failureChallenge counts towards Keycloak's lockout, like a wrong password
            case WRONG -> ctx.failureChallenge(AuthenticationFlowError.INVALID_CREDENTIALS,
                    CodeFlow.page(ctx.form(), ch, ctx.getUser(), false, "nidanCodeWrong"));
            case LOCKED -> ctx.failureChallenge(AuthenticationFlowError.INVALID_CREDENTIALS,
                    CodeFlow.page(ctx.form(), ch, ctx.getUser(), false, "nidanCodeLocked"));
            case EXPIRED, NONE -> ctx.challenge(CodeFlow.page(ctx.form(), ch, ctx.getUser(), false, "nidanCodeExpired"));
        }
    }

    @Override
    public boolean requiresUser() {
        return true;
    }

    @Override
    public boolean configuredFor(KeycloakSession session, RealmModel realm, UserModel user) {
        return ch.configuredFor(user);
    }

    @Override
    public void setRequiredActions(KeycloakSession session, RealmModel realm, UserModel user) {
        // set up from the launcher, never forced (the second step is optional)
    }

    @Override
    public void close() {
    }
}
