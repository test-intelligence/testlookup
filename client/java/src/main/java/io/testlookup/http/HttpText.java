package io.testlookup.http;

import java.io.ByteArrayOutputStream;
import java.nio.ByteBuffer;
import java.nio.CharBuffer;
import java.nio.charset.CharacterCodingException;
import java.nio.charset.CharsetDecoder;
import java.nio.charset.CodingErrorAction;
import java.nio.charset.StandardCharsets;
import java.util.ArrayList;
import java.util.Arrays;
import java.util.Collections;
import java.util.HashSet;
import java.util.List;
import java.util.Set;
import java.util.function.Function;
import java.util.function.UnaryOperator;
import java.util.regex.Matcher;
import java.util.regex.Pattern;

/**
 * The text side of {@link HttpRedaction}: the text pass, the url / query / endpoint rules and the
 * Python-compatible string helpers. A line-by-line port of {@code client/http_capture.py} (itself the port of
 * {@code backend/app/services/http_exchange_redaction.py}); the cross-language specification is
 * {@code sdk-vectors/http_exchange.json}.
 *
 * <p>Python {@code re} and {@code str} differ from {@code java.util.regex} and {@code String}; every pattern
 * below is written in Python's syntax and goes through {@link #py(String, boolean)}, which translates the
 * differences that matter: {@code \d} is any Unicode decimal digit, {@code \s} is Python's whitespace set,
 * {@code \b} is Python's Unicode word boundary, {@code \Z} is the absolute end, {@code (?P<n>)} /
 * {@code (?P=n)} are Java's named group / back-reference, and {@code .} / {@code $} see only {@code \n} as a
 * line end (UNIX_LINES). Lengths: Python slices count code points, so every character cap here does too.
 */
final class HttpText {

    private HttpText() {
    }

    // ── Python's character classes ───────────────────────────────────────────

    /** Python's {@code str.isspace()} set (= what {@code \s} and {@code str.strip()} use), as class content. */
    static final String PY_SPACE_CHARS =
        "\\t\\n\\x0B\\f\\r\\x1C-\\x1F \\x85\\xA0\\u1680\\u2000-\\u200A\\u2028\\u2029\\u202F\\u205F\\u3000";

    /**
     * Python's Unicode word character: {@code str.isalnum()} or {@code _}. The literal U+1D400 (a letter, already
     * in {@code \p{L}}, so the class means the same) is load-bearing: a pattern text holding a supplementary
     * character makes Java compile its look-behinds as BehindS / NotBehindS, which step back one CODE POINT. The
     * plain look-behind steps back one UTF-16 unit, sees half of an astral character ("𝟏", U+1D7CF, a digit
     * and a word character in Python) and finds a word boundary Python does not (a differential run against
     * http_capture.py redacted "10.0.0.11𝟏5" as an IPv4 address).
     */
    private static final String PY_WORD = "[\\p{L}\\p{N}_𝐀]";

    /** Python's {@code \b}: a word character on exactly one side. */
    private static final String PY_BOUNDARY =
        "(?:(?<=" + PY_WORD + ")(?!" + PY_WORD + ")|(?<!" + PY_WORD + ")(?=" + PY_WORD + "))";

    static boolean pyIsSpace(char c) {
        if (c < 128) {
            return (c >= '\t' && c <= '\r') || (c >= 0x1C && c <= ' ');
        }
        return c == 0x85 || c == 0xA0 || c == 0x1680 || (c >= 0x2000 && c <= 0x200A) || c == 0x2028
            || c == 0x2029 || c == 0x202F || c == 0x205F || c == 0x3000;
    }

    /** Python's {@code str.strip()} (its whitespace set, not Java's). */
    static String pyStrip(String text) {
        int start = 0;
        int end = text.length();
        while (start < end && pyIsSpace(text.charAt(start))) {
            start++;
        }
        while (end > start && pyIsSpace(text.charAt(end - 1))) {
            end--;
        }
        return text.substring(start, end);
    }

    static String pyLower(String text) {
        return text.toLowerCase(java.util.Locale.ROOT);
    }

    static String pyUpper(String text) {
        return text.toUpperCase(java.util.Locale.ROOT);
    }

    /**
     * Python's {@code str.isdigit()}: non-empty, every code point a decimal digit (Nd) or one of Unicode's
     * Numeric_Type=Digit characters (superscripts, circled digits, ...; DerivedNumericType.txt).
     */
    static boolean pyIsDigit(String text) {
        if (text.isEmpty()) {
            return false;
        }
        for (int i = 0; i < text.length(); ) {
            int cp = text.codePointAt(i);
            if (!Character.isDigit(cp) && !numericTypeDigit(cp)) {
                return false;
            }
            i += Character.charCount(cp);
        }
        return true;
    }

    private static final int[] NUMERIC_TYPE_DIGIT = {
        0x00B2, 0x00B3, 0x00B9, 0x00B9, 0x1369, 0x1371, 0x19DA, 0x19DA, 0x2070, 0x2070, 0x2074, 0x2079,
        0x2080, 0x2089, 0x2460, 0x2468, 0x2474, 0x247C, 0x2488, 0x2490, 0x24EA, 0x24EA, 0x24F5, 0x24FD,
        0x24FF, 0x24FF, 0x2776, 0x277E, 0x2780, 0x2788, 0x278A, 0x2792, 0x10A40, 0x10A43, 0x10E60, 0x10E68,
        0x11052, 0x1105A, 0x1F100, 0x1F10A,
    };

    private static boolean numericTypeDigit(int cp) {
        for (int i = 0; i < NUMERIC_TYPE_DIGIT.length; i += 2) {
            if (cp >= NUMERIC_TYPE_DIGIT[i] && cp <= NUMERIC_TYPE_DIGIT[i + 1]) {
                return true;
            }
        }
        return false;
    }

    /** Python {@code int()} of a {@code \d+} run (any Unicode decimal digits). */
    static long parseDigits(String digits) {
        long value = 0;
        for (int i = 0; i < digits.length(); ) {
            int cp = digits.codePointAt(i);
            value = value * 10 + Character.digit(cp, 10);
            i += Character.charCount(cp);
        }
        return value;
    }

    // ── Python re → java.util.regex ──────────────────────────────────────────

    /** A Python {@code re} pattern text, compiled with Python's semantics (see the class comment). */
    static Pattern py(String pattern, boolean ignoreCase) {
        int flags = Pattern.UNIX_LINES;
        if (ignoreCase) {
            flags |= Pattern.CASE_INSENSITIVE | Pattern.UNICODE_CASE;
        }
        return Pattern.compile(translate(pattern), flags);
    }

    static String translate(String py) {
        StringBuilder out = new StringBuilder(py.length() + 64);
        boolean inClass = false;
        int i = 0;
        int n = py.length();
        while (i < n) {
            char c = py.charAt(i);
            if (c == '\\') {
                if (i + 1 >= n) {
                    throw new IllegalArgumentException("a trailing backslash: " + py);
                }
                char e = py.charAt(i + 1);
                switch (e) {
                    case 'd':
                        out.append("\\p{Nd}");
                        break;
                    case 's':
                        out.append(inClass ? PY_SPACE_CHARS : "[" + PY_SPACE_CHARS + "]");
                        break;
                    case 'S':
                        out.append("[^").append(PY_SPACE_CHARS).append(']');
                        break;
                    case 'b':
                        out.append(inClass ? "\\x08" : PY_BOUNDARY);
                        break;
                    case 'Z':
                        out.append("\\z");
                        break;
                    case 'D':
                    case 'w':
                    case 'W':
                    case 'B':
                        throw new IllegalArgumentException("no translation for \\" + e + " in " + py);
                    default:
                        out.append(c).append(e);
                }
                i += 2;
                continue;
            }
            if (inClass) {
                if (c == ']') {
                    inClass = false;
                } else if (c == '[' || c == '&') {
                    out.append('\\'); // literal in Python; a nested class / intersection in Java
                }
                out.append(c);
                i++;
                continue;
            }
            if (c == '[') {
                inClass = true;
                out.append(c);
                i++;
                if (i < n && py.charAt(i) == '^') {
                    out.append('^');
                    i++;
                }
                if (i < n && py.charAt(i) == ']') {
                    out.append("\\]"); // a leading ] is literal in Python
                    i++;
                }
                continue;
            }
            if (py.startsWith("(?P<", i)) {
                out.append("(?<");
                i += 4;
                continue;
            }
            if (py.startsWith("(?P=", i)) {
                int close = py.indexOf(')', i);
                out.append("\\k<").append(py, i + 4, close).append('>');
                i = close + 1;
                continue;
            }
            out.append(c);
            i++;
        }
        return out.toString();
    }

    /** Python {@code re.sub} with a function replacement. */
    static String sub(Pattern pattern, String text, Function<Matcher, String> replacement) {
        Matcher m = pattern.matcher(text);
        if (!m.find()) {
            return text;
        }
        StringBuilder out = new StringBuilder(text.length() + 16);
        int last = 0;
        do {
            out.append(text, last, m.start());
            out.append(replacement.apply(m));
            last = m.end();
        } while (m.find());
        out.append(text, last, text.length());
        return out.toString();
    }

    private static Function<Matcher, String> literal(String replacement) {
        return m -> replacement;
    }

    private static Function<Matcher, String> groupThenRedacted() {
        return m -> m.group(1) + HttpRedaction.REDACTED;
    }

    // ── Strings: clean, cut, measure ─────────────────────────────────────────

    /** {@code _clean}: NUL removed, a lone surrogate becomes U+FFFD (Python's {@code [\x00\ud800-\udfff]}). */
    static String clean(String text) {
        int n = text.length();
        int i = 0;
        for (; i < n; i++) {
            char c = text.charAt(i);
            if (c == 0) {
                break;
            }
            if (Character.isSurrogate(c)) {
                if (Character.isHighSurrogate(c) && i + 1 < n && Character.isLowSurrogate(text.charAt(i + 1))) {
                    i++;
                    continue;
                }
                break;
            }
        }
        if (i == n) {
            return text;
        }
        StringBuilder out = new StringBuilder(n);
        out.append(text, 0, i);
        for (; i < n; i++) {
            char c = text.charAt(i);
            if (c == 0) {
                continue;
            }
            if (Character.isHighSurrogate(c) && i + 1 < n && Character.isLowSurrogate(text.charAt(i + 1))) {
                out.append(c).append(text.charAt(i + 1));
                i++;
            } else if (Character.isSurrogate(c)) {
                out.append('�');
            } else {
                out.append(c);
            }
        }
        return out.toString();
    }

    /** Python {@code text[:limit]}: the first {@code limit} code points. */
    static String cutChars(String text, int limit) {
        if (text.length() <= limit) {
            return text;
        }
        if (text.codePointCount(0, text.length()) <= limit) {
            return text;
        }
        return text.substring(0, text.offsetByCodePoints(0, limit));
    }

    /** {@code len(text.encode("utf-8"))} of a cleaned text. */
    static int utf8Len(String text) {
        int bytes = 0;
        for (int i = 0; i < text.length(); ) {
            int cp = text.codePointAt(i);
            bytes += utf8Width(cp);
            i += Character.charCount(cp);
        }
        return bytes;
    }

    private static int utf8Width(int cp) {
        if (cp < 0x80) {
            return 1;
        }
        if (cp < 0x800) {
            return 2;
        }
        return cp < 0x10000 ? 3 : 4;
    }

    /** {@code _cut_bytes}: at most {@code limit} UTF-8 bytes, never splitting a code point. */
    static String cutBytes(String text, int limit) {
        int bytes = 0;
        for (int i = 0; i < text.length(); ) {
            int cp = text.codePointAt(i);
            int width = utf8Width(cp);
            if (bytes + width > limit) {
                return text.substring(0, i);
            }
            bytes += width;
            i += Character.charCount(cp);
        }
        return text;
    }

    private static final int SETTLE_ROUNDS = 6;

    static String settle(String text, UnaryOperator<String> step, UnaryOperator<String> cut) {
        for (int round = 0; round < SETTLE_ROUNDS; round++) {
            String next = cut.apply(step.apply(text));
            if (next.equals(text)) {
                return next;
            }
            text = next;
        }
        return text;
    }

    /** {@code _markers}: how many {@code [REDACTED...} markers a text holds. */
    static int markers(String text) {
        if (text == null || text.isEmpty()) {
            return 0;
        }
        int count = 0;
        int at = text.indexOf("[REDACTED");
        while (at >= 0) {
            count++;
            at = text.indexOf("[REDACTED", at + "[REDACTED".length());
        }
        return count;
    }

    /** Code-point order (Python's {@code str} comparison), not UTF-16 order. */
    static int compareCodePoints(String a, String b) {
        int i = 0;
        int j = 0;
        while (i < a.length() && j < b.length()) {
            int ca = a.codePointAt(i);
            int cb = b.codePointAt(j);
            if (ca != cb) {
                return Integer.compare(ca, cb);
            }
            i += Character.charCount(ca);
            j += Character.charCount(cb);
        }
        if (i < a.length()) {
            return 1;
        }
        return j < b.length() ? -1 : 0;
    }

    // ── redaction_service's key names ────────────────────────────────────────

    private static Set<String> setOf(String... names) {
        return Collections.unmodifiableSet(new HashSet<>(Arrays.asList(names)));
    }

    private static Set<String> union(Set<String> a, Set<String> b) {
        Set<String> out = new HashSet<>(a);
        out.addAll(b);
        return Collections.unmodifiableSet(out);
    }

    private static final Set<String> SECRET_KEY_NAMES = setOf(
        "password", "passwd", "secret", "token", "api_key", "apikey",
        "api_token", "authorization", "auth_token", "access_token",
        "refresh_token", "private_key", "secret_key", "credential",
        "credentials", "cookie", "hashed_password", "jwt", "bearer",
        "smtp_password", "minio_secret_key", "webhook_secret",
        "client_secret", "signing_secret", "personal_access_token",
        "private_token", "session_token", "id_token", "encryption_key",
        "ssh_key", "deploy_key", "webhook_token");
    private static final Set<String> PII_KEY_NAMES = setOf(
        "email", "email_address", "phone", "phone_number", "mobile",
        "ssn", "social_security", "social_security_number",
        "credit_card", "card_number", "cc_number",
        "dob", "date_of_birth", "address", "street_address",
        "national_id", "passport_number", "drivers_license");
    static final Set<String> SENSITIVE_KEYS = union(SECRET_KEY_NAMES, PII_KEY_NAMES);
    static final Set<String> LOG_SECRET_KEYS = union(SECRET_KEY_NAMES, setOf(
        "x_api_key", "set_cookie", "proxy_authorization",
        "x_api_token", "x_auth_token", "x_webhook_secret",
        "access_tokens", "refresh_tokens", "webhook_url", "smtp_pass",
        "signing_key", "hmac_key", "app_secret_key_previous", "jwt_secret_key_previous",
        "passphrase", "client_secret", "session_cookie"));
    static final Set<String> LOG_SECRET_NAME_EXCEPTIONS =
        setOf("next_page_token", "page_token", "pagination_token", "next_token");
    static final List<String> LOG_SECRET_SUFFIXES = Collections.unmodifiableList(Arrays.asList(
        "_token", "_password", "_passwd", "_secret", "_api_key", "_apikey",
        "_secret_key", "_access_key", "_private_key", "_webhook_url",
        "_credential", "_credentials", "_passphrase"));

    private static final Pattern CAMEL_BOUNDARY = Pattern.compile("(?<=[a-z0-9])(?=[A-Z])");

    static boolean isLogSecretName(String name) {
        String lowered = pyLower(name).replace('-', '_');
        if (LOG_SECRET_NAME_EXCEPTIONS.contains(lowered)) {
            return false;
        }
        if (LOG_SECRET_KEYS.contains(lowered)) {
            return true;
        }
        return endsWithAny(lowered, LOG_SECRET_SUFFIXES);
    }

    static boolean keyIsSecret(String key) {
        String plain = pyLower(key).replace('-', '_');
        String snake = pyLower(CAMEL_BOUNDARY.matcher(key).replaceAll("_")).replace('-', '_');
        return nameIsSecret(plain) || nameIsSecret(snake);
    }

    private static boolean nameIsSecret(String name) {
        return SENSITIVE_KEYS.contains(name) || isLogSecretName(name);
    }

    static boolean endsWithAny(String text, List<String> suffixes) {
        for (String suffix : suffixes) {
            if (text.endsWith(suffix)) {
                return true;
            }
        }
        return false;
    }

    // ── The text pass ────────────────────────────────────────────────────────

    private static final class Rule {
        final Pattern pattern;
        final Function<Matcher, String> replacement;

        Rule(Pattern pattern, Function<Matcher, String> replacement) {
            this.pattern = pattern;
            this.replacement = replacement;
        }
    }

    private static final String AUTH_SCHEMES =
        "Basic|Bearer|Digest|Negotiate|NTLM|Kerberos|Token|ApiKey|"
            + "AWS4-HMAC-SHA256|DPoP|HOBA|Mutual|OAuth|SCRAM-SHA-1|SCRAM-SHA-256|vapid";

    private static final Pattern AUTHORIZATION_HEADER = py(
        "(?P<head>(?<![A-Za-z0-9])Authorization(?:\\\\?[\"'])?[ \\t]*[:=][ \\t]*"
            + "(?P<open>\\\\?[\"'])?)"
            + "(?P<scheme>(?:" + AUTH_SCHEMES + ")[ \\t]+)?"
            + "(?:(?!(?P=open))[^\\r\\n])+",
        true);

    /** redaction_service.redact_text's patterns, in its order. */
    private static final List<Rule> TEXT_PATTERNS = Collections.unmodifiableList(Arrays.asList(
        new Rule(py("(Bearer\\s+)[A-Za-z0-9\\-_\\.]{20,}", true), groupThenRedacted()),
        new Rule(AUTHORIZATION_HEADER, m -> m.group("head")
            + (m.group("scheme") == null ? "" : m.group("scheme")) + HttpRedaction.REDACTED),
        new Rule(py("(hooks\\.slack\\.com/(?:services|workflows|triggers)/)[^\\s'\"<>]+", true), groupThenRedacted()),
        new Rule(py("(\\.webhook\\.office\\.com/webhookb2/)[^\\s'\"<>]+", true), groupThenRedacted()),
        new Rule(py("(outlook\\.office(?:365)?\\.com/webhook/)[^\\s'\"<>]+", true), groupThenRedacted()),
        new Rule(py("(discord(?:app)?\\.com/api/webhooks/)[^\\s'\"<>]+", true), groupThenRedacted()),
        new Rule(py("((?:\\.logic\\.azure\\.com|\\.powerplatform\\.com)[^\\s'\"<>]*?[?&]sig=)[^\\s&'\"<>]+", true),
            groupThenRedacted()),
        new Rule(py("((?:Set-)?Cookie:\\s*)[^\\r\\n]+", true), groupThenRedacted()),
        new Rule(py("(api[_-]?key\\s*[:=]\\s*)['\"]?[^\\s'\",]{4,}['\"]?", true), groupThenRedacted()),
        new Rule(py("(token\\s*[:=]\\s*)['\"]?[^\\s'\",]{4,}['\"]?", true), groupThenRedacted()),
        new Rule(py("(password\\s*[:=]\\s*)['\"]?[^\\s'\",]{4,}['\"]?", true), groupThenRedacted()),
        new Rule(py("(secret[_-]?key\\s*[:=]\\s*)['\"]?[A-Za-z0-9\\-_]{10,}['\"]?", true), groupThenRedacted()),
        new Rule(py("(AKIA[A-Z0-9]{16})", false), literal("[REDACTED_AWS_KEY]")),
        new Rule(py("eyJ[A-Za-z0-9_-]{20,}\\.eyJ[A-Za-z0-9_-]{20,}\\.[A-Za-z0-9_-]{20,}", false),
            literal("[REDACTED_JWT]")),
        new Rule(py("(://[^:]+:)[^@]{4,}(@)", false), m -> m.group(1) + HttpRedaction.REDACTED + m.group(2)),
        new Rule(py("[a-zA-Z0-9._%+\\-]+@[a-zA-Z0-9.\\-]+\\.[a-zA-Z]{2,}", false), literal("[REDACTED_EMAIL]")),
        new Rule(py("\\b(\\+?1[-.\\s]?)?\\(?\\d{3}\\)?[-.\\s]?\\d{3}[-.\\s]?\\d{4}\\b", false),
            literal("[REDACTED_PHONE]")),
        new Rule(py("\\b\\d{3}[-\\s]\\d{2}[-\\s]\\d{4}\\b", false), literal("[REDACTED_SSN]")),
        new Rule(py("\\b\\d{4}[-\\s]?\\d{4}[-\\s]?\\d{4}[-\\s]?\\d{4}\\b(?!-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}\\b)", false),
            literal("[REDACTED_CC]")),
        new Rule(py("\\b\\d{1,3}\\.\\d{1,3}\\.\\d{1,3}\\.\\d{1,3}\\b", false), literal("[REDACTED_IP]"))));

    private static final List<Pattern> TOKEN_SHAPE_RES = compileAll(HttpRedaction.TOKEN_SHAPES);
    static final Pattern METHOD = py(HttpRedaction.METHOD_RE, false);
    static final Pattern FIELD_NAME = py(HttpRedaction.FIELD_NAME_RE, false);
    private static final List<Pattern> ID_SEGMENTS = compileAll(HttpRedaction.ENDPOINT_ID_SEGMENT_RES);
    private static final Set<String> HEADER_DENY = new HashSet<>(HttpRedaction.HEADER_DENYLIST);
    private static final Set<String> QUERY_DENY = new HashSet<>(HttpRedaction.QUERY_DENYLIST);

    static final String MARKER_HEAD;
    static final String MARKER_TAIL;
    static final Pattern MARKER_AT_END;

    static {
        String[] parts = HttpRedaction.TRUNCATION_MARKER.split("\\{omitted\\}", -1);
        MARKER_HEAD = parts[0];
        MARKER_TAIL = parts[1];
        // Python: re.escape(head) + r"(\d{1,15})" + re.escape(tail) + r"\Z" (\d = any Unicode decimal digit).
        MARKER_AT_END = Pattern.compile(
            Pattern.quote(MARKER_HEAD) + "(\\p{Nd}{1,15})" + Pattern.quote(MARKER_TAIL) + "\\z");
    }

    /**
     * {@code JSON_PAIR_RE} with ONE change that keeps Python's meaning: the string-value loop
     * {@code (?:[^"\\]|\\.)*} is possessive here. Java runs a non-possessive alternation loop by recursion
     * (one stack frame group per character), so a 20 000-character JSON string value overflows the stack; the
     * possessive loop iterates. Equivalent: that loop can only stop before a quote, at the end, or at a
     * backslash before a newline, and giving characters back never yields a quote or the end.
     */
    static final Pattern JSON_PAIR = py(possessiveValueLoop(HttpRedaction.JSON_PAIR_RE), false);
    static final Pattern FORM_PAIR = py(HttpRedaction.FORM_PAIR_RE, false);
    private static final Pattern URL_PARTS = py(HttpRedaction.URL_PARTS_RE, false);

    static String possessiveValueLoop(String jsonPairRe) {
        String loop = "|\\\\.)*(?:";
        int at = jsonPairRe.indexOf(loop);
        if (at < 0 || jsonPairRe.indexOf(loop, at + 1) >= 0) {
            throw new IllegalStateException("JSON_PAIR_RE changed: the value loop is not where the Java port expects it");
        }
        return jsonPairRe.substring(0, at) + "|\\\\.)*+(?:" + jsonPairRe.substring(at + loop.length());
    }

    private static List<Pattern> compileAll(List<String> patterns) {
        List<Pattern> out = new ArrayList<>();
        for (String pattern : patterns) {
            out.add(py(pattern, false));
        }
        return Collections.unmodifiableList(out);
    }

    static boolean headerIsSecret(String lowered) {
        return HEADER_DENY.contains(lowered) || endsWithAny(lowered, HttpRedaction.HEADER_SECRET_SUFFIXES);
    }

    static String tokenPass(String text) {
        for (Pattern shape : TOKEN_SHAPE_RES) {
            text = sub(shape, text, literal(HttpRedaction.REDACTED));
        }
        return text;
    }

    static String redactText(String text) {
        if (text == null || text.isEmpty()) {
            return text;
        }
        for (Rule rule : TEXT_PATTERNS) {
            text = sub(rule.pattern, text, rule.replacement);
        }
        return text;
    }

    private static String redactJsonPair(Matcher m) {
        if (!keyIsSecret(m.group(1))) {
            return m.group();
        }
        return "\"" + m.group(1) + "\"" + m.group(2) + "\"" + HttpRedaction.REDACTED + "\"";
    }

    private static String redactFormPair(Matcher m) {
        if (!keyIsSecret(m.group(1))) {
            return m.group();
        }
        return m.group(1) + "=" + HttpRedaction.REDACTED;
    }

    /** Secret-named pairs → token shapes → the credential / PII patterns → token shapes. Idempotent. */
    static String textPass(String text) {
        if (text == null || text.isEmpty()) {
            return text;
        }
        String paired = sub(FORM_PAIR, sub(JSON_PAIR, text, HttpText::redactJsonPair), HttpText::redactFormPair);
        return tokenPass(redactText(tokenPass(paired)));
    }

    // ── The url ──────────────────────────────────────────────────────────────

    /** {@code urllib.parse.unquote_plus} (UTF-8, errors="replace"). */
    static String unquotePlus(String text) {
        return unquote(text.replace('+', ' '));
    }

    /** {@code urllib.parse.unquote}: ASCII runs are percent-decoded to bytes and read as UTF-8 (errors="replace"). */
    static String unquote(String text) {
        if (text.indexOf('%') < 0) {
            return text;
        }
        StringBuilder out = new StringBuilder(text.length());
        int i = 0;
        int n = text.length();
        while (i < n) {
            int j = i;
            while (j < n && text.charAt(j) > 0x7F) {
                j++;
            }
            out.append(text, i, j);
            int k = j;
            while (k < n && text.charAt(k) <= 0x7F) {
                k++;
            }
            if (k > j) {
                out.append(unquoteAscii(text.substring(j, k)));
            }
            i = k;
        }
        return out.toString();
    }

    private static String unquoteAscii(String run) {
        ByteArrayOutputStream bytes = new ByteArrayOutputStream(run.length());
        String[] bits = run.split("%", -1);
        writeAscii(bytes, bits[0]);
        for (int b = 1; b < bits.length; b++) {
            String item = bits[b];
            if (item.length() >= 2 && isHex(item.charAt(0)) && isHex(item.charAt(1))) {
                bytes.write(Integer.parseInt(item.substring(0, 2), 16));
                writeAscii(bytes, item.substring(2));
            } else {
                bytes.write('%');
                writeAscii(bytes, item);
            }
        }
        CharsetDecoder decoder = StandardCharsets.UTF_8.newDecoder()
            .onMalformedInput(CodingErrorAction.REPLACE)
            .onUnmappableCharacter(CodingErrorAction.REPLACE);
        try {
            CharBuffer chars = decoder.decode(ByteBuffer.wrap(bytes.toByteArray()));
            return chars.toString();
        } catch (CharacterCodingException e) {
            throw new IllegalStateException(e); // REPLACE never throws
        }
    }

    private static void writeAscii(ByteArrayOutputStream out, String ascii) {
        for (int i = 0; i < ascii.length(); i++) {
            out.write(ascii.charAt(i));
        }
    }

    private static boolean isHex(char c) {
        return (c >= '0' && c <= '9') || (c >= 'a' && c <= 'f') || (c >= 'A' && c <= 'F');
    }

    private static final char[] HEX_UPPER = "0123456789ABCDEF".toCharArray();

    /** {@code urllib.parse.quote(text, safe="[]")}: UTF-8, every byte outside {@code A-Za-z0-9_.-~[]} as %XX. */
    static String quote(String text) {
        byte[] raw = text.getBytes(StandardCharsets.UTF_8);
        StringBuilder out = new StringBuilder(raw.length + 16);
        for (byte b : raw) {
            int v = b & 0xFF;
            boolean safe = (v >= 'A' && v <= 'Z') || (v >= 'a' && v <= 'z') || (v >= '0' && v <= '9')
                || v == '_' || v == '.' || v == '-' || v == '~' || v == '[' || v == ']';
            if (safe) {
                out.append((char) v);
            } else {
                out.append('%').append(HEX_UPPER[v >> 4]).append(HEX_UPPER[v & 0xF]);
            }
        }
        return out.toString();
    }

    private static String redactNetloc(String netloc) {
        int at = netloc.lastIndexOf('@');
        if (at < 0) {
            return netloc;
        }
        String userinfo = netloc.substring(0, at);
        String host = netloc.substring(at + 1);
        int colon = userinfo.indexOf(':');
        if (colon >= 0) {
            return userinfo.substring(0, colon) + ":" + HttpRedaction.REDACTED + "@" + host;
        }
        return HttpRedaction.REDACTED + "@" + host;
    }

    static String redactQuery(String query) {
        if (query.isEmpty()) {
            return query;
        }
        List<String> parts = new ArrayList<>();
        for (String part : query.split("&", -1)) {
            int eq = part.indexOf('=');
            String name = eq < 0 ? part : part.substring(0, eq);
            String sep = eq < 0 ? "" : "=";
            String value = eq < 0 ? "" : part.substring(eq + 1);
            name = tokenPass(name);
            if (!value.isEmpty() && QUERY_DENY.contains(pyLower(pyStrip(unquotePlus(name))))) {
                parts.add(name + "=" + HttpRedaction.REDACTED);
                continue;
            }
            if (!value.isEmpty()) {
                String decoded = unquotePlus(value);
                String redacted = textPass(decoded);
                if (!redacted.equals(decoded)) {
                    value = quote(redacted);
                }
            }
            parts.add(name + sep + value);
        }
        return String.join("&", parts);
    }

    /** The token shapes per path SEGMENT (the generic 40+ run includes {@code /}). */
    static String pathTokenPass(String path) {
        String[] segments = path.split("/", -1);
        for (int i = 0; i < segments.length; i++) {
            segments[i] = tokenPass(segments[i]);
        }
        return String.join("/", segments);
    }

    private static String redactUrlOnce(String url) {
        Matcher m = URL_PARTS.matcher(url);
        String scheme = null;
        String netloc = null;
        String path = url;
        String query = null;
        if (m.matches()) {
            scheme = m.group(1);
            netloc = m.group(2);
            path = m.group(3) == null ? "" : m.group(3);
            query = m.group(4);
        }
        StringBuilder out = new StringBuilder(url.length() + 16);
        if (scheme != null) {
            out.append(scheme).append(':');
        }
        if (netloc != null) {
            out.append("//").append(redactNetloc(netloc));
        }
        out.append(pathTokenPass(path));
        if (query != null) {
            out.append('?').append(redactQuery(query));
        }
        return out.toString(); // the fragment is dropped: never sent over HTTP
    }

    /** {@code redact_url}'s text (the caller counts its markers). */
    static String redactUrl(String url) {
        String text = url == null ? "" : pyStrip(clean(url));
        return settle(text, HttpText::redactUrlOnce, value -> cutChars(value, HttpRedaction.URL_MAX));
    }

    // ── The endpoint ─────────────────────────────────────────────────────────

    private static String pathOf(String text) {
        String path = text;
        int hash = path.indexOf('#');
        if (hash >= 0) {
            path = path.substring(0, hash);
        }
        int question = path.indexOf('?');
        if (question >= 0) {
            path = path.substring(0, question);
        }
        int scheme = path.indexOf("://");
        if (scheme >= 0) {
            path = path.substring(scheme + 3);
            int slash = path.indexOf('/');
            path = slash >= 0 ? path.substring(slash) : "";
        }
        return path.isEmpty() ? "/" : path;
    }

    private static String finishEndpoint(String path) {
        if (!path.startsWith("/")) {
            path = "/" + path;
        }
        return settle(path, HttpText::pathTokenPass, value -> cutChars(value, HttpRedaction.ENDPOINT_MAX));
    }

    static String deriveEndpoint(String url) {
        String path = pathOf(url == null ? "" : pyStrip(clean(url)));
        String[] segments = path.split("/", -1);
        for (int i = 0; i < segments.length; i++) {
            if (!segments[i].isEmpty() && isIdSegment(segments[i])) {
                segments[i] = "{id}";
            }
        }
        return finishEndpoint(String.join("/", segments));
    }

    private static boolean isIdSegment(String segment) {
        for (Pattern pattern : ID_SEGMENTS) {
            if (pattern.matcher(segment).matches()) {
                return true;
            }
        }
        return false;
    }

    static String normaliseEndpoint(String text) {
        return finishEndpoint(pathOf(text == null ? "" : pyStrip(clean(text))));
    }
}
