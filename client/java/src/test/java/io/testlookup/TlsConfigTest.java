package io.testlookup;

import org.junit.jupiter.api.AfterEach;
import org.junit.jupiter.api.BeforeAll;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.io.TempDir;

import javax.net.ssl.KeyManagerFactory;
import javax.net.ssl.SSLContext;
import java.io.InputStream;
import java.nio.file.Files;
import java.nio.file.Path;
import java.nio.file.Paths;
import java.security.KeyStore;
import java.util.List;
import java.util.Map;
import java.util.Properties;
import java.util.concurrent.TimeUnit;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertThrows;
import static org.junit.jupiter.api.Assertions.assertTrue;

/**
 * TESTLOOKUP_CA_CERT / TESTLOOKUP_INSECURE (and testlookup.ca_cert_path /
 * testlookup.insecure) for the Java SDK, which had no way to reach a server
 * with a self-signed certificate short of editing the JVM's trust store.
 *
 * <p>The server here is a real HTTPS server on 127.0.0.1 whose certificate
 * {@code keytool} generates for this test.
 */
class TlsConfigTest {

    /** Certificates are generated once per class: keytool takes about a second each. */
    @TempDir static Path certs;
    private static Path caPem;
    private static SSLContext serverTls;
    /** A certificate that names another host. */
    private static Path otherHostPem;
    private static SSLContext otherHostTls;

    @TempDir Path dir;
    private FakeTestLookupServer server;
    private FakeTestLookupServer otherHostServer;

    @BeforeAll
    static void generateCertificates() throws Exception {
        caPem = certs.resolve("ca.pem");
        serverTls = selfSigned("tl", "ip:127.0.0.1,dns:localhost", caPem);
        otherHostPem = certs.resolve("other.pem");
        otherHostTls = selfSigned("other", "dns:elsewhere.invalid", otherHostPem);
    }

    @BeforeEach
    void startHttpsServers() throws Exception {
        server = FakeTestLookupServer.https(serverTls);
        otherHostServer = FakeTestLookupServer.https(otherHostTls);
    }

    @AfterEach
    void stop() {
        if (server != null) server.close();
        if (otherHostServer != null) otherHostServer.close();
    }

    /** A server TLS context for a fresh self-signed cert; its PEM goes to {@code pem}. */
    private static SSLContext selfSigned(String alias, String san, Path pem) throws Exception {
        Path keystore = certs.resolve(alias + ".p12");
        keytool("-genkeypair", "-alias", alias, "-keyalg", "RSA", "-keysize", "2048",
            "-dname", "CN=" + alias, "-ext", "SAN=" + san, "-validity", "2",
            "-keystore", keystore.toString(), "-storetype", "PKCS12",
            "-storepass", "changeit", "-keypass", "changeit", "-noprompt");
        keytool("-exportcert", "-rfc", "-alias", alias, "-keystore", keystore.toString(),
            "-storetype", "PKCS12", "-storepass", "changeit", "-file", pem.toString());

        KeyStore ks = KeyStore.getInstance("PKCS12");
        try (InputStream in = Files.newInputStream(keystore)) {
            ks.load(in, "changeit".toCharArray());
        }
        KeyManagerFactory kmf = KeyManagerFactory.getInstance(KeyManagerFactory.getDefaultAlgorithm());
        kmf.init(ks, "changeit".toCharArray());
        SSLContext tls = SSLContext.getInstance("TLS");
        tls.init(kmf.getKeyManagers(), null, null);
        return tls;
    }

    private static void keytool(String... args) throws Exception {
        boolean windows = System.getProperty("os.name").toLowerCase().contains("win");
        Path bin = Paths.get(System.getProperty("java.home"), "bin", windows ? "keytool.exe" : "keytool");
        List<String> cmd = new java.util.ArrayList<>(List.of(bin.toString()));
        cmd.addAll(List.of(args));
        Process p = new ProcessBuilder(cmd).redirectErrorStream(true).start();
        String out = new String(p.getInputStream().readAllBytes());
        assertTrue(p.waitFor(60, TimeUnit.SECONDS), "keytool hung");
        assertEquals(0, p.exitValue(), "keytool failed: " + out);
    }

    private TestLookupReporter.Builder reporter() {
        return reporterFor(server);
    }

    private static TestLookupReporter.Builder reporterFor(FakeTestLookupServer target) {
        return new TestLookupReporter.Builder()
            .baseUrl(target.baseUrl()).token("jwt-test").projectId("proj-1");
    }

    private static void openAndClose(TestLookupReporter r) throws Exception {
        try (TestLookupReporter.LiveSession s = r.startSession("b-1")) {
            s.record("t", TestLookupReporter.TestStatus.PASSED, 1);
        }
    }

    @Test
    @DisplayName("without TLS config the self-signed server is refused (the control)")
    void defaultTrustStoreRefusesSelfSigned() {
        assertThrows(TestLookupReporter.TestLookupException.class, () -> openAndClose(reporter().build()));
        assertTrue(server.sessionCreates.isEmpty());
    }

    @Test
    @DisplayName("caCertPath trusts the server's CA")
    void caCertPathTrustsTheCa() throws Exception {
        openAndClose(reporter().caCertPath(caPem.toString()).build());
        assertEquals(1, server.sessionCreates.size());
        assertEquals(List.of("t:PASSED"), server.results());
    }

    @Test
    @DisplayName("insecure skips verification, the hostname check included")
    void insecureSkipsVerification() throws Exception {
        openAndClose(reporterFor(otherHostServer).insecure(true).build());
        assertEquals(1, otherHostServer.sessionCreates.size());
    }

    @Test
    @DisplayName("caCertPath still checks the hostname")
    void caCertPathStillChecksTheHostname() {
        assertThrows(TestLookupReporter.TestLookupException.class,
            () -> openAndClose(reporterFor(otherHostServer).caCertPath(otherHostPem.toString()).build()));
        assertTrue(otherHostServer.sessionCreates.isEmpty());
    }

    @Test
    @DisplayName("-Dtestlookup.ca_cert_path reaches the reporter through ConfigLoader")
    void sysPropReachesTheReporter() throws Exception {
        try (TestConfigScope ignored = new TestConfigScope()
                .sysProps(Map.of("testlookup.ca_cert_path", caPem.toString()))) {
            openAndClose(reporter().build());
        }
        assertEquals(1, server.sessionCreates.size());
    }

    @Test
    @DisplayName("testlookup.insecure in testlookup.properties reaches the reporter")
    void propertiesFileReachesTheReporter() throws Exception {
        try (TestConfigScope ignored = new TestConfigScope()
                .classpathProperties(dir, Map.of("testlookup.insecure", "true"))) {
            openAndClose(reporter().build());
        }
        assertEquals(1, server.sessionCreates.size());
    }

    @Test
    @DisplayName("TESTLOOKUP_CA_CERT and TESTLOOKUP_INSECURE are mapped; TESTLOOKUP_UPLOAD_MODE is not")
    void envVarsAreMapped() {
        Map<String, Object> cfg = ConfigLoader.load(Map.of(
            "TESTLOOKUP_CA_CERT", "/etc/testlookup/ca.pem",
            "TESTLOOKUP_INSECURE", "TRUE",
            "TESTLOOKUP_UPLOAD_MODE", "offline"), new Properties());

        assertEquals("/etc/testlookup/ca.pem", ConfigLoader.getString(cfg, "auth.ca_cert_path", null));
        assertTrue(ConfigLoader.getFlag(cfg, "auth.insecure"));
        // Mapped for years and read by nothing; the docs promised an offline mode.
        assertFalse(cfg.containsKey("upload"));
    }
}
