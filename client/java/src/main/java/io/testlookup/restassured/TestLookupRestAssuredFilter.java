package io.testlookup.restassured;

import com.fasterxml.jackson.databind.node.JsonNodeFactory;
import com.fasterxml.jackson.databind.node.ObjectNode;
import io.testlookup.ConfigLoader;
import io.testlookup.http.CaptureSettings;
import io.testlookup.http.ExchangeBuffer;
import io.testlookup.http.HttpRedaction;

import io.restassured.filter.Filter;
import io.restassured.filter.FilterContext;
import io.restassured.http.Header;
import io.restassured.http.Headers;
import io.restassured.response.Response;
import io.restassured.specification.FilterableRequestSpecification;
import io.restassured.specification.FilterableResponseSpecification;

import java.nio.charset.StandardCharsets;
import java.time.Instant;
import java.time.ZoneOffset;
import java.time.format.DateTimeFormatter;
import java.util.Arrays;
import java.util.Collections;
import java.util.HashMap;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Locale;
import java.util.Map;
import java.util.concurrent.TimeUnit;
import java.util.function.Supplier;
import java.util.logging.Logger;

/**
 * Captures each RestAssured call made during a test and sends it, redacted, with the test's result.
 *
 * <p>Registering the filter is the opt-in. Register it for every request, or on one request:
 * <pre>{@code
 * RestAssured.filters(new TestLookupRestAssuredFilter());
 * given().filter(new TestLookupRestAssuredFilter()).get("/orders");
 * }</pre>
 * {@code TESTLOOKUP_CAPTURE_HTTP} set to a falsy word ({@code 0 false no off}) turns capture off; the
 * {@code testlookup.capture_http} JVM property (or {@code reporting.capture_http} in a config file) can too,
 * when the environment variable is not set ({@link CaptureSettings}).
 *
 * <p>The call itself is never changed: the filter runs the request first and returns its response (or rethrows
 * its exception) untouched. Building, redacting and buffering the exchange can never fail the test; an error
 * there is logged with its exception class only. Exchanges go to the current test's {@link ExchangeBuffer},
 * which the JUnit 5 extension or the TestNG listener opens; with no test open nothing is kept.
 *
 * <p>This is the only SDK class that imports {@code io.restassured}: the SDK runs without RestAssured on the
 * classpath as long as this class is never loaded.
 */
public final class TestLookupRestAssuredFilter implements Filter {

    private static final Logger LOG = Logger.getLogger(TestLookupRestAssuredFilter.class.getName());

    /** {@code http_capture._TEXT_TYPES}: a body is captured only for these content types (or none). */
    private static final List<String> TEXT_TYPES = Collections.unmodifiableList(Arrays.asList(
        "json", "text/", "xml", "x-www-form-urlencoded", "javascript", "graphql", "yaml", "csv", "html"));

    /** The config file path of the capture switch (the Python SDK's {@code reporting.capture_http}). */
    static final String CONFIG_PATH = "reporting.capture_http";

    /** ISO-8601 UTC with a Z suffix, millisecond precision (the vectors' wire convention). */
    private static final DateTimeFormatter STARTED_AT =
        DateTimeFormatter.ofPattern("yyyy-MM-dd'T'HH:mm:ss.SSS'Z'", Locale.ROOT).withZone(ZoneOffset.UTC);

    private static final JsonNodeFactory NODES = JsonNodeFactory.instance;

    private final Supplier<Map<String, String>> env;

    /** A filter that reads the process environment. */
    public TestLookupRestAssuredFilter() {
        this.env = System::getenv;
    }

    /** A filter that reads this environment instead of the process's (tests). */
    TestLookupRestAssuredFilter(Map<String, String> env) {
        this.env = () -> env;
    }

    @Override
    public Response filter(FilterableRequestSpecification request, FilterableResponseSpecification responseSpec,
                           FilterContext ctx) {
        long startedAtMs = System.currentTimeMillis();
        long startNanos = System.nanoTime();
        Response response;
        try {
            response = ctx.next(request, responseSpec);
        } catch (Throwable error) {
            capture(request, null, startedAtMs, startNanos, error);
            throw TestLookupRestAssuredFilter.<RuntimeException>rethrow(error);
        }
        capture(request, response, startedAtMs, startNanos, null);
        return response;
    }

    /** Build, redact and buffer one exchange. Never throws. */
    private void capture(FilterableRequestSpecification request, Response response, long startedAtMs,
                         long startNanos, Throwable error) {
        try {
            if (!ExchangeBuffer.isOpen()) {
                ExchangeBuffer.dropOutsideTest();
                return;
            }
            if (!CaptureSettings.enabled(true, env.get(), configValue())) {
                return;
            }
            ExchangeBuffer.add(HttpRedaction.wireExchange(raw(request, response, startedAtMs, startNanos, error)));
        } catch (Throwable failure) {
            LOG.fine("TestLookup: HTTP capture skipped one exchange (" + failure.getClass().getSimpleName() + ")");
        }
    }

    /** The {@code testlookup.capture_http} JVM property, else {@code reporting.capture_http} from config. */
    private static Object configValue() {
        String property = System.getProperty(CaptureSettings.CONFIG_KEY);
        if (property != null && !property.trim().isEmpty()) {
            return property;
        }
        return ConfigLoader.getString(ConfigLoader.load(), CONFIG_PATH, null);
    }

    /** The raw exchange, in the shape {@link HttpRedaction#wireExchange} reads. */
    static ObjectNode raw(FilterableRequestSpecification request, Response response, long startedAtMs,
                          long startNanos, Throwable error) {
        ObjectNode raw = NODES.objectNode();
        raw.put("method", String.valueOf(request.getMethod()));
        raw.put("url", String.valueOf(request.getURI()));
        long durationMs = response != null ? response.getTimeIn(TimeUnit.MILLISECONDS) : -1L;
        if (durationMs < 0) {
            durationMs = Math.max(0L, (System.nanoTime() - startNanos) / 1_000_000L);
        }
        raw.put("duration_ms", durationMs);
        raw.put("started_at", STARTED_AT.format(Instant.ofEpochMilli(startedAtMs)));
        raw.set("request", message(headers(request.getHeaders()), requestBody(request.getBody())));
        if (error != null) {
            String name = error.getClass().getSimpleName();
            raw.put("error", name.isEmpty() ? error.getClass().getName() : name);
        }
        if (response != null) {
            raw.put("status", response.getStatusCode());
            Map<String, String> headers = headers(response.getHeaders());
            // Read the body only when it would be kept: a binary download stays untouched.
            byte[] body = textual(header(headers, "content-type")) ? response.asByteArray() : null;
            raw.set("response", message(headers, body));
        } else {
            raw.set("response", message(Collections.emptyMap(), null));
        }
        return raw;
    }

    /** Name → value; a repeated header (any case) is one entry, its values joined with ", ". */
    static Map<String, String> headers(Headers headers) {
        Map<String, String> out = new LinkedHashMap<>();
        if (headers == null) {
            return out;
        }
        Map<String, String> firstName = new HashMap<>();
        for (Header header : headers) {
            if (header == null || header.getName() == null) {
                continue;
            }
            String value = header.getValue() == null ? "" : header.getValue();
            String seen = firstName.putIfAbsent(header.getName().toLowerCase(Locale.ROOT), header.getName());
            if (seen == null) {
                out.put(header.getName(), value);
            } else {
                out.put(seen, out.get(seen) + ", " + value);
            }
        }
        return out;
    }

    /** A String body as UTF-8, a byte[] as is; a file, stream, multipart or object body is not captured. */
    private static byte[] requestBody(Object body) {
        if (body instanceof String) {
            return ((String) body).getBytes(StandardCharsets.UTF_8);
        }
        if (body instanceof byte[]) {
            return (byte[]) body;
        }
        return null;
    }

    /** {@code http_capture._message}: headers plus a textual body cut at the wire cap, and its full size. */
    private static ObjectNode message(Map<String, String> headers, byte[] raw) {
        ObjectNode message = NODES.objectNode();
        ObjectNode headerNode = message.putObject("headers");
        headers.forEach(headerNode::put);
        if (raw == null || !textual(header(headers, "content-type"))) {
            message.putNull("body");
            message.put("body_truncated", false);
            message.put("body_bytes", 0);
            return message;
        }
        int size = raw.length;
        int kept = Math.min(size, HttpRedaction.BODY_WIRE_MAX_BYTES);
        message.put("body", new String(raw, 0, kept, StandardCharsets.UTF_8));
        message.put("body_truncated", size > HttpRedaction.BODY_WIRE_MAX_BYTES);
        message.put("body_bytes", size);
        return message;
    }

    /** {@code http_capture._textual}: no content type, or one naming a text-like kind. */
    static boolean textual(String contentType) {
        if (contentType == null || contentType.isEmpty()) {
            return true;
        }
        String lowered = contentType.toLowerCase(Locale.ROOT);
        for (String kind : TEXT_TYPES) {
            if (lowered.contains(kind)) {
                return true;
            }
        }
        return false;
    }

    private static String header(Map<String, String> headers, String lowerName) {
        for (Map.Entry<String, String> entry : headers.entrySet()) {
            if (entry.getKey().toLowerCase(Locale.ROOT).equals(lowerName)) {
                return entry.getValue();
            }
        }
        return null;
    }

    /** Rethrows any throwable unchanged (RestAssured can surface a checked IOException from Groovy). */
    @SuppressWarnings("unchecked")
    private static <T extends Throwable> RuntimeException rethrow(Throwable error) throws T {
        throw (T) error;
    }
}
