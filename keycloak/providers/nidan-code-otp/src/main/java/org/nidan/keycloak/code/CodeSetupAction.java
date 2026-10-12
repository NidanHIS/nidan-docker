package org.nidan.keycloak.code;

import jakarta.ws.rs.core.MultivaluedMap;
import org.keycloak.authentication.InitiatedActionSupport;
import org.keycloak.authentication.RequiredActionContext;
import org.keycloak.authentication.RequiredActionProvider;
import org.keycloak.models.UserModel;

/**
 * Turning email / SMS codes on (launcher "Two-step sign-in", kc_action=nidan-email-code-setup / nidan-sms-code-setup):
 * a code goes to the address or phone the hospital admin entered, and only typing it back turns the channel on.
 * Nobody can point codes elsewhere: the destination is not editable here.
 */
public class CodeSetupAction implements RequiredActionProvider {
    private final CodeChannel ch;

    CodeSetupAction(CodeChannel ch) {
        this.ch = ch;
    }

    @Override
    public InitiatedActionSupport initiatedActionSupport() {
        return InitiatedActionSupport.SUPPORTED;
    }

    @Override
    public void evaluateTriggers(RequiredActionContext ctx) {
        // never added on its own: started by the person from the launcher
    }

    @Override
    public void requiredActionChallenge(RequiredActionContext ctx) {
        UserModel user = ctx.getUser();
        String err;
        if (!ch.available(ctx.getRealm())) {
            err = "nidanCodeUnavailable";
        } else if (ch.destination(user) == null) {
            err = "nidanCodeNoContact";
        } else {
            err = CodeFlow.sendNew(ctx.getSession(), ctx.getRealm(), user, ctx.getAuthenticationSession(), ch);
            if (CodeFlow.WAIT.equals(err)) {
                err = null;
            }
        }
        ctx.challenge(CodeFlow.page(ctx.form(), ch, user, true, err));
    }

    @Override
    public void processAction(RequiredActionContext ctx) {
        MultivaluedMap<String, String> f = ctx.getHttpRequest().getDecodedFormParameters();
        UserModel user = ctx.getUser();
        if (f.containsKey("cancel") || !ch.available(ctx.getRealm()) || ch.destination(user) == null) {
            ctx.cancel();
            return;
        }
        if (f.containsKey("resend")) {
            ctx.challenge(CodeFlow.page(ctx.form(), ch, user, true,
                    CodeFlow.sendNew(ctx.getSession(), ctx.getRealm(), user, ctx.getAuthenticationSession(), ch)));
            return;
        }
        switch (CodeFlow.check(ctx.getAuthenticationSession(), ch, f.getFirst("code"))) {
            case OK -> {
                user.setSingleAttribute(ch.flagAttribute, "true");
                ctx.success();
            }
            case WRONG -> ctx.challenge(CodeFlow.page(ctx.form(), ch, user, true, "nidanCodeWrong"));
            case EXPIRED, NONE -> ctx.challenge(CodeFlow.page(ctx.form(), ch, user, true, "nidanCodeExpired"));
            case LOCKED -> ctx.failure("nidanCodeLocked");
        }
    }

    @Override
    public void close() {
    }
}
