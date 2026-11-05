package io.testlookup;

import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import com.sun.net.httpserver.HttpExchange;
import com.sun.net.httpserver.HttpServer;
import com.sun.net.httpserver.HttpsConfigurator;
import com.sun.net.httpserver.HttpsServer;

import javax.net.ssl.SSLContext;
import java.io.IOException;
import java.io.OutputStream;
import java.net.InetSocketAddress;
import java.nio.charset.StandardCharsets;
import java.util.ArrayList;
import java.util.Collections;
import java.util.List;

/**
 * A TestLookup server stand-in on 127.0.0.1 that records what the SDK sends:
 * session creates (body + auth headers), events, session closes and captured HTTP exchanges. It also serves
 * {@code /target}, an application for RestAssured tests to call.
 */
public final class FakeTestLookupServer implements AutoCloseable {

    private static final ObjectMapper JSON = new ObjectMapper();

    private final HttpServer server;
    private final String scheme;
    public final List<JsonNode> sessionCreates = Collections.synchronizedList(new ArrayList<>());
    public final List<String> sessionCreateAuth = Collections.synchronizedList(new ArrayList<>());
    public final List<JsonNode> events = Collections.synchronizedList(new ArrayList<>());
    public final List<String> closes = Collections.synchronizedList(new ArrayList<>());
    /** Bodies of POST /api/v1/stream/http-exchanges, and the auth header each carried. */
    public final List<JsonNode> httpExchangePosts = Collections.synchronizedList(new ArrayList<>());
    public final List<String> httpExchangeAuth = Collections.synchronizedList(new ArrayList<>());
    /** The status POST /api/v1/stream/http-exchanges answers (202 = stored). */
    public volatile int httpExchangesStatus = 202;
    /**
     * Every request the SDK sent to a TestLookup endpoint, verbatim: method, path and query, each header,
     * and the body. The /target endpoint (the system under test) is not recorded.
     */
    public final List<String> rawRequests = Collections.synchronizedList(new ArrayList<>());
    /** Requests served by /target, the stand-in for the application a RestAssured test calls. */
    public final List<String> targetRequests = Collections.synchronizedList(new ArrayList<>());
    /** When set, /target answers with this value in a Set-Cookie header and an access_token JSON field. */
    public volatile String targetSecret;

    private FakeTestLookupServer(HttpServer server, String scheme) {
        this.server = server;
        this.scheme = scheme;
        server.createContext("/api/v1/stream/sessions", this::sessions);
        server.createContext("/api/v1/stream/events/batch", this::batch);
        server.createContext("/api/v1/stream/http-exchanges", this::httpExchanges);
        server.createContext("/target", this::target);
        server.start();
    }

    public static FakeTestLookupServer http() throws IOException {
        return new FakeTestLookupServer(HttpServer.create(new InetSocketAddress("127.0.0.1", 0), 0), "http");
    }

    public static FakeTestLookupServer https(SSLContext tls) throws IOException {
        HttpsServer s = HttpsServer.create(new InetSocketAddress("127.0.0.1", 0), 0);
        s.setHttpsConfigurator(new HttpsConfigurator(tls));
        return new FakeTestLookupServer(s, "https");
    }

    public String baseUrl() {
        return scheme + "://127.0.0.1:" + server.getAddress().getPort();
    }

    /** Every recorded test_result event as "name:STATUS". */
    public List<String> results() {
        List<String> out = new ArrayList<>();
        synchronized (events) {
            for (JsonNode e : events) {
                if ("test_result".equals(e.path("event_type").asText())) {
                    out.add(e.path("test_name").asText() + ":" + e.path("status").asText());
                }
            }
        }
        return out;
    }

    /** The recorded test_result event for one test name, or null. */
    public JsonNode result(String testName) {
        synchronized (events) {
            for (JsonNode e : events) {
                if ("test_result".equals(e.path("event_type").asText())
                        && testName.equals(e.path("test_name").asText())) {
                    return e;
                }
            }
        }
        return null;
    }

    /** Read the request body and keep the whole request in {@link #rawRequests}. */
    private byte[] readAndRecord(HttpExchange ex) throws IOException {
        byte[] body = ex.getRequestBody().readAllBytes();
        StringBuilder raw = new StringBuilder();
        raw.append(ex.getRequestMethod()).append(' ').append(ex.getRequestURI()).append('\n');
        ex.getRequestHeaders().forEach((name, values) -> {
            for (String value : values) raw.append(name).append(": ").append(value).append('\n');
        });
        raw.append('\n').append(new String(body, StandardCharsets.UTF_8));
        rawRequests.add(raw.toString());
        return body;
    }

    private void httpExchanges(HttpExchange ex) throws IOException {
        byte[] body = readAndRecord(ex);
        httpExchangePosts.add(JSON.readTree(body));
        String key = ex.getRequestHeaders().getFirst("X-API-Key");
        httpExchangeAuth.add(key != null ? "X-API-Key " + key
            : String.valueOf(ex.getRequestHeaders().getFirst("Authorization")));
        int status = httpExchangesStatus;
        respond(ex, status, status == 202 ? "{\"stored\":1,\"truncated\":0,\"dropped\":0,\"test_case_id\":\"c\"}"
            : "{\"detail\":\"refused\"}");
    }

    /** The application under test: JSON for any path, binary bytes for a path ending in ".bin". */
    private void target(HttpExchange ex) throws IOException {
        ex.getRequestBody().readAllBytes();
        targetRequests.add(ex.getRequestMethod() + " " + ex.getRequestURI().getPath());
        String secret = targetSecret;
        if (secret != null) ex.getResponseHeaders().add("Set-Cookie", "sid=" + secret + "; HttpOnly");
        ex.getResponseHeaders().add("X-Trace", "a");
        ex.getResponseHeaders().add("X-Trace", "b");
        if (ex.getRequestURI().getPath().endsWith(".bin")) {
            byte[] bytes = ("binary " + secret).getBytes(StandardCharsets.UTF_8);
            ex.getResponseHeaders().add("Content-Type", "application/octet-stream");
            ex.sendResponseHeaders(200, bytes.length);
            try (OutputStream os = ex.getResponseBody()) {
                os.write(bytes);
            }
            return;
        }
        int status = "POST".equals(ex.getRequestMethod()) ? 201 : 200;
        respond(ex, status, secret != null ? "{\"ok\":true,\"access_token\":\"" + secret + "\"}" : "{\"ok\":true}");
    }

    private void sessions(HttpExchange ex) throws IOException {
        String body = new String(readAndRecord(ex), StandardCharsets.UTF_8);
        if ("POST".equals(ex.getRequestMethod())) {
            sessionCreates.add(JSON.readTree(body));
            String key = ex.getRequestHeaders().getFirst("X-API-Key");
            sessionCreateAuth.add(key != null ? "X-API-Key " + key
                : String.valueOf(ex.getRequestHeaders().getFirst("Authorization")));
            int n = sessionCreates.size();
            respond(ex, 201, "{\"session_id\":\"s-" + n + "-00000000\",\"session_token\":\"st-" + n
                + "\",\"run_id\":\"r-" + n + "\"}");
        } else if ("DELETE".equals(ex.getRequestMethod())) {
            closes.add(ex.getRequestURI().getPath());
            ex.sendResponseHeaders(204, -1);
            ex.close();
        } else {
            respond(ex, 405, "{}");
        }
    }

    private void batch(HttpExchange ex) throws IOException {
        JsonNode body = JSON.readTree(readAndRecord(ex));
        int accepted = 0;
        for (JsonNode e : body.path("events")) {
            events.add(e);
            accepted++;
        }
        respond(ex, 202, "{\"accepted\":" + accepted + ",\"run_id\":\"r\"}");
    }

    private static void respond(HttpExchange ex, int status, String json) throws IOException {
        byte[] bytes = json.getBytes(StandardCharsets.UTF_8);
        ex.getResponseHeaders().add("Content-Type", "application/json");
        ex.sendResponseHeaders(status, bytes.length);
        try (OutputStream os = ex.getResponseBody()) {
            os.write(bytes);
        }
    }

    @Override
    public void close() {
        server.stop(0);
    }
}
