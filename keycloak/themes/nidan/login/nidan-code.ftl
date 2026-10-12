<#-- Written for the keycloak.v2 layout, field and button macros of Keycloak 26.8.0 (re-check on upgrade). Nidan email / SMS sign-in code (keycloak/providers/nidan-code-otp): the login step and its set-up.
     Plain ${} output only (auto-escaped): no ?no_esc (check-theme.py). -->
<#import "template.ftl" as layout>
<#import "field.ftl" as field>
<#import "buttons.ftl" as buttons>
<@layout.registrationLayout displayMessage=true; section>
    <#if section="header">
        ${msg(nidanSetup?then("nidanCodeSetupTitle", "doLogIn"))}
    <#elseif section="form">
        <form id="kc-nidan-code-form" class="${properties.kcFormClass!}" action="${url.loginAction}" method="post">
            <#if nidanCanEnter>
                <p id="nidan-code-sent">${msg((nidanChannel == "email")?then("nidanCodeSentEmail", "nidanCodeSentSms"), nidanDestination)}</p>
                <#if nidanSetup><p>${msg("nidanCodeSetupHelp")}</p></#if>
                <@field.input name="code" label=msg("nidanCodeLabel") autocomplete="one-time-code" fieldName="code" autofocus=true />
                <@buttons.actionGroup>
                    <@buttons.button id="kc-login" name="login" label=nidanSetup?then("nidanCodeTurnOn", "doLogIn") />
                    <@buttons.button id="nidan-code-resend" name="resend" label="nidanCodeResend" type="secondary" />
                    <#if nidanSetup><@buttons.button id="nidan-code-cancel" name="cancel" label="doCancel" type="secondary" /></#if>
                </@buttons.actionGroup>
            <#else>
                <@buttons.actionGroup>
                    <@buttons.button id="nidan-code-cancel" name="cancel" label="doCancel" type="secondary" />
                </@buttons.actionGroup>
            </#if>
        </form>
    </#if>
</@layout.registrationLayout>
