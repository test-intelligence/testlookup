package com.example;

import org.testng.Assert;
import org.testng.annotations.Test;

/**
 * Demonstrates that ordinary TestNG tests stream to TestLookup with zero
 * test-code changes. This file has no TestLookup imports — the listener
 * registered in testng.xml does all the wiring.
 */
public class SmokeTests {

    @Test
    public void checkoutPasses() {
        sleep(40);
        Assert.assertEquals(1 + 1, 2);
    }

    @Test
    public void dashboardLoads() {
        sleep(120);
        Assert.assertTrue(true);
    }

    @Test(description = "Demonstrates a FAILED status reaching the live UI")
    public void paymentFailsIntentionally() {
        sleep(80);
        Assert.assertEquals(500, 200, "expected HTTP 200, got 500");
    }

    @Test(enabled = false, description = "Demonstrates a SKIPPED status")
    public void featureFlaggedOff() {
        Assert.fail("should never run");
    }

    @Test(dataProvider = "items")
    public void parametrisedRow(int n) {
        Assert.assertTrue(n > 0);
    }

    @org.testng.annotations.DataProvider(name = "items")
    public Object[][] items() {
        return new Object[][] { {1}, {2}, {3} };
    }

    private static void sleep(long ms) {
        try { Thread.sleep(ms); } catch (InterruptedException ignored) { }
    }
}
