package io.testlookup;

import java.io.IOException;
import java.io.OutputStream;
import java.net.URL;
import java.net.URLClassLoader;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.ArrayList;
import java.util.List;
import java.util.Map;
import java.util.Properties;

/**
 * Scoped TestLookup configuration for a test: JVM system properties, plus an
 * optional {@code testlookup.properties} served from the context classloader
 * (the way {@code src/test/resources/testlookup.properties} reaches the SDK in
 * a real project). Everything is undone on close.
 */
public final class TestConfigScope implements AutoCloseable {

    private final List<String> setProps = new ArrayList<>();
    private final ClassLoader previousLoader = Thread.currentThread().getContextClassLoader();
    private URLClassLoader loader;

    public TestConfigScope sysProps(Map<String, String> props) {
        for (Map.Entry<String, String> e : props.entrySet()) {
            System.setProperty(e.getKey(), e.getValue());
            setProps.add(e.getKey());
        }
        return this;
    }

    /** Serve these keys as the classpath {@code testlookup.properties}. */
    public TestConfigScope classpathProperties(Path dir, Map<String, String> keys) throws IOException {
        Properties p = new Properties();
        p.putAll(keys);
        try (OutputStream out = Files.newOutputStream(dir.resolve("testlookup.properties"))) {
            p.store(out, null);
        }
        loader = new URLClassLoader(new URL[] { dir.toUri().toURL() }, previousLoader);
        Thread.currentThread().setContextClassLoader(loader);
        return this;
    }

    @Override
    public void close() throws IOException {
        for (String k : setProps) System.clearProperty(k);
        Thread.currentThread().setContextClassLoader(previousLoader);
        if (loader != null) loader.close();
    }
}
