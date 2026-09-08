package io.testlookup;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertNotNull;
import static org.junit.jupiter.api.Assertions.assertTrue;

import com.fasterxml.jackson.databind.JsonNode;
import com.sun.net.httpserver.HttpExchange;
import com.sun.net.httpserver.HttpServer;
import java.io.IOException;
import java.net.InetSocketAddress;
import java.nio.charset.StandardCharsets;
import java.util.List;
import java.util.UUID;
import java.util.concurrent.CopyOnWriteArrayList;
import java.util.concurrent.CountDownLatch;
import java.util.concurrent.TimeUnit;
import org.junit.jupiter.api.Test;

class LiveProtocolIdentityTest {

    @Test
    void retryReusesBatchIdentityAndEventOrder() throws Exception {
        List<String> batchBodies = new CopyOnWriteArrayList<>();
        CountDownLatch retried = new CountDownLatch(2);
        HttpServer server = HttpServer.create(new InetSocketAddress(0), 0);
        server.createContext("/api/v1/stream/sessions", exchange -> {
            if ("DELETE".equals(exchange.getRequestMethod())) {
                respond(exchange, 204, "");
                return;
            }
            respond(exchange, 200,
                "{\"session_id\":\"session-1\",\"session_token\":\"token-1\","+
                "\"run_id\":\"run-1\",\"project_id\":\"project-1\","+
                "\"expires_in\":3600,\"created_at\":\"2026-01-01T00:00:00Z\"}");
        });
        server.createContext("/api/v1/stream/events/batch", exchange -> {
            batchBodies.add(new String(exchange.getRequestBody().readAllBytes(), StandardCharsets.UTF_8));
            if (batchBodies.size() == 1) {
                exchange.getResponseHeaders().set("Retry-After", "0");
                respond(exchange, 503, "{}");
            } else {
                respond(exchange, 200, "{\"accepted\":1}");
            }
            retried.countDown();
        });
        server.start();

        TestLookupReporter reporter = new TestLookupReporter.Builder()
            .baseUrl("http://127.0.0.1:" + server.getAddress().getPort())
            .apiKey("qai_test")
            .projectId("project-1")
            .batchSize(1)
            .batchIntervalMs(60_000)
            .build();
        TestLookupReporter.LiveSession session = reporter.startSession(
            TestLookupReporter.SessionOptions.builder().build());
        try {
            session.record("test_login", TestLookupReporter.TestStatus.PASSED, 10);
            assertTrue(retried.await(5, TimeUnit.SECONDS), "batch was not retried");
            assertEquals(2, batchBodies.size());
            assertEquals(batchBodies.get(0), batchBodies.get(1));
            JsonNode payload = TestLookupReporter.MAPPER.readTree(batchBodies.get(0));
            assertNotNull(UUID.fromString(payload.path("batch_id").asText()));
        } finally {
            session.close();
            server.stop(0);
        }
    }

    private static void respond(HttpExchange exchange, int status, String body) throws IOException {
        byte[] bytes = body.getBytes(StandardCharsets.UTF_8);
        if (status == 204) {
            exchange.sendResponseHeaders(status, -1);
        } else {
            exchange.getResponseHeaders().set("Content-Type", "application/json");
            exchange.sendResponseHeaders(status, bytes.length);
            exchange.getResponseBody().write(bytes);
        }
        exchange.close();
    }
}
