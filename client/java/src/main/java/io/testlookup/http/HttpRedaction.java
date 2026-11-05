package io.testlookup.http;

import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.node.BigIntegerNode;
import com.fasterxml.jackson.databind.node.IntNode;
import com.fasterxml.jackson.databind.node.JsonNodeFactory;
import com.fasterxml.jackson.databind.node.LongNode;
import com.fasterxml.jackson.databind.node.NullNode;
import com.fasterxml.jackson.databind.node.ObjectNode;

import java.math.BigInteger;
import java.util.ArrayList;
import java.util.Arrays;
import java.util.Collections;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;

/**
 * Redaction of captured HTTP exchanges: the Java port of {@code client/http_capture.py} (lines 57-660), which
 * is itself the port of the server's {@code backend/app/services/http_exchange_redaction.py}. The
 * cross-language specification is {@code sdk-vectors/http_exchange.json}; {@code HttpRedactionVectorsTest}
 * runs every one of its cases (and idempotence) through this class.
 *
 * <p>Pure: no HTTP client, no framework, no I/O. Header values of {@link #HEADER_DENYLIST} names (and names
 * ending in a {@link #HEADER_SECRET_SUFFIXES} entry) become {@link #REDACTED}; query values of
 * {@link #QUERY_DENYLIST} names and url userinfo too; every other value, and every JSON string leaf and key,
 * passes the text rules (secret-named pairs, provider token shapes, credentials, JWTs, e-mail addresses,
 * phone / card / IPv4 shapes). Bodies are cut at {@link #BODY_WIRE_MAX_BYTES} first.
 */
public final class HttpRedaction {

    private HttpRedaction() {
    }

    // ── The server's vocabulary (http_exchange_vocab.py; pinned by sdk-vectors/http_exchange.json) ──
    // No static field here may touch HttpText / HttpJson: they read these lists while initialising.

    public static final int EXCHANGES_PER_TEST_MAX = 20;
    public static final int EXCHANGES_PER_RUN_MAX = 1000;
    public static final int BODY_WIRE_MAX_BYTES = 65_536;
    public static final int BODY_STORED_MAX_BYTES = 16_384;
    public static final String TRUNCATION_MARKER = "\u2026[truncated: {omitted} bytes not stored]";
    public static final int HEADERS_MAX = 50;
    public static final int HEADER_NAME_MAX = 128;
    public static final int HEADER_VALUE_MAX = 2_048;
    public static final int URL_MAX = 2_048;
    public static final int ENDPOINT_MAX = 300;
    /** Python {@code re} pattern text (as in the vectors); compiled with Python's semantics internally. */
    public static final String METHOD_RE = "^[A-Z]{1,16}$";
    public static final int SCHEMA_KEYS_MAX = 200;
    /** Python {@code re} pattern text (as in the vectors). */
    public static final String FIELD_NAME_RE = "^[A-Za-z_$@][A-Za-z0-9_.$@:-]{0,119}$";
    public static final List<String> SCHEMA_STATES = list("parsed", "not_json", "empty", "too_large");
    public static final List<String> HEADER_DENYLIST = list(
        "authorization", "proxy-authorization", "cookie", "set-cookie", "x-api-key", "api-key", "x-auth-token",
        "x-access-token", "x-csrf-token", "x-xsrf-token", "x-amz-security-token", "x-goog-api-key",
        "x-session-token", "private-token", "x-gitlab-token", "x-hub-signature", "x-hub-signature-256");
    public static final List<String> HEADER_SECRET_SUFFIXES =
        list("token", "secret", "password", "api-key", "apikey", "signature", "credential");
    public static final List<String> QUERY_DENYLIST = list(
        "token", "access_token", "id_token", "refresh_token", "api_key", "apikey", "key", "code", "password",
        "secret", "client_secret", "sig", "signature", "x-amz-signature", "x-amz-credential", "x-amz-security-token",
        "auth");
    /** Python {@code re} pattern texts (as in the vectors; {@code \d} is any Unicode digit). */
    public static final List<String> ENDPOINT_ID_SEGMENT_RES = list(
        "^\\d+$", "^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$",
        "^[0-9a-fA-F]{24}$", "^[A-Za-z0-9_-]{16,}$");
    public static final String REDACTED = "[REDACTED]";
    /** run_context_vocab.TOKEN_SHAPES (W23b), Python {@code re} pattern texts. */
    public static final List<String> TOKEN_SHAPES = list(
        "gh[pousr]_[A-Za-z0-9]{20,}", "github_pat_[A-Za-z0-9_]{20,}", "glpat-[A-Za-z0-9_-]{16,}",
        "xox[abprs]-[A-Za-z0-9-]{10,}", "sk-[A-Za-z0-9_-]{20,}", "AKIA[A-Z0-9]{16}",
        "[A-Za-z0-9+/=_-]{40,}");
    public static final int JSON_DEPTH_MAX = 10;
    public static final String UNNAMED_KEYS = "_unnamed_keys";
    public static final int ERROR_MAX = 200;
    /** Python {@code re} pattern text, exactly as the vectors pin it (the Java form is derived from it). */
    public static final String JSON_PAIR_RE =
        "\"((?:[^\"\\\\\\r\\n]|\\\\.){1,128})\"(\\s*:\\s*)(?:\"(?:[^\"\\\\]|\\\\.)*(?:\"|$(?![\\s\\S]))|-?[0-9][0-9.eE+-]*)";
    /** Python {@code re} pattern text, exactly as the vectors pin it. */
    public static final String FORM_PAIR_RE = "(?<![A-Za-z0-9_.-])([A-Za-z][A-Za-z0-9_.-]{0,127})=([^&\\s\\\"',;]+)";
    /** RFC 3986 appendix B: scheme, authority, path, query (the fragment is matched and dropped). Python text. */
    public static final String URL_PARTS_RE =
        "\\A(?:([A-Za-z][A-Za-z0-9+.-]*):)?(?://([^/?#]*))?([^?#]*)(?:\\?([^#]*))?(?:#[\\s\\S]*)?\\Z";

    static final List<String> JSON_TYPES = list("string", "number", "boolean", "object", "array", "null");
    private static final List<String> CARRIED_STATES = list("parsed", "not_json", "too_large");
    private static final JsonNodeFactory NODES = JsonNodeFactory.instance;

    private static List<String> list(String... items) {
        return Collections.unmodifiableList(Arrays.asList(items));
    }

    // ── Public API ───────────────────────────────────────────────────────────

    /**
     * The server-equivalent pass ({@code http_capture.redact_exchange}): one wire exchange → the form the
     * server stores (every key present: method, url, endpoint, status, duration_ms, started_at, error,
     * request / response {@code {headers, body, body_truncated, body_bytes}}, response_schema,
     * response_schema_state, redaction_counts). {@code null} = refused (no url, a method failing
     * {@link #METHOD_RE}, a status or duration the server's model rejects). Idempotent.
     */
    public static ObjectNode redactExchange(JsonNode exchange) {
        if (exchange == null || !exchange.isObject()) {
            return null;
        }
        String method = normaliseMethod(exchange.get("method"));
        JsonNode originalUrl = exchange.get("url");
        if (method == null || !isNonBlankText(originalUrl)) {
            return null;
        }
        if (!inRange(exchange.get("status"), 100, 599) || !inRange(exchange.get("duration_ms"), 0, 86_400_000)) {
            return null; // the server's model refuses it, so its pre-filter drops it
        }
        String url = HttpText.redactUrl(originalUrl.textValue());
        int queryCount = HttpText.markers(url);
        JsonNode endpointIn = exchange.get("endpoint");
        String endpoint = isNonBlankText(endpointIn) ? HttpText.normaliseEndpoint(endpointIn.textValue())
            : HttpText.deriveEndpoint(originalUrl.textValue());
        ObjectNode out = NODES.objectNode();
        out.put("method", method);
        out.put("url", url);
        out.put("endpoint", endpoint);
        out.set("status", copyOrNull(exchange.get("status")));
        out.set("duration_ms", copyOrNull(exchange.get("duration_ms")));
        out.set("started_at", copyOrNull(exchange.get("started_at")));
        out.set("error", textOrNull(redactError(exchange.get("error"))));
        int headerCount = 0;
        int bodyCount = 0;
        for (String side : new String[] {"request", "response"}) {
            JsonNode message = objectOrEmpty(exchange.get(side));
            Headers headers = redactHeaders(message.get("headers"));
            headerCount += headers.count;
            boolean response = side.equals("response");
            Body body = redactMessageBody(message.get("body"), isTrue(message.get("body_truncated")),
                usableSize(message.get("body_bytes")),
                response ? exchange.get("response_schema") : null,
                response ? exchange.get("response_schema_state") : null);
            bodyCount += body.redactions;
            ObjectNode sideOut = NODES.objectNode();
            sideOut.set("headers", headers.node);
            sideOut.set("body", textOrNull(body.text));
            sideOut.put("body_truncated", body.truncated);
            sideOut.set("body_bytes", integer(body.bodyBytes));
            out.set(side, sideOut);
            if (response) {
                out.set("response_schema", body.schema == null ? NullNode.getInstance() : body.schema);
                out.put("response_schema_state", body.state);
            }
        }
        ObjectNode counts = NODES.objectNode();
        counts.put("headers", headerCount);
        counts.put("query", queryCount);
        counts.put("body", bodyCount);
        out.set("redaction_counts", counts);
        return out;
    }

    /**
     * What the SDK sends ({@code http_capture.wire_exchange}): every redaction rule but the
     * {@link #BODY_STORED_MAX_BYTES} store cut (the server makes it, after computing the response schema from
     * the full body). {@code null} = unusable. The raw node has method, url, optional endpoint / status /
     * duration_ms / started_at / error, and request / response objects
     * {@code {headers: {name: value}, body: String|null, body_truncated: bool, body_bytes: int}}.
     */
    public static ObjectNode wireExchange(JsonNode raw) {
        if (raw == null || !raw.isObject()) {
            return null;
        }
        String method = normaliseMethod(raw.get("method"));
        JsonNode url = raw.get("url");
        if (method == null || !isNonBlankText(url)) {
            return null;
        }
        // The drift key is derived from the url BEFORE redaction (as the server derives it from a raw url): a
        // token-shaped id segment reads "{id}" here, where the redacted url would only offer "[REDACTED]".
        JsonNode template = raw.get("endpoint");
        String endpoint = isNonBlankText(template) ? HttpText.normaliseEndpoint(template.textValue())
            : HttpText.deriveEndpoint(url.textValue());
        ObjectNode out = NODES.objectNode();
        out.put("method", method);
        out.put("url", HttpText.redactUrl(url.textValue()));
        out.put("endpoint", endpoint);
        for (String key : new String[] {"status", "duration_ms", "started_at"}) {
            JsonNode value = raw.get(key);
            if (value != null && !value.isNull()) {
                out.set(key, value.deepCopy());
            }
        }
        String error = redactError(raw.get("error"));
        if (error != null) {
            out.put("error", error);
        }
        for (String side : new String[] {"request", "response"}) {
            JsonNode message = objectOrEmpty(raw.get(side));
            JsonNode bodyNode = message.get("body");
            String rawBody = bodyNode != null && bodyNode.isTextual() ? bodyNode.textValue() : null;
            String body = rawBody;
            boolean truncated = isTrue(message.get("body_truncated"));
            if (body != null) {
                body = HttpText.clean(body);
                if (HttpText.utf8Len(body) > BODY_WIRE_MAX_BYTES) {
                    body = HttpText.cutBytes(body, BODY_WIRE_MAX_BYTES);
                    truncated = true;
                }
                if (!HttpText.pyStrip(body).isEmpty()) {
                    if (truncated) {
                        body = HttpText.textPass(body);
                    } else {
                        try {
                            body = HttpJson.dump(HttpJson.walk(HttpJson.parse(body), 1));
                        } catch (HttpJson.NotJson notJson) {
                            body = HttpText.textPass(body);
                        }
                    }
                }
            }
            // The size BEFORE the SDK touched it (the server stores max(this, what it receives) as body_bytes).
            BigInteger captured = BigInteger.valueOf(
                rawBody != null && !rawBody.isEmpty() ? HttpText.utf8Len(HttpText.clean(rawBody)) : 0);
            ObjectNode sideOut = NODES.objectNode();
            sideOut.set("headers", redactHeaders(message.get("headers")).node);
            sideOut.set("body", textOrNull(body));
            sideOut.put("body_truncated", truncated);
            sideOut.set("body_bytes", integer(usableSize(message.get("body_bytes")).max(captured)));
            out.set(side, sideOut);
        }
        return out;
    }

    /** The url's path with id-like segments as {@code {id}} (the server's drift key when no template is sent). */
    public static String deriveEndpoint(String url) {
        return HttpText.deriveEndpoint(url);
    }

    // ── Headers ──────────────────────────────────────────────────────────────

    private static final class Headers {
        final ObjectNode node;
        final int count;

        Headers(ObjectNode node, int count) {
            this.node = node;
            this.count = count;
        }
    }

    private static String headerText(JsonNode value) {
        if (value == null) {
            return null;
        }
        if (value.isTextual()) {
            return HttpText.clean(value.textValue());
        }
        if (value.isNumber()) {
            return value.isIntegralNumber() ? value.bigIntegerValue().toString()
                : HttpJson.floatRepr(value.doubleValue());
        }
        return null;
    }

    /**
     * At most {@link #HEADERS_MAX} headers (sorted by name); a denylisted / secret-suffixed name keeps its
     * name and loses its value; every other value gets the text pass and the {@link #HEADER_VALUE_MAX} cut.
     */
    private static Headers redactHeaders(JsonNode headers) {
        ObjectNode node = NODES.objectNode();
        if (headers == null || !headers.isObject()) {
            return new Headers(node, 0);
        }
        List<String[]> entries = new ArrayList<>();
        for (Map.Entry<String, JsonNode> field : headers.properties()) {
            String name = HttpText.pyStrip(HttpText.clean(field.getKey()));
            String value = headerText(field.getValue());
            if (name.isEmpty() || value == null) {
                continue;
            }
            entries.add(new String[] {name, value});
        }
        entries.sort((a, b) -> HttpText.compareCodePoints(a[0], b[0])); // stable, like Python's sort
        Map<String, String> out = new LinkedHashMap<>();
        for (String[] entry : entries.subList(0, Math.min(entries.size(), HEADERS_MAX))) {
            String name = entry[0];
            String value = entry[1];
            String lowered = HttpText.pyLower(HttpText.pyStrip(name)).replace('_', '-');
            String stored;
            if (HttpText.headerIsSecret(lowered)) {
                stored = value.isEmpty() ? value : REDACTED;
            } else {
                stored = HttpText.settle(value, HttpText::textPass, text -> HttpText.cutChars(text, HEADER_VALUE_MAX));
            }
            out.put(HttpText.cutChars(name, HEADER_NAME_MAX), stored);
        }
        int count = 0;
        for (Map.Entry<String, String> header : out.entrySet()) {
            node.put(header.getKey(), header.getValue());
            count += HttpText.markers(header.getValue());
        }
        return new Headers(node, count);
    }

    // ── Bodies ───────────────────────────────────────────────────────────────

    private static final class Body {
        final String text;
        final int redactions;
        final ObjectNode schema;
        final String state;
        final boolean truncated;
        final BigInteger bodyBytes;

        Body(String text, int redactions, ObjectNode schema, String state, boolean truncated, BigInteger bodyBytes) {
            this.text = text;
            this.redactions = redactions;
            this.schema = schema;
            this.state = state;
            this.truncated = truncated;
            this.bodyBytes = bodyBytes;
        }
    }

    private static String storeCut(String text) {
        return HttpText.cutBytes(text, BODY_STORED_MAX_BYTES);
    }

    private static String marker(long omitted) {
        return TRUNCATION_MARKER.replace("{omitted}", Long.toString(omitted));
    }

    private static ObjectNode sanitisedSchema(JsonNode schema) {
        if (schema == null || !schema.isObject()) {
            return null;
        }
        ObjectNode out = NODES.objectNode();
        int seen = 0;
        for (Map.Entry<String, JsonNode> entry : schema.properties()) {
            if (seen++ > SCHEMA_KEYS_MAX) {
                break; // list(schema.items())[: SCHEMA_KEYS_MAX + 1]
            }
            String key = entry.getKey();
            JsonNode kind = entry.getValue();
            if (key.equals(UNNAMED_KEYS) && kind.isTextual() && HttpText.pyIsDigit(kind.textValue())) {
                out.put(key, kind.textValue());
            } else if (HttpText.FIELD_NAME.matcher(key).matches() && kind.isTextual()
                && JSON_TYPES.contains(kind.textValue())) {
                out.put(key, kind.textValue());
            }
        }
        return out;
    }

    /**
     * The server's {@code redact_message_body}. A stored copy ({@code truncated} and a trailing marker) keeps
     * its marker, schema and state; otherwise the body is fresh.
     */
    private static Body redactMessageBody(JsonNode textNode, boolean truncated, BigInteger usable, JsonNode schema,
                                          JsonNode state) {
        if (textNode == null || !textNode.isTextual()) {
            return new Body(null, 0, null, "empty", truncated, BigInteger.ZERO);
        }
        String text = HttpText.clean(textNode.textValue());
        if (truncated) {
            java.util.regex.Matcher m = HttpText.MARKER_AT_END.matcher(text);
            if (m.find()) {
                String kept = text.substring(0, m.start());
                if (HttpText.utf8Len(kept) <= BODY_STORED_MAX_BYTES) {
                    long omitted = HttpText.parseDigits(m.group(1));
                    kept = HttpText.settle(kept, HttpText::textPass, HttpRedaction::storeCut);
                    String carried = state != null && state.isTextual() && CARRIED_STATES.contains(state.textValue())
                        ? state.textValue() : "too_large";
                    ObjectNode carriedSchema = carried.equals("parsed") ? sanitisedSchema(schema) : null;
                    if (carried.equals("parsed") && carriedSchema == null) {
                        carried = "too_large";
                    }
                    BigInteger size = BigInteger.valueOf(HttpText.utf8Len(kept) + omitted).max(usable);
                    return new Body(kept + marker(omitted), HttpText.markers(kept), carriedSchema, carried, true, size);
                }
            }
        }
        int received = HttpText.utf8Len(text);
        if (HttpText.pyStrip(text).isEmpty()) {
            return new Body(text, 0, null, "empty", truncated, BigInteger.valueOf(received).max(usable));
        }
        boolean wireCut = received > BODY_WIRE_MAX_BYTES;
        if (wireCut) {
            text = HttpText.cutBytes(text, BODY_WIRE_MAX_BYTES);
        }
        boolean incomplete = wireCut || truncated;
        ObjectNode resultSchema = null;
        String resultState;
        String full;
        if (incomplete) {
            resultState = "too_large";
            full = HttpText.textPass(text);
        } else {
            Object walked = null;
            boolean parsed;
            try {
                walked = HttpJson.walk(HttpJson.parse(text), 1);
                parsed = true;
            } catch (HttpJson.NotJson notJson) {
                parsed = false;
            }
            if (parsed) {
                resultState = "parsed";
                full = HttpJson.dump(walked);
                resultSchema = NODES.objectNode();
                for (Map.Entry<String, String> entry : HttpJson.schemaOf(walked).entrySet()) {
                    resultSchema.put(entry.getKey(), entry.getValue());
                }
            } else {
                resultState = "not_json";
                full = HttpText.textPass(text);
            }
        }
        int fullBytes = HttpText.utf8Len(full);
        BigInteger size = BigInteger.valueOf(Math.max(received, fullBytes)).max(usable);
        if (fullBytes <= BODY_STORED_MAX_BYTES) {
            return new Body(full, HttpText.markers(full), resultSchema, resultState, incomplete, size);
        }
        String kept = HttpText.settle(full, HttpText::textPass, HttpRedaction::storeCut);
        long omitted = (long) Math.max(received, fullBytes) - HttpText.utf8Len(kept);
        return new Body(kept + marker(omitted), HttpText.markers(kept), resultSchema, resultState, true, size);
    }

    // ── Scalars ──────────────────────────────────────────────────────────────

    private static String normaliseMethod(JsonNode value) {
        if (value == null || !value.isTextual()) {
            return null;
        }
        String method = HttpText.pyUpper(HttpText.pyStrip(value.textValue()));
        return HttpText.METHOD.matcher(method).matches() ? method : null;
    }

    /** {@code null} or an integer in {@code [low, high]} (what the server's model accepts). */
    private static boolean inRange(JsonNode value, long low, long high) {
        if (value == null || value.isNull()) {
            return true;
        }
        if (!value.isIntegralNumber()) {
            return false;
        }
        BigInteger number = value.bigIntegerValue();
        return number.compareTo(BigInteger.valueOf(low)) >= 0 && number.compareTo(BigInteger.valueOf(high)) <= 0;
    }

    /** A transport error's class name ("ConnectTimeout") through the text pass, at most {@link #ERROR_MAX}. */
    private static String redactError(JsonNode value) {
        if (value == null || !value.isTextual()) {
            return null;
        }
        String error = HttpText.settle(HttpText.clean(value.textValue()), HttpText::textPass,
            text -> HttpText.cutChars(text, ERROR_MAX));
        return error.isEmpty() ? null : error;
    }

    private static BigInteger usableSize(JsonNode value) {
        if (value == null || !value.isIntegralNumber() || value.bigIntegerValue().signum() < 0) {
            return BigInteger.ZERO;
        }
        return value.bigIntegerValue();
    }

    private static boolean isNonBlankText(JsonNode value) {
        return value != null && value.isTextual() && !HttpText.pyStrip(value.textValue()).isEmpty();
    }

    private static boolean isTrue(JsonNode value) {
        return value != null && value.isBoolean() && value.booleanValue();
    }

    private static JsonNode objectOrEmpty(JsonNode value) {
        return value != null && value.isObject() ? value : NODES.objectNode();
    }

    private static JsonNode copyOrNull(JsonNode value) {
        return value == null ? NullNode.getInstance() : value.deepCopy();
    }

    private static JsonNode textOrNull(String text) {
        return text == null ? NullNode.getInstance() : NODES.textNode(text);
    }

    /** The node Jackson's parser makes for this integer (int, then long, then BigInteger). */
    private static JsonNode integer(BigInteger value) {
        if (value.bitLength() < 32) {
            return IntNode.valueOf(value.intValue());
        }
        if (value.bitLength() < 64) {
            return LongNode.valueOf(value.longValue());
        }
        return BigIntegerNode.valueOf(value);
    }
}
