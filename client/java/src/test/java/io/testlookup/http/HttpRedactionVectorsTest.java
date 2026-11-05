package io.testlookup.http;

import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import com.fasterxml.jackson.databind.node.ArrayNode;
import com.fasterxml.jackson.databind.node.JsonNodeFactory;
import com.fasterxml.jackson.databind.node.ObjectNode;
import org.junit.jupiter.api.DynamicTest;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.TestFactory;

import java.io.IOException;
import java.lang.reflect.Field;
import java.lang.reflect.Modifier;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.nio.file.Paths;
import java.util.ArrayList;
import java.util.Arrays;
import java.util.HashSet;
import java.util.Iterator;
import java.util.List;
import java.util.Map;
import java.util.Set;
import java.util.regex.Pattern;
import java.util.stream.Collectors;
import java.util.stream.Stream;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertNotNull;
import static org.junit.jupiter.api.Assertions.assertNull;
import static org.junit.jupiter.api.Assertions.assertTrue;
import static org.junit.jupiter.api.Assertions.fail;
import static org.junit.jupiter.api.DynamicTest.dynamicTest;

/**
 * The cross-language specification {@code sdk-vectors/http_exchange.json} through the Java port — the parity
 * proof with the server ({@code http_exchange_redaction.py}) and the Python SDK ({@code http_capture.py}).
 * Mirrors {@code client/tests/test_http_capture.py}'s three vector tests: the vocabulary, every case (and
 * idempotence), and what the SDK sends (wire → redact). One dynamic test per case, named after it
 * ({@code junit-jupiter-params} is not on this build's classpath, so no {@code @ParameterizedTest}).
 *
 * <p>The vector file is READ-ONLY here; a missing file FAILS this class, it never skips.
 */
class HttpRedactionVectorsTest {

    private static final ObjectMapper MAPPER = new ObjectMapper();
    private static final JsonNodeFactory NODES = JsonNodeFactory.instance;

    /** The names test_http_capture.py checks one by one (a renamed vocabulary key must not slip by). */
    private static final List<String> PYTHON_CHECKED_NAMES = Arrays.asList(
        "EXCHANGES_PER_TEST_MAX", "BODY_WIRE_MAX_BYTES", "BODY_STORED_MAX_BYTES", "TRUNCATION_MARKER",
        "HEADERS_MAX", "HEADER_NAME_MAX", "HEADER_VALUE_MAX", "URL_MAX", "ENDPOINT_MAX", "METHOD_RE",
        "SCHEMA_KEYS_MAX", "FIELD_NAME_RE", "REDACTED", "UNNAMED_KEYS", "JSON_DEPTH_MAX", "ERROR_MAX",
        "URL_PARTS_RE", "HEADER_DENYLIST", "HEADER_SECRET_SUFFIXES", "QUERY_DENYLIST", "ENDPOINT_ID_SEGMENT_RES",
        "TOKEN_SHAPES", "SCHEMA_STATES", "JSON_PAIR_RE", "FORM_PAIR_RE");

    private static JsonNode vectors;
    private static List<Case> cases;

    private static final class Case {
        final String name;
        final JsonNode input;
        final JsonNode expect;

        Case(String name, JsonNode input, JsonNode expect) {
            this.name = name;
            this.input = input;
            this.expect = expect;
        }
    }

    /** {@code ${project.basedir}/../../sdk-vectors/http_exchange.json} (surefire sets basedir; else the cwd). */
    static Path vectorsPath() {
        String base = System.getProperty("basedir", System.getProperty("user.dir"));
        return Paths.get(base).resolve(Paths.get("..", "..", "sdk-vectors", "http_exchange.json")).normalize();
    }

    private static synchronized JsonNode vectors() {
        if (vectors == null) {
            Path path = vectorsPath();
            if (!Files.isRegularFile(path)) {
                fail("sdk-vectors/http_exchange.json is missing (looked at " + path + "); this test fails, never skips");
            }
            try {
                vectors = MAPPER.readTree(new String(Files.readAllBytes(path), StandardCharsets.UTF_8));
            } catch (IOException e) {
                throw new AssertionError("cannot read " + path, e);
            }
        }
        return vectors;
    }

    private static synchronized List<Case> cases() {
        if (cases == null) {
            List<Case> out = new ArrayList<>();
            for (JsonNode c : vectors().get("cases")) {
                out.add(new Case(c.get("name").textValue(), expand(c.get("input")), expand(c.get("expect"))));
            }
            cases = out;
        }
        return cases;
    }

    // ── The vector file's generators: $repeat / $join / $list / $json ───────

    private static Set<String> keys(JsonNode node) {
        Set<String> out = new HashSet<>();
        node.fieldNames().forEachRemaining(out::add);
        return out;
    }

    private static String str(JsonNode node) {
        return node.isTextual() ? node.textValue() : node.toString();
    }

    static JsonNode expand(JsonNode value) {
        if (value.isObject()) {
            Set<String> keys = keys(value);
            if (keys.equals(new HashSet<>(Arrays.asList("$repeat", "times")))) {
                StringBuilder out = new StringBuilder();
                String unit = str(expand(value.get("$repeat")));
                for (int i = 0; i < value.get("times").asInt(); i++) {
                    out.append(unit);
                }
                return NODES.textNode(out.toString());
            }
            if (keys.equals(new HashSet<>(Arrays.asList("$join")))) {
                StringBuilder out = new StringBuilder();
                for (JsonNode part : value.get("$join")) {
                    out.append(str(expand(part)));
                }
                return NODES.textNode(out.toString());
            }
            if (keys.equals(new HashSet<>(Arrays.asList("$list", "count")))) {
                ArrayNode out = NODES.arrayNode();
                for (int i = 0; i < value.get("count").asInt(); i++) {
                    out.add(expand(value.get("$list")));
                }
                return out;
            }
            if (keys.equals(new HashSet<>(Arrays.asList("$json")))) {
                StringBuilder out = new StringBuilder();
                compactJson(out, expand(value.get("$json")));
                return NODES.textNode(out.toString());
            }
            ObjectNode out = NODES.objectNode();
            for (Map.Entry<String, JsonNode> entry : value.properties()) {
                out.set(entry.getKey(), expand(entry.getValue()));
            }
            return out;
        }
        if (value.isArray()) {
            ArrayNode out = NODES.arrayNode();
            for (JsonNode item : value) {
                out.add(expand(item));
            }
            return out;
        }
        return value;
    }

    /** The $json rule: "," and ":" with no spaces, non-ASCII kept, keys in the order given (Python's dumps). */
    private static void compactJson(StringBuilder out, JsonNode value) {
        if (value.isObject()) {
            out.append('{');
            boolean first = true;
            for (Map.Entry<String, JsonNode> entry : value.properties()) {
                if (!first) {
                    out.append(',');
                }
                first = false;
                pythonString(out, entry.getKey());
                out.append(':');
                compactJson(out, entry.getValue());
            }
            out.append('}');
        } else if (value.isArray()) {
            out.append('[');
            boolean first = true;
            for (JsonNode item : value) {
                if (!first) {
                    out.append(',');
                }
                first = false;
                compactJson(out, item);
            }
            out.append(']');
        } else if (value.isTextual()) {
            pythonString(out, value.textValue());
        } else if (value.isIntegralNumber()) {
            out.append(value.bigIntegerValue());
        } else if (value.isNumber()) {
            out.append(HttpJson.floatRepr(value.doubleValue())); // pinned by pythonFloatRepr below
        } else {
            out.append(value.toString()); // true / false / null
        }
    }

    private static void pythonString(StringBuilder out, String text) {
        out.append('"');
        for (char c : text.toCharArray()) {
            if (c == '"' || c == '\\') {
                out.append('\\').append(c);
            } else if (c == '\n') {
                out.append("\\n");
            } else if (c == '\r') {
                out.append("\\r");
            } else if (c == '\t') {
                out.append("\\t");
            } else if (c == '\b') {
                out.append("\\b");
            } else if (c == '\f') {
                out.append("\\f");
            } else if (c < 0x20) {
                out.append(String.format("\\u%04x", (int) c));
            } else {
                out.append(c);
            }
        }
        out.append('"');
    }

    // ── The tests ────────────────────────────────────────────────────────────

    @Test
    void theVectorFileIsTheOneThisPortImplements() throws Exception {
        JsonNode vocabulary = vectors().get("vocabulary");
        for (String name : PYTHON_CHECKED_NAMES) {
            assertTrue(vocabulary.has(name), "the vocabulary lost " + name);
        }
        // Every vocabulary key is a public static final constant of HttpRedaction with the same value
        // (JSON_PAIR_RE / FORM_PAIR_RE / URL_PARTS_RE / the pattern lists: the Python pattern TEXT).
        for (Map.Entry<String, JsonNode> entry : vocabulary.properties()) {
            String name = entry.getKey();
            JsonNode want = entry.getValue();
            Field field;
            try {
                field = HttpRedaction.class.getField(name);
            } catch (NoSuchFieldException e) {
                fail("HttpRedaction has no constant for the vocabulary key " + name);
                return;
            }
            int modifiers = field.getModifiers();
            assertTrue(Modifier.isStatic(modifiers) && Modifier.isFinal(modifiers), name + " is not static final");
            Object have = field.get(null);
            if (want.isArray()) {
                List<String> wanted = new ArrayList<>();
                want.forEach(item -> wanted.add(item.textValue()));
                assertEquals(wanted, have, name);
            } else if (want.isInt()) {
                assertEquals(int.class, field.getType(), name);
                assertEquals(want.intValue(), have, name);
            } else {
                assertEquals(want.textValue(), have, name);
            }
        }
        assertTrue(cases().size() >= 30, "expected at least 30 cases, found " + cases().size());
    }

    @TestFactory
    Stream<DynamicTest> everyVectorCaseThroughTheJavaPort() {
        return cases().stream().map(c -> dynamicTest(c.name, () -> {
            ObjectNode got = HttpRedaction.redactExchange(c.input);
            if (c.expect.isNull()) {
                assertNull(got, c.name + ": expected the exchange to be dropped");
                return;
            }
            assertNotNull(got, c.name + ": the exchange was dropped");
            assertEquals(c.expect, got, () -> c.name + ": " + firstDifference("", c.expect, got));
            // Idempotence: redacting the stored form again changes nothing.
            ObjectNode again = HttpRedaction.redactExchange(c.expect);
            assertEquals(c.expect, again, () -> c.name + " (idempotence): " + firstDifference("", c.expect, again));
        }));
    }

    private static final Pattern STORE_MARKER = Pattern.compile("\\[truncated: \\d+ bytes not stored\\]\\z");

    /**
     * The SDK skips only the store cut: the server's pass over what it sends equals the server's pass over the
     * raw exchange (bodies within the wire cap). The marker's {omitted} counts from what the server received,
     * so it is compared without that number; body_bytes (the size before any cut) is exact.
     */
    @TestFactory
    Stream<DynamicTest> whatTheSdkSendsStoresExactlyTheExpectedExchange() {
        List<Case> eligible = cases().stream()
            .filter(c -> !c.expect.isNull() && bodyWithinWireCap(c.input, "request") && bodyWithinWireCap(c.input, "response"))
            .collect(Collectors.toList());
        assertTrue(eligible.size() >= 27, "expected at least 27 wire-eligible cases, found " + eligible.size());
        return eligible.stream().map(c -> dynamicTest(c.name, () -> {
            ObjectNode wired = HttpRedaction.wireExchange(c.input);
            assertNotNull(wired, c.name + ": wireExchange refused a stored case");
            ObjectNode stored = HttpRedaction.redactExchange(wired);
            assertNotNull(stored, c.name + ": the wired exchange was dropped");
            ObjectNode expect = c.expect.deepCopy();
            for (ObjectNode doc : new ObjectNode[] {stored, expect}) {
                for (String side : new String[] {"request", "response"}) {
                    ObjectNode message = (ObjectNode) doc.get(side);
                    JsonNode body = message.get("body");
                    if (body.isTextual() && !body.textValue().isEmpty()) {
                        message.put("body", STORE_MARKER.matcher(body.textValue()).replaceAll("[truncated]"));
                    }
                }
            }
            assertEquals(expect, stored, () -> c.name + " (wire): " + firstDifference("", expect, stored));
        }));
    }

    private static boolean bodyWithinWireCap(JsonNode input, String side) {
        JsonNode message = input.get(side);
        JsonNode body = message == null ? null : message.get("body");
        if (body == null || !body.isTextual()) {
            return true;
        }
        return body.textValue().getBytes(StandardCharsets.UTF_8).length <= HttpRedaction.BODY_WIRE_MAX_BYTES;
    }

    /**
     * Python's float repr, which json.dumps writes and the vectors compare byte for byte (only 12.5 appears in
     * the vectors today, so the rest of the format is pinned here).
     */
    @Test
    void pythonFloatRepr() {
        Object[][] table = {
            {12.5, "12.5"}, {1.0, "1.0"}, {0.1, "0.1"}, {100.0, "100.0"}, {-0.0, "-0.0"}, {0.0, "0.0"},
            {1e20, "1e+20"}, {1e16, "1e+16"}, {1e15, "1000000000000000.0"}, {1e-7, "1e-07"},
            {0.0001, "0.0001"}, {0.00001, "1e-05"}, {1.5e-5, "1.5e-05"}, {123456789.123, "123456789.123"},
            {0.1 + 0.2, "0.30000000000000004"}, {Double.MIN_VALUE, "5e-324"}, {Double.MAX_VALUE, "1.7976931348623157e+308"},
            {2.5e16, "2.5e+16"}, {9007199254740993.0, "9007199254740992.0"}, {-1.25e-10, "-1.25e-10"},
        };
        for (Object[] row : table) {
            assertEquals(row[1], HttpJson.floatRepr((Double) row[0]), "repr(" + row[0] + ")");
        }
    }

    /**
     * Python's {@code \b} works on code points: an astral digit ("𝟏", U+1D7CF) is a word character, so it
     * joins the digits beside it. Java's plain look-behind sees half of it (see HttpText.PY_WORD); no vector has
     * an astral character, so the expected texts here are http_capture.text_pass's, Python 3.11.
     */
    @Test
    void pythonWordBoundaryAroundAstralCharacters() {
        String digit = "𝟏"; // U+1D7CF MATHEMATICAL BOLD DIGIT ONE (Nd)
        String letter = "𝐀"; // U+1D400 MATHEMATICAL BOLD CAPITAL A (Lu)
        String[][] table = {
            {"10.0.0.11" + digit + "5", "10.0.0.11" + digit + "5"},
            {"ip 10.0.0.1" + digit, "ip [REDACTED_IP]"},
            {digit + "555-123-4567", digit + "555-123-4567"},
            {"x" + digit + " 555-123-4567", "x" + digit + " [REDACTED_PHONE]"},
            {"call 555-123-4567 now", "call [REDACTED_PHONE] now"},
            {"id 123-45-6789" + digit, "id 123-45-6789" + digit},
            {letter + " 10.0.0.1 " + letter, letter + " [REDACTED_IP] " + letter},
        };
        for (String[] row : table) {
            assertEquals(row[1], HttpText.textPass(row[0]), "text_pass(" + row[0] + ")");
        }
    }

    // ── A readable failure ───────────────────────────────────────────────────

    private static String firstDifference(String path, JsonNode want, JsonNode have) {
        if (have == null) {
            return path + ": missing (want " + abbreviate(want) + ")";
        }
        if (want.isObject() && have.isObject()) {
            for (Iterator<String> it = want.fieldNames(); it.hasNext(); ) {
                String key = it.next();
                if (!want.get(key).equals(have.get(key))) {
                    return firstDifference(path + "/" + key, want.get(key), have.get(key));
                }
            }
            for (Iterator<String> it = have.fieldNames(); it.hasNext(); ) {
                String key = it.next();
                if (!want.has(key)) {
                    return path + "/" + key + ": unexpected " + abbreviate(have.get(key));
                }
            }
        }
        return path + ": want " + abbreviate(want) + " (" + want.getNodeType() + "/" + want.getClass().getSimpleName()
            + "), have " + abbreviate(have) + " (" + have.getNodeType() + "/" + have.getClass().getSimpleName() + ")";
    }

    private static String abbreviate(JsonNode node) {
        String text = node.toString();
        return text.length() <= 300 ? text : text.substring(0, 150) + " ... " + text.substring(text.length() - 150);
    }
}
