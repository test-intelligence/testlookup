package io.testlookup.http;

import org.junit.jupiter.api.Test;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertNull;
import static org.junit.jupiter.api.Assertions.assertTrue;

/**
 * A test's error message and stack trace lose their credentials and keep their file paths: the Java side of
 * client/tests/test_http_capture.py's failure-text tests. Samples are made up; none is a credential.
 */
class FailureTextTest {

    private static final String UNIX_FRAME = "/home/runner/work/shop/shop/tests/integration/test_checkout_flow.py";
    private static final String WINDOWS_FRAME =
        "C:\\agent\\_work\\1\\s\\tests\\integration\\checkout\\test_checkout_flow_long_name.py";
    private static final String PAT = "ghp_" + "w23dSamplePatValueQxZkLmNoPqRsTuVwXy";
    private static final String TRACE = String.join("\n",
        "java.lang.AssertionError: 500 != 200; sent db_password=w23fFailurePass and " + PAT, // gitleaks:allow (made-up sample for the redaction test)
        "  File \"" + UNIX_FRAME + "\", line 42",
        "  File \"" + WINDOWS_FRAME + "\", line 7",
        "    headers={'Authorization': 'Bearer w23fBearerSampleValue0123456789'}",
        "\tat com.example.shop.checkout.CheckoutServiceIntegrationTest.testCheckout(CheckoutServiceIntegrationTest.java:42)",
        "");

    @Test
    void failureTextLosesItsCredentials() {
        String out = HttpRedaction.failureText(TRACE);
        for (String raw : new String[] {"w23fBearerSampleValue0123456789", "w23fFailurePass", PAT}) {
            assertFalse(out.contains(raw), raw);
        }
        assertTrue(out.contains("Bearer [REDACTED]"), out);
        assertTrue(out.contains("db_password=[REDACTED]"), out);
    }

    @Test
    void failureTextKeepsFilePathsAndFrames() {
        String out = HttpRedaction.failureText(TRACE);
        assertTrue(out.contains(UNIX_FRAME), out);
        assertTrue(out.contains(WINDOWS_FRAME), out);
        assertTrue(out.contains("CheckoutServiceIntegrationTest.testCheckout(CheckoutServiceIntegrationTest.java:42)"), out);
        // The plain text pass would not keep the path: that is why failure text has its own.
        assertFalse(HttpText.textPass(TRACE).contains(UNIX_FRAME));
    }

    @Test
    void failureTextIsIdempotentAndTakesEmptyText() {
        String once = HttpRedaction.failureText(TRACE);
        assertEquals(once, HttpRedaction.failureText(once));
        assertEquals("", HttpRedaction.failureText(""));
        assertNull(HttpRedaction.failureText(null));
        assertEquals("plain assertion text", HttpRedaction.failureText("plain assertion text"));
    }
}
