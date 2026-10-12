package org.nidan.keycloak.code;

import java.net.URI;
import java.net.URLEncoder;
import java.net.http.HttpClient;
import java.net.http.HttpRequest;
import java.net.http.HttpResponse;
import java.nio.charset.StandardCharsets;
import java.time.Duration;
import java.util.Map;
import java.util.stream.Collectors;
import org.keycloak.email.EmailSenderProvider;
import org.keycloak.models.KeycloakSession;
import org.keycloak.models.RealmModel;
import org.keycloak.models.UserModel;

/**
 * Where a sign-in code goes. The destination is the person's email or the "phone" attribute, both set only by a
 * hospital admin (user profile: admin edit), so a stolen password cannot redirect codes. The person turns a channel
 * on for themselves (flag attribute, set by CodeSetupAction after a code reached them).
 *
 * Settings live in the realm (realm import, from env): SMTP for email; realm attributes for SMS
 * (nidanSmsProvider = sparrow | aakash | webhook | none, nidanSmsUrl, nidanSmsToken, nidanSmsFrom).
 */
enum CodeChannel {
    EMAIL("email", "nidan_2fa_email"),
    SMS("sms", "nidan_2fa_sms");

    static final String PHONE = "phone";
    private static final HttpClient HTTP = HttpClient.newBuilder().connectTimeout(Duration.ofSeconds(5)).build();

    final String id;
    final String flagAttribute;

    CodeChannel(String id, String flagAttribute) {
        this.id = id;
        this.flagAttribute = flagAttribute;
    }

    String destination(UserModel user) {
        String d = this == EMAIL ? user.getEmail() : user.getFirstAttribute(PHONE);
        return d == null || d.isBlank() ? null : d.trim();
    }

    /** The realm has what this channel needs (SMTP host / an SMS provider). */
    boolean available(RealmModel realm) {
        if (this == EMAIL) {
            Map<String, String> smtp = realm.getSmtpConfig();
            return smtp != null && notBlank(smtp.get("host")) && notBlank(smtp.get("from"));
        }
        String p = realm.getAttribute("nidanSmsProvider");
        return notBlank(p) && !"none".equals(p) && notBlank(realm.getAttribute("nidanSmsToken"));
    }

    /** The person turned this channel on, it has a destination and the realm can send: then the login asks. */
    boolean configuredFor(RealmModel realm, UserModel user) {
        return available(realm) && destination(user) != null && "true".equals(user.getFirstAttribute(flagAttribute));
    }

    /** Sends the code; throws on any failure (the caller shows "could not send"). Never logs the code or token. */
    void send(KeycloakSession session, RealmModel realm, UserModel user, String code) throws Exception {
        String name = realm.getDisplayName() == null ? realm.getName() : realm.getDisplayName();
        String text = "Your " + name + " sign-in code is " + code + ". It expires in 5 minutes. "
                + "If you did not try to sign in, tell your hospital admin.";
        if (this == EMAIL) {
            session.getProvider(EmailSenderProvider.class)
                    .send(realm.getSmtpConfig(), destination(user), name + " sign-in code", text, null);
            return;
        }
        sendSms(realm, destination(user), text);
    }

    private static void sendSms(RealmModel realm, String phone, String text) throws Exception {
        String provider = realm.getAttribute("nidanSmsProvider");
        String token = realm.getAttribute("nidanSmsToken");
        String url = realm.getAttribute("nidanSmsUrl");
        String local = phone.replaceAll("\\D", "").replaceFirst("^977", "");   // Nepali gateways take 98XXXXXXXX
        HttpRequest.Builder req;
        switch (provider) {
            // Sparrow SMS v2: POST form token, from, to, text (https://docs.sparrowsms.com)
            case "sparrow" -> req = form(notBlank(url) ? url : "https://api.sparrowsms.com/v2/sms/",
                    Map.of("token", token, "from", nz(realm.getAttribute("nidanSmsFrom")), "to", local, "text", text));
            // Aakash SMS v3: POST form auth_token, to, text (https://aakashsms.com)
            case "aakash" -> req = form(notBlank(url) ? url : "https://sms.aakashsms.com/sms/v3/send",
                    Map.of("auth_token", token, "to", local, "text", text));
            // Any other gateway behind a small relay: POST JSON {to, text} with a bearer token
            case "webhook" -> req = HttpRequest.newBuilder(URI.create(url))
                    .header("Content-Type", "application/json")
                    .header("Authorization", "Bearer " + token)
                    .POST(HttpRequest.BodyPublishers.ofString("{\"to\":" + json(phone) + ",\"text\":" + json(text) + "}"));
            default -> throw new IllegalStateException("SMS provider not set");
        }
        HttpResponse<Void> res = HTTP.send(req.timeout(Duration.ofSeconds(10)).build(), HttpResponse.BodyHandlers.discarding());
        if (res.statusCode() / 100 != 2) {
            throw new IllegalStateException("SMS gateway answered " + res.statusCode());
        }
    }

    private static HttpRequest.Builder form(String url, Map<String, String> fields) {
        String body = fields.entrySet().stream()
                .map(e -> URLEncoder.encode(e.getKey(), StandardCharsets.UTF_8) + "=" + URLEncoder.encode(e.getValue(), StandardCharsets.UTF_8))
                .collect(Collectors.joining("&"));
        return HttpRequest.newBuilder(URI.create(url))
                .header("Content-Type", "application/x-www-form-urlencoded")
                .POST(HttpRequest.BodyPublishers.ofString(body));
    }

    private static String json(String s) {
        StringBuilder b = new StringBuilder("\"");
        for (char c : s.toCharArray()) {
            switch (c) {
                case '"' -> b.append("\\\"");
                case '\\' -> b.append("\\\\");
                default -> {
                    if (c < 0x20) {
                        b.append(String.format("\\u%04x", (int) c));
                    } else {
                        b.append(c);
                    }
                }
            }
        }
        return b.append('"').toString();
    }

    private static boolean notBlank(String s) {
        return s != null && !s.isBlank();
    }

    private static String nz(String s) {
        return s == null ? "" : s;
    }
}
