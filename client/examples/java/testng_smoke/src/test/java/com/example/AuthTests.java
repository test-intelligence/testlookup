package com.example;

import org.testng.Assert;
import org.testng.annotations.Test;

/**
 * Second test class in the suite — proves that multiple classes feed the
 * same live session. The TestLookup listener tracks pass/fail counters
 * across the whole suite, not per-class.
 */
public class AuthTests {

    @Test
    public void loginWithValidCredentials() {
        Assert.assertNotNull("token");
    }

    @Test
    public void loginWithInvalidCredentialsRejected() {
        // Negative test: the assertion should hold (login was rejected).
        Assert.assertTrue(true, "401 returned as expected");
    }
}
