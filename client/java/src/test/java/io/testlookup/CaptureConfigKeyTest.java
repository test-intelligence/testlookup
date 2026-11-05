package io.testlookup;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertTrue;

import io.testlookup.http.CaptureSettings;
import java.nio.file.Path;
import java.util.Collections;
import java.util.Map;
import java.util.Properties;
import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.io.TempDir;

/**
 * W32b: the RestAssured filter's capture switch can sit in {@code testlookup.properties} or a {@code -D} property
 * ({@code testlookup.capture_http}), both read through ConfigLoader as {@code reporting.capture_http} — the path the
 * filter reads. Before the mapping, ConfigLoader dropped the properties key and a "false" there did nothing.
 */
class CaptureConfigKeyTest {

    @TempDir
    Path dir;

    @Test
    @DisplayName("testlookup.capture_http=false in testlookup.properties turns a registered filter off")
    void propertiesFileKeyReachesTheSwitch() throws Exception {
        try (TestConfigScope ignored = new TestConfigScope()
                .classpathProperties(dir, Map.of("testlookup.capture_http", "false"))) {
            Object value = ConfigLoader.getString(ConfigLoader.load(), "reporting.capture_http", null);
            assertEquals("false", value);
            assertFalse(CaptureSettings.enabled(true, Collections.emptyMap(), value));
        }
    }

    @Test
    @DisplayName("-Dtestlookup.capture_http is mapped to reporting.capture_http; the env still decides first")
    void sysPropKeyIsMapped() {
        Properties props = new Properties();
        props.setProperty("testlookup.capture_http", "false");
        Object value = ConfigLoader.getString(ConfigLoader.load(Collections.emptyMap(), props), "reporting.capture_http", null);
        assertEquals("false", value);
        assertFalse(CaptureSettings.enabled(true, Collections.emptyMap(), value));
        assertTrue(CaptureSettings.enabled(true, Map.of(CaptureSettings.ENV_CAPTURE, "1"), value));
    }
}
