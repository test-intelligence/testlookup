package io.testlookup.http;

import java.util.Arrays;
import java.util.Collections;
import java.util.HashSet;
import java.util.Map;
import java.util.Set;

/**
 * Whether HTTP exchange capture is on. OFF unless asked; the same truthy / falsy words as
 * {@code client/http_capture.py} ({@code 1 true yes on} / {@code 0 false no off}, trimmed, any case).
 *
 * <p>The Java rule (W32 contract §1.12 point 3, decision D-6) differs from Python's {@code capture_enabled}
 * on purpose: registering the RestAssured filter IS the opt-in, and the switches above it can still turn it
 * off. Precedence, first decisive answer wins:
 * <ol>
 *   <li>{@value #ENV_CAPTURE}: a truthy word → ON, a falsy word → OFF (even when the filter is registered;
 *       Python lets its pytest flag win over a falsy env);</li>
 *   <li>the {@value #CONFIG_KEY} config value: a Boolean, or a truthy / falsy word → that answer;</li>
 *   <li>registration: ON when the filter is registered, otherwise OFF.</li>
 * </ol>
 * A blank or unrecognised env / config value is not an answer and falls through.
 */
public final class CaptureSettings {

    public static final String ENV_CAPTURE = "TESTLOOKUP_CAPTURE_HTTP";
    /** The config key ({@code ConfigLoader} dot path) that can switch capture on or off. */
    public static final String CONFIG_KEY = "testlookup.capture_http";

    private static final Set<String> TRUTHY = words("1", "true", "yes", "on");
    private static final Set<String> FALSEY = words("0", "false", "no", "off");

    private CaptureSettings() {
    }

    /**
     * @param registered  whether the capture filter is registered (the opt-in)
     * @param env         the environment (e.g. {@code System.getenv()}); {@code null} = empty
     * @param configValue the {@value #CONFIG_KEY} value ({@code Boolean}, {@code String} or {@code null})
     */
    public static boolean enabled(boolean registered, Map<String, String> env, Object configValue) {
        Boolean fromEnv = word(env == null ? null : env.get(ENV_CAPTURE));
        if (fromEnv != null) {
            return fromEnv;
        }
        Boolean fromConfig = configValue instanceof Boolean ? (Boolean) configValue
            : configValue instanceof String ? word((String) configValue) : null;
        if (fromConfig != null) {
            return fromConfig;
        }
        return registered;
    }

    /** TRUE / FALSE for a truthy / falsy word, {@code null} for anything else (blank, unknown, null). */
    private static Boolean word(String raw) {
        if (raw == null) {
            return null;
        }
        String lowered = HttpText.pyLower(HttpText.pyStrip(raw));
        if (TRUTHY.contains(lowered)) {
            return Boolean.TRUE;
        }
        if (FALSEY.contains(lowered)) {
            return Boolean.FALSE;
        }
        return null;
    }

    private static Set<String> words(String... words) {
        return Collections.unmodifiableSet(new HashSet<>(Arrays.asList(words)));
    }
}
