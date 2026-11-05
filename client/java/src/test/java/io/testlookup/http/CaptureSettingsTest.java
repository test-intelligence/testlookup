package io.testlookup.http;

import org.junit.jupiter.api.Test;

import java.util.Collections;
import java.util.HashMap;
import java.util.Map;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertTrue;

/**
 * Capture is OFF unless asked. The first test is client/tests/test_http_capture.py's
 * {@code test_capture_is_off_unless_asked} with "registered" in place of the pytest flag; the rest pin the
 * Java rule (W32 contract §1.12 point 3, D-6): env &gt; config &gt; registration, so a falsy env or config
 * value turns capture off even when the filter is registered (Python lets its flag win there).
 */
class CaptureSettingsTest {

    private static Map<String, String> env(String value) {
        Map<String, String> env = new HashMap<>();
        env.put(CaptureSettings.ENV_CAPTURE, value);
        return env;
    }

    private static final Map<String, String> NO_ENV = Collections.emptyMap();

    @Test
    void captureIsOffUnlessAsked() {
        assertFalse(CaptureSettings.enabled(false, NO_ENV, null));
        assertTrue(CaptureSettings.enabled(true, NO_ENV, null));
        assertTrue(CaptureSettings.enabled(false, env("1"), null));
        assertFalse(CaptureSettings.enabled(false, env("off"), "true"));
        assertTrue(CaptureSettings.enabled(false, NO_ENV, "true"));
        assertTrue(CaptureSettings.enabled(false, NO_ENV, true));
    }

    @Test
    void theNamesAreTheSdkWideOnes() {
        assertEquals("TESTLOOKUP_CAPTURE_HTTP", CaptureSettings.ENV_CAPTURE);
        assertEquals("testlookup.capture_http", CaptureSettings.CONFIG_KEY);
    }

    @Test
    void aFalsyEnvTurnsCaptureOffEvenWhenTheFilterIsRegistered() {
        for (String word : new String[] {"0", "false", "no", "off", " OFF ", "False", "\tno\n"}) {
            assertFalse(CaptureSettings.enabled(true, env(word), null), word);
            assertFalse(CaptureSettings.enabled(true, env(word), true), word + " over config true");
        }
    }

    @Test
    void aTruthyEnvTurnsCaptureOnOverEverything() {
        for (String word : new String[] {"1", "true", "yes", "on", " YES ", "On"}) {
            assertTrue(CaptureSettings.enabled(false, env(word), null), word);
            assertTrue(CaptureSettings.enabled(false, env(word), false), word + " over config false");
            assertTrue(CaptureSettings.enabled(true, env(word), "off"), word + " over config off");
        }
    }

    @Test
    void theConfigValueDecidesBeforeRegistration() {
        assertFalse(CaptureSettings.enabled(true, NO_ENV, false));
        assertFalse(CaptureSettings.enabled(true, NO_ENV, "no"));
        assertFalse(CaptureSettings.enabled(true, NO_ENV, " 0 "));
        assertTrue(CaptureSettings.enabled(false, NO_ENV, "YES"));
        assertTrue(CaptureSettings.enabled(false, NO_ENV, Boolean.TRUE));
    }

    @Test
    void registrationDecidesWhenNeitherSwitchAnswers() {
        for (Object config : new Object[] {null, "", "  ", "maybe", 1, 0}) {
            assertTrue(CaptureSettings.enabled(true, NO_ENV, config), String.valueOf(config));
            assertFalse(CaptureSettings.enabled(false, NO_ENV, config), String.valueOf(config));
        }
        for (String word : new String[] {"", "   ", "maybe", "2"}) {
            assertTrue(CaptureSettings.enabled(true, env(word), null), "env '" + word + "'");
            assertFalse(CaptureSettings.enabled(false, env(word), null), "env '" + word + "'");
        }
        assertTrue(CaptureSettings.enabled(true, null, null), "a null env map is an empty one");
        assertFalse(CaptureSettings.enabled(false, null, null), "a null env map is an empty one");
    }
}
