package org.nidan.keycloak.code;

import org.keycloak.Config;
import org.keycloak.authentication.RequiredActionFactory;
import org.keycloak.authentication.RequiredActionProvider;
import org.keycloak.models.KeycloakSession;
import org.keycloak.models.KeycloakSessionFactory;

/** nidan-email-code-setup / nidan-sms-code-setup. */
public abstract class CodeSetupActionFactory implements RequiredActionFactory {
    abstract CodeChannel channel();

    @Override
    public String getId() {
        return "nidan-" + channel().id + "-code-setup";
    }

    @Override
    public String getDisplayText() {
        return channel() == CodeChannel.EMAIL ? "Set up email sign-in codes" : "Set up SMS sign-in codes";
    }

    @Override
    public RequiredActionProvider create(KeycloakSession session) {
        return new CodeSetupAction(channel());
    }

    @Override
    public void init(Config.Scope config) {
    }

    @Override
    public void postInit(KeycloakSessionFactory factory) {
    }

    @Override
    public void close() {
    }

    public static class EmailCode extends CodeSetupActionFactory {
        @Override
        CodeChannel channel() {
            return CodeChannel.EMAIL;
        }
    }

    public static class SmsCode extends CodeSetupActionFactory {
        @Override
        CodeChannel channel() {
            return CodeChannel.SMS;
        }
    }
}
