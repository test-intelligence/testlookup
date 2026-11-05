package io.testlookup.http;

import java.math.BigDecimal;
import java.math.BigInteger;
import java.math.MathContext;
import java.math.RoundingMode;
import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;

/**
 * The body side of {@link HttpRedaction}: Python's {@code json.loads} / {@code json.dumps} as
 * {@code client/http_capture.py} uses them, so a redacted JSON body is the same TEXT, byte for byte, as the
 * server and the Python SDK store.
 *
 * <p>The parsed model mirrors Python's: {@code LinkedHashMap} (a duplicate key keeps the LAST value at the
 * FIRST key's position), {@code ArrayList}, {@code String}, {@code BigInteger} (Python's unbounded int),
 * {@code Double}, {@code Boolean}, and Java {@code null} for JSON null. Refused like Python: NaN / Infinity,
 * a float outside the double range, an integer of more than 4300 digits (Python's int max str digits), a
 * leading BOM, control characters inside strings, and nesting past {@link #NESTING_MAX} (Python's
 * RecursionError). Python has no fixed depth: measured through http_capture, 3.11 parses ≈ 991 nested
 * containers (its recursion limit 1000 minus the caller's stack; the server runs 3.11), 3.12 2997, 3.14 16913.
 * The Java port fixes it at 1000 (3.11's limit, and Jackson's default nesting cap). Either side of the line the
 * body is redacted: walked (containers past JSON_DEPTH_MAX become [REDACTED]) or text-passed.
 */
final class HttpJson {

    private HttpJson() {
    }

    static final int NESTING_MAX = 1000;
    static final int INT_DIGITS_MAX = 4300;

    /** Not JSON (Python's ValueError / RecursionError from {@code _parse_json}). */
    static final class NotJson extends Exception {
        private static final long serialVersionUID = 1L;

        NotJson(String message) {
            super(message, null, false, false);
        }
    }

    // ── json.loads(text, parse_constant=refuse, parse_float=finite) ─────────

    static Object parse(String text) throws NotJson {
        if (text.startsWith("﻿")) {
            throw new NotJson("Unexpected UTF-8 BOM");
        }
        Parser parser = new Parser(text);
        parser.i = parser.ws(0);
        Object value = parser.value(0);
        if (parser.ws(parser.i) != text.length()) {
            throw new NotJson("Extra data");
        }
        return value;
    }

    private static final class Parser {
        private final String s;
        private final int n;
        int i;

        Parser(String s) {
            this.s = s;
            this.n = s.length();
        }

        int ws(int at) {
            while (at < n) {
                char c = s.charAt(at);
                if (c != ' ' && c != '\t' && c != '\n' && c != '\r') {
                    break;
                }
                at++;
            }
            return at;
        }

        Object value(int depth) throws NotJson {
            if (i >= n) {
                throw new NotJson("Expecting value");
            }
            char c = s.charAt(i);
            switch (c) {
                case '"':
                    return string();
                case '{':
                    return object(depth + 1);
                case '[':
                    return array(depth + 1);
                case 'n':
                    if (s.startsWith("null", i)) {
                        i += 4;
                        return null;
                    }
                    break;
                case 't':
                    if (s.startsWith("true", i)) {
                        i += 4;
                        return Boolean.TRUE;
                    }
                    break;
                case 'f':
                    if (s.startsWith("false", i)) {
                        i += 5;
                        return Boolean.FALSE;
                    }
                    break;
                default:
                    break;
            }
            if (c == '-' || (c >= '0' && c <= '9')) {
                return number();
            }
            // NaN / Infinity / -Infinity land here too: http_capture refuses them (parse_constant).
            throw new NotJson("Expecting value");
        }

        private Map<String, Object> object(int depth) throws NotJson {
            if (depth > NESTING_MAX) {
                throw new NotJson("maximum recursion depth exceeded");
            }
            Map<String, Object> out = new LinkedHashMap<>();
            i = ws(i + 1);
            if (i < n && s.charAt(i) == '}') {
                i++;
                return out;
            }
            while (true) {
                if (i >= n || s.charAt(i) != '"') {
                    throw new NotJson("Expecting property name enclosed in double quotes");
                }
                String key = string();
                i = ws(i);
                if (i >= n || s.charAt(i) != ':') {
                    throw new NotJson("Expecting ':' delimiter");
                }
                i = ws(i + 1);
                out.put(key, value(depth));
                i = ws(i);
                if (i < n && s.charAt(i) == '}') {
                    i++;
                    return out;
                }
                if (i >= n || s.charAt(i) != ',') {
                    throw new NotJson("Expecting ',' delimiter");
                }
                i = ws(i + 1);
            }
        }

        private List<Object> array(int depth) throws NotJson {
            if (depth > NESTING_MAX) {
                throw new NotJson("maximum recursion depth exceeded");
            }
            List<Object> out = new ArrayList<>();
            i = ws(i + 1);
            if (i < n && s.charAt(i) == ']') {
                i++;
                return out;
            }
            while (true) {
                out.add(value(depth));
                i = ws(i);
                if (i < n && s.charAt(i) == ']') {
                    i++;
                    return out;
                }
                if (i >= n || s.charAt(i) != ',') {
                    throw new NotJson("Expecting ',' delimiter");
                }
                i = ws(i + 1);
            }
        }

        private String string() throws NotJson {
            int start = ++i;
            StringBuilder out = null;
            while (true) {
                if (i >= n) {
                    throw new NotJson("Unterminated string");
                }
                char c = s.charAt(i);
                if (c == '"') {
                    String text = out == null ? s.substring(start, i) : out.append(s, start, i).toString();
                    i++;
                    return text;
                }
                if (c == '\\') {
                    if (out == null) {
                        out = new StringBuilder();
                    }
                    out.append(s, start, i);
                    if (i + 1 >= n) {
                        throw new NotJson("Unterminated string");
                    }
                    char e = s.charAt(i + 1);
                    i += 2;
                    switch (e) {
                        case '"':
                            out.append('"');
                            break;
                        case '\\':
                            out.append('\\');
                            break;
                        case '/':
                            out.append('/');
                            break;
                        case 'b':
                            out.append('\b');
                            break;
                        case 'f':
                            out.append('\f');
                            break;
                        case 'n':
                            out.append('\n');
                            break;
                        case 'r':
                            out.append('\r');
                            break;
                        case 't':
                            out.append('\t');
                            break;
                        case 'u':
                            // A surrogate pair of escapes becomes one code point in Python; adjacent UTF-16
                            // halves are that code point in Java. A lone half stays lone (cleaned later).
                            out.append(hex4());
                            break;
                        default:
                            throw new NotJson("Invalid \\escape");
                    }
                    start = i;
                    continue;
                }
                if (c < 0x20) {
                    throw new NotJson("Invalid control character");
                }
                i++;
            }
        }

        private char hex4() throws NotJson {
            if (i + 4 > n) {
                throw new NotJson("Invalid \\uXXXX escape");
            }
            int value = 0;
            for (int k = 0; k < 4; k++) {
                char h = s.charAt(i + k);
                int digit;
                if (h >= '0' && h <= '9') {
                    digit = h - '0';
                } else if (h >= 'a' && h <= 'f') {
                    digit = h - 'a' + 10;
                } else if (h >= 'A' && h <= 'F') {
                    digit = h - 'A' + 10;
                } else {
                    throw new NotJson("Invalid \\uXXXX escape");
                }
                value = (value << 4) | digit;
            }
            i += 4;
            return (char) value;
        }

        private Object number() throws NotJson {
            int start = i;
            if (s.charAt(i) == '-') {
                i++;
                if (i >= n) {
                    throw new NotJson("Expecting value");
                }
            }
            char c = s.charAt(i);
            if (c >= '1' && c <= '9') {
                i++;
                while (i < n && isDigit(s.charAt(i))) {
                    i++;
                }
            } else if (c == '0') {
                i++;
            } else {
                throw new NotJson("Expecting value");
            }
            boolean isFloat = false;
            if (i + 1 < n && s.charAt(i) == '.' && isDigit(s.charAt(i + 1))) {
                isFloat = true;
                i += 2;
                while (i < n && isDigit(s.charAt(i))) {
                    i++;
                }
            }
            if (i + 1 < n && (s.charAt(i) == 'e' || s.charAt(i) == 'E')) {
                int exponentStart = i;
                i++;
                if (i + 1 < n && (s.charAt(i) == '-' || s.charAt(i) == '+')) {
                    i++;
                }
                while (i < n && isDigit(s.charAt(i))) {
                    i++;
                }
                if (isDigit(s.charAt(i - 1))) {
                    isFloat = true;
                } else {
                    i = exponentStart;
                }
            }
            String literal = s.substring(start, i);
            if (isFloat) {
                double value = Double.parseDouble(literal);
                if (Double.isInfinite(value)) {
                    throw new NotJson("a number outside the float range");
                }
                return value;
            }
            int digits = literal.length() - (literal.charAt(0) == '-' ? 1 : 0);
            if (digits > INT_DIGITS_MAX) {
                throw new NotJson("Exceeds the limit (4300 digits) for integer string conversion");
            }
            return new BigInteger(literal);
        }

        private static boolean isDigit(char c) {
            return c >= '0' && c <= '9';
        }
    }

    // ── _walk ────────────────────────────────────────────────────────────────

    /**
     * Every string leaf and key through the text pass; a secret-named key's non-null, non-boolean value
     * becomes [REDACTED]; a container deeper than JSON_DEPTH_MAX becomes [REDACTED].
     */
    @SuppressWarnings("unchecked")
    static Object walk(Object value, int depth) {
        if ((value instanceof Map || value instanceof List) && depth > HttpRedaction.JSON_DEPTH_MAX) {
            return HttpRedaction.REDACTED;
        }
        if (value instanceof Map) {
            Map<String, Object> out = new LinkedHashMap<>();
            for (Map.Entry<String, Object> entry : ((Map<String, Object>) value).entrySet()) {
                String name = HttpText.clean(entry.getKey());
                String key = HttpText.textPass(name);
                Object item = entry.getValue();
                if (HttpText.keyIsSecret(name) && item != null && !(item instanceof Boolean)) {
                    out.put(key, HttpRedaction.REDACTED);
                } else {
                    out.put(key, walk(item, depth + 1));
                }
            }
            return out;
        }
        if (value instanceof List) {
            List<Object> out = new ArrayList<>();
            for (Object item : (List<Object>) value) {
                out.add(walk(item, depth + 1));
            }
            return out;
        }
        if (value instanceof String) {
            return HttpText.textPass(HttpText.clean((String) value));
        }
        return value;
    }

    static String jsonType(Object value) {
        if (value == null) {
            return "null";
        }
        if (value instanceof Boolean) {
            return "boolean";
        }
        if (value instanceof Number) {
            return "number";
        }
        if (value instanceof String) {
            return "string";
        }
        return value instanceof List ? "array" : "object";
    }

    /** {@code _schema_of}: top-level key → JSON type; other keys counted under UNNAMED_KEYS. */
    @SuppressWarnings("unchecked")
    static Map<String, String> schemaOf(Object value) {
        Map<String, String> schema = new LinkedHashMap<>();
        if (!(value instanceof Map)) {
            return schema;
        }
        int unnamed = 0;
        for (Map.Entry<String, Object> entry : ((Map<String, Object>) value).entrySet()) {
            String key = entry.getKey();
            if (!key.equals(HttpRedaction.UNNAMED_KEYS) && schema.size() < HttpRedaction.SCHEMA_KEYS_MAX
                && HttpText.FIELD_NAME.matcher(key).matches()) {
                schema.put(key, jsonType(entry.getValue()));
            } else {
                unnamed++;
            }
        }
        if (unnamed > 0) {
            schema.put(HttpRedaction.UNNAMED_KEYS, Integer.toString(unnamed));
        }
        return schema;
    }

    // ── json.dumps(value, ensure_ascii=False, separators=(",", ":")) ─────────

    static String dump(Object value) {
        StringBuilder out = new StringBuilder();
        dumpInto(out, value);
        return out.toString();
    }

    @SuppressWarnings("unchecked")
    private static void dumpInto(StringBuilder out, Object value) {
        if (value == null) {
            out.append("null");
        } else if (value instanceof Boolean) {
            out.append(((Boolean) value) ? "true" : "false");
        } else if (value instanceof String) {
            quoteInto(out, (String) value);
        } else if (value instanceof Double) {
            double d = (Double) value;
            if (Double.isNaN(d)) {
                out.append("NaN");
            } else if (Double.isInfinite(d)) {
                out.append(d > 0 ? "Infinity" : "-Infinity");
            } else {
                out.append(floatRepr(d));
            }
        } else if (value instanceof Number) {
            out.append(value.toString());
        } else if (value instanceof Map) {
            out.append('{');
            boolean first = true;
            for (Map.Entry<String, Object> entry : ((Map<String, Object>) value).entrySet()) {
                if (!first) {
                    out.append(',');
                }
                first = false;
                quoteInto(out, entry.getKey());
                out.append(':');
                dumpInto(out, entry.getValue());
            }
            out.append('}');
        } else if (value instanceof List) {
            out.append('[');
            boolean first = true;
            for (Object item : (List<Object>) value) {
                if (!first) {
                    out.append(',');
                }
                first = false;
                dumpInto(out, item);
            }
            out.append(']');
        } else {
            throw new IllegalArgumentException("not a JSON value: " + value.getClass());
        }
    }

    private static final char[] HEX_LOWER = "0123456789abcdef".toCharArray();

    /** Python's string escaping with ensure_ascii=False: only {@code " \ } and C0 controls are escaped. */
    static void quoteInto(StringBuilder out, String text) {
        out.append('"');
        for (int i = 0; i < text.length(); i++) {
            char c = text.charAt(i);
            switch (c) {
                case '"':
                    out.append("\\\"");
                    break;
                case '\\':
                    out.append("\\\\");
                    break;
                case '\n':
                    out.append("\\n");
                    break;
                case '\r':
                    out.append("\\r");
                    break;
                case '\t':
                    out.append("\\t");
                    break;
                case '\b':
                    out.append("\\b");
                    break;
                case '\f':
                    out.append("\\f");
                    break;
                default:
                    if (c < 0x20) {
                        out.append("\\u00").append(HEX_LOWER[c >> 4]).append(HEX_LOWER[c & 0xF]);
                    } else {
                        out.append(c);
                    }
            }
        }
        out.append('"');
    }

    /**
     * Python's {@code repr(float)}: the SHORTEST decimal that reads back as {@code d} (the closest to
     * {@code d} among those), fixed notation when the decimal point position is in (-4, 16], else
     * {@code d.ddde+XX}. Not {@code Double.toString}: before JDK 19 it is not always shortest, and since
     * JDK 19 it prefers two digits over one ("4.9E-324" where Python writes "5e-324").
     */
    static String floatRepr(double d) {
        if (Double.isNaN(d)) {
            return "nan";
        }
        if (Double.isInfinite(d)) {
            return d > 0 ? "inf" : "-inf";
        }
        String sign = (Double.doubleToRawLongBits(d) < 0) ? "-" : "";
        if (d == 0) {
            return sign + "0.0";
        }
        double magnitude = Math.abs(d);
        BigDecimal exact = new BigDecimal(magnitude);
        BigDecimal best = null;
        for (int precision = 1; precision <= 17 && best == null; precision++) {
            BigDecimal down = exact.round(new MathContext(precision, RoundingMode.FLOOR));
            BigDecimal up = exact.round(new MathContext(precision, RoundingMode.CEILING));
            boolean downOk = Double.parseDouble(down.toString()) == magnitude;
            boolean upOk = Double.parseDouble(up.toString()) == magnitude;
            if (downOk && upOk) {
                int cmp = exact.subtract(down).compareTo(up.subtract(exact));
                if (cmp < 0) {
                    best = down;
                } else if (cmp > 0) {
                    best = up;
                } else {
                    best = exact.round(new MathContext(precision, RoundingMode.HALF_EVEN));
                }
            } else if (downOk) {
                best = down;
            } else if (upOk) {
                best = up;
            }
        }
        if (best == null) {
            best = exact.round(MathContext.DECIMAL64); // unreachable: 17 digits always round-trip
        }
        BigDecimal stripped = best.stripTrailingZeros();
        String digits = stripped.unscaledValue().toString();
        int decpt = digits.length() - stripped.scale(); // value = 0.digits × 10^decpt
        StringBuilder out = new StringBuilder(sign);
        if (decpt <= -4 || decpt > 16) {
            out.append(digits.charAt(0));
            if (digits.length() > 1) {
                out.append('.').append(digits, 1, digits.length());
            }
            int exponent = decpt - 1;
            out.append('e').append(exponent < 0 ? '-' : '+');
            int abs = Math.abs(exponent);
            if (abs < 10) {
                out.append('0');
            }
            out.append(abs);
        } else if (decpt <= 0) {
            out.append("0.");
            for (int k = 0; k < -decpt; k++) {
                out.append('0');
            }
            out.append(digits);
        } else if (decpt < digits.length()) {
            out.append(digits, 0, decpt).append('.').append(digits, decpt, digits.length());
        } else {
            out.append(digits);
            for (int k = digits.length(); k < decpt; k++) {
                out.append('0');
            }
            out.append(".0");
        }
        return out.toString();
    }
}
