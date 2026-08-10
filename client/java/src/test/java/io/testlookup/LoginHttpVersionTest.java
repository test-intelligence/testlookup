package io.testlookup;

import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Test;

import com.sun.net.httpserver.HttpServer;

import java.io.OutputStream;
import java.net.InetSocketAddress;
import java.nio.charset.StandardCharsets;
import java.util.concurrent.atomic.AtomicReference;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertNotNull;
import static org.junit.jupiter.api.Assertions.assertTrue;

/**
 * {@code login()} must speak HTTP/1.1, like every other request in the SDK.
 *
 * <p>{@code HttpClient.newHttpClient()} defaults to {@code HTTP_2}. Over plain
 * {@code http://} that attempts an h2c upgrade, which Traefik — and reverse
 * proxies generally — reject. The rejection surfaces as a bare
 * {@code HTTP 400: Invalid HTTP request received.} with nothing pointing at the
 * protocol. Measured against the live deployment:
 *
 * <pre>
 *   HTTP_2   -&gt; HTTP 400 "Invalid HTTP request received."
 *   HTTP_1_1 -&gt; HTTP 200, token returned
 * </pre>
 *
 * <p>The constructor already pinned {@code HTTP_1_1} for the instance client,
 * so sessions and event batches were fine. This static helper was the one that
 * was missed — which meant {@code login()}, the documented entry point, was the
 * only call in the SDK that could not succeed behind a proxy. Go, JS, Python
 * and the CLI were all unaffected because they use HTTP/1.1.
 *
 * <p>The test asserts on the version the server actually observes, rather than
 * on the source, so it fails for the original reason rather than on a string
 * match.
 */
class LoginHttpVersionTest {

    @Test
    @DisplayName("login() negotiates HTTP/1.1, not an h2c upgrade")
    void loginUsesHttp11() throws Exception {
        AtomicReference<String> observedProtocol = new AtomicReference<>();
        AtomicReference<String> observedUpgrade = new AtomicReference<>();

        HttpServer server = HttpServer.create(new InetSocketAddress("127.0.0.1", 0), 0);
        server.createContext("/api/v1/auth/login", exchange -> {
            observedProtocol.set(exchange.getProtocol());
            // An h2c upgrade attempt announces itself in the request headers;
            // a proxy that refuses it is what produced the measured 400.
            observedUpgrade.set(exchange.getRequestHeaders().getFirst("Upgrade"));

            byte[] body = "{\"access_token\":\"tok\"}".getBytes(StandardCharsets.UTF_8);
            exchange.getResponseHeaders().add("Content-Type", "application/json");
            exchange.sendResponseHeaders(200, body.length);
            try (OutputStream os = exchange.getResponseBody()) {
                os.write(body);
            }
        });
        server.start();
        try {
            String base = "http://127.0.0.1:" + server.getAddress().getPort();
            String token = TestLookupReporter.login(base, "admin", "secret");

            assertEquals("tok", token, "login should return the access token");
            assertNotNull(observedProtocol.get(), "the server never saw the request");
            assertTrue(
                observedProtocol.get().startsWith("HTTP/1.1"),
                "login negotiated " + observedProtocol.get() + " — it must use HTTP/1.1, "
                    + "or a reverse proxy rejects it with a bare "
                    + "'400 Invalid HTTP request received.'"
            );
            assertEquals(
                null, observedUpgrade.get(),
                "login sent an Upgrade header (h2c) — that is exactly what the "
                    + "proxy refuses"
            );
        } finally {
            server.stop(0);
        }
    }

    @Test
    @DisplayName("login still sends a form-encoded body")
    void loginPostsFormEncoded() throws Exception {
        // Guards the fix from regressing the encoding: /auth/login takes
        // OAuth2PasswordRequestForm, and posting JSON 422s. The CLI shipped
        // exactly that bug (json= instead of data=), so it is worth pinning
        // here while this path is under test.
        AtomicReference<String> contentType = new AtomicReference<>();
        AtomicReference<String> body = new AtomicReference<>();

        HttpServer server = HttpServer.create(new InetSocketAddress("127.0.0.1", 0), 0);
        server.createContext("/api/v1/auth/login", exchange -> {
            contentType.set(exchange.getRequestHeaders().getFirst("Content-Type"));
            body.set(new String(exchange.getRequestBody().readAllBytes(), StandardCharsets.UTF_8));
            byte[] out = "{\"access_token\":\"tok\"}".getBytes(StandardCharsets.UTF_8);
            exchange.sendResponseHeaders(200, out.length);
            try (OutputStream os = exchange.getResponseBody()) {
                os.write(out);
            }
        });
        server.start();
        try {
            String base = "http://127.0.0.1:" + server.getAddress().getPort();
            TestLookupReporter.login(base, "admin", "secret");

            assertEquals("application/x-www-form-urlencoded", contentType.get());
            assertTrue(body.get().contains("username=admin"), "body was: " + body.get());
            assertTrue(body.get().contains("password=secret"), "body was: " + body.get());
        } finally {
            server.stop(0);
        }
    }
}
