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
 * session creates (body + auth headers), events, and session closes.
 */
public final class FakeTestLookupServer implements AutoCloseable {

    private static final ObjectMapper JSON = new ObjectMapper();

    private final HttpServer server;
    private final String scheme;
    public final List<JsonNode> sessionCreates = Collections.synchronizedList(new ArrayList<>());
    public final List<String> sessionCreateAuth = Collections.synchronizedList(new ArrayList<>());
    public final List<JsonNode> events = Collections.synchronizedList(new ArrayList<>());
    public final List<String> closes = Collections.synchronizedList(new ArrayList<>());

    private FakeTestLookupServer(HttpServer server, String scheme) {
        this.server = server;
        this.scheme = scheme;
        server.createContext("/api/v1/stream/sessions", this::sessions);
        server.createContext("/api/v1/stream/events/batch", this::batch);
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

    private void sessions(HttpExchange ex) throws IOException {
        String body = new String(ex.getRequestBody().readAllBytes(), StandardCharsets.UTF_8);
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
        JsonNode body = JSON.readTree(ex.getRequestBody().readAllBytes());
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
