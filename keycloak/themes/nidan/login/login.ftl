<#-- Copied from keycloak.v2 login.ftl, Keycloak 26.8.0 (scripts/check-theme.py checks this matches the compose image).
     Nidan change (KC-17-02), nothing else: a page-level error with no field error - a disabled account that typed the
     RIGHT password - is shown under the username field like any wrong password, instead of as a banner above the form,
     so the page does not tell someone who holds the password that the account exists but is disabled.
     Second Nidan change (UI review 2026-10-12): with self-service reset off, the info area says who resets a
     password (nidanForgotPassword) instead of showing nothing; it also carries the language dropdown and the copyright
     line (nidan.css places them at the top right and the foot of the page). -->
<#import "template.ftl" as layout>
<#import "field.ftl" as field>
<#import "buttons.ftl" as buttons>
<#import "social-providers.ftl" as identityProviders>
<#import "passkeys.ftl" as passkeys>
<#assign nidanPageError = !usernameHidden?? && message?has_content && message.type = 'error' && !messagesPerField.existsError('username','password')>
<@layout.registrationLayout displayMessage=!messagesPerField.existsError('username','password') && !nidanPageError displayInfo=realm.password; section>
<!-- template: login.ftl -->

    <#if section = "header">
        ${msg("loginAccountTitle")}
    <#elseif section = "form">
        <div id="kc-form">
          <div id="kc-form-wrapper">
            <#if realm.password>
                <form id="kc-form-login" class="${properties.kcFormClass!}" onsubmit="login.disabled = true; return true;" action="${url.loginAction}" method="post" novalidate="novalidate">
                    <#if !usernameHidden??>
                        <#assign label>
                            <#if !realm.loginWithEmailAllowed>${msg("username")}<#elseif !realm.registrationEmailAsUsername>${msg("usernameOrEmail")}<#else>${msg("email")}</#if>
                        </#assign>
                        <@field.input name="username" label=label error=nidanPageError?then(message.summary, messagesPerField.getFirstError('username','password'))
                            autofocus=true autocomplete="${(enableWebAuthnConditionalUI?has_content)?then('username webauthn', 'username')}" value=login.username!'' />
                        <@field.password name="password" label=msg("password") error="" forgotPassword=realm.resetPasswordAllowed autofocus=usernameHidden?? autocomplete="current-password">
                            <#if realm.rememberMe && !usernameHidden??>
                                <@field.checkbox name="rememberMe" label=msg("rememberMe") value=login.rememberMe?? />
                            </#if>
                        </@field.password>
                    <#else>
                        <@field.password name="password" label=msg("password") forgotPassword=realm.resetPasswordAllowed autofocus=usernameHidden?? autocomplete="current-password">
                            <#if realm.rememberMe && !usernameHidden??>
                                <@field.checkbox name="rememberMe" label=msg("rememberMe") value=login.rememberMe?? />
                            </#if>
                        </@field.password>
                    </#if>

                    <input type="hidden" id="id-hidden-input" name="credentialId" <#if auth.selectedCredential?has_content>value="${auth.selectedCredential}"</#if>/>
                    <@buttons.loginButton />
                </form>
            </#if>
            </div>
        </div>
        <@passkeys.conditionalUIData />
    <#elseif section = "socialProviders" >
        <#if realm.password && social.providers?? && social.providers?has_content>
            <@identityProviders.show social=social/>
        </#if>
    <#elseif section = "info" >
        <#if !realm.resetPasswordAllowed>
            <p id="nidan-forgot-password">${msg("nidanForgotPassword")}</p>
        </#if>
        <#if realm.internationalizationEnabled && locale.supported?size gt 1>
            <div id="nidan-locale">
                <select id="nidan-locale-select" aria-label="${msg("languages")}" onchange="if (this.value) { document.cookie = 'nidan_locale=' + this.selectedOptions[0].lang + '; Path=/; Max-Age=31536000; SameSite=Lax; Secure'; window.location.href = this.value; }">
                    <#list locale.supported as l>
                        <option value="${l.url}" lang="${l.languageTag}"<#if l.languageTag == locale.currentLanguageTag> selected</#if>>${l.label}</option>
                    </#list>
                </select>
            </div>
        </#if>
        <p id="nidan-copyright">${msg("nidanCopyright", .now?string("yyyy"), realm.displayName!realm.name)}</p>
        <#if realm.password && realm.registrationAllowed && !registrationDisabled??>
            <div id="kc-registration-container">
                <div id="kc-registration">
                    <span>${msg("noAccount")} <a href="${url.registrationUrl}">${msg("doRegister")}</a></span>
                </div>
            </div>
        </#if>
    </#if>

</@layout.registrationLayout>
