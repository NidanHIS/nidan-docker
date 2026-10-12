package org.nidan.keycloak.code;

import java.util.List;
import org.keycloak.Config;
import org.keycloak.authentication.Authenticator;
import org.keycloak.authentication.AuthenticatorFactory;
import org.keycloak.models.AuthenticationExecutionModel.Requirement;
import org.keycloak.models.KeycloakSession;
import org.keycloak.models.KeycloakSessionFactory;
import org.keycloak.provider.ProviderConfigProperty;

/** nidan-email-code / nidan-sms-code (see EmailCode / SmsCode below). */
public abstract class CodeAuthenticatorFactory implements AuthenticatorFactory {
    abstract CodeChannel channel();

    @Override
    public String getId() {
        return "nidan-" + channel().id + "-code";
    }

    @Override
    public Authenticator create(KeycloakSession session) {
        return new CodeAuthenticator(channel());
    }

    @Override
    public String getDisplayType() {
        return channel() == CodeChannel.EMAIL ? "Email code" : "SMS code";
    }

    @Override
    public String getReferenceCategory() {
        return "nidan-code";
    }

    @Override
    public boolean isConfigurable() {
        return false;
    }

    @Override
    public Requirement[] getRequirementChoices() {
        return new Requirement[] {Requirement.REQUIRED, Requirement.ALTERNATIVE, Requirement.DISABLED};
    }

    @Override
    public boolean isUserSetupAllowed() {
        return false;
    }

    @Override
    public String getHelpText() {
        return "A 6-digit code sent to the email / phone a hospital admin entered (Nidan).";
    }

    @Override
    public List<ProviderConfigProperty> getConfigProperties() {
        return List.of();
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

    public static class EmailCode extends CodeAuthenticatorFactory {
        @Override
        CodeChannel channel() {
            return CodeChannel.EMAIL;
        }
    }

    public static class SmsCode extends CodeAuthenticatorFactory {
        @Override
        CodeChannel channel() {
            return CodeChannel.SMS;
        }
    }
}
