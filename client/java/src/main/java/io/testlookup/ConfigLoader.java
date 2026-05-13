package io.testlookup;

import com.fasterxml.jackson.databind.ObjectMapper;
import com.fasterxml.jackson.dataformat.yaml.YAMLFactory;

import java.io.File;
import java.io.FileInputStream;
import java.io.IOException;
import java.io.InputStream;
import java.nio.file.Path;
import java.nio.file.Paths;
import java.util.Collections;
import java.util.HashMap;
import java.util.Map;
import java.util.Properties;
import java.util.logging.Level;
import java.util.logging.Logger;

/**
 * Discovers and loads testlookup.yaml configuration, overlaying environment
 * variables and JVM system properties.
 *
 * <p>Discovery order (first file found wins):
 * <ol>
 *   <li>{@code ./testlookup.yaml}</li>
 *   <li>{@code ./.testlookup/config.yaml}</li>
 *   <li>{@code ~/.testlookup/config.yaml}</li>
 * </ol>
 *
 * <p>Precedence (highest wins):
 * Builder args &gt; JVM system props &gt; Environment vars &gt; Config file &gt; Defaults
 */
public final class ConfigLoader {

    private static final Logger LOG = Logger.getLogger(ConfigLoader.class.getName());

    /** Properties-file search paths (preferred — ReportPortal-compatible). */
    private static final String[] PROPERTIES_PATHS = {
        "testlookup.properties",
        ".testlookup" + File.separator + "testlookup.properties",
    };

    /** YAML search paths (legacy — kept working for backwards compatibility). */
    private static final String[] YAML_PATHS = {
        "testlookup.yaml",
        ".testlookup" + File.separator + "config.yaml",
    };

    /** Combined list — properties first so it wins when both exist. */
    private static final String[] SEARCH_PATHS;
    static {
        SEARCH_PATHS = new String[PROPERTIES_PATHS.length + YAML_PATHS.length];
        System.arraycopy(PROPERTIES_PATHS, 0, SEARCH_PATHS, 0, PROPERTIES_PATHS.length);
        System.arraycopy(YAML_PATHS, 0, SEARCH_PATHS, PROPERTIES_PATHS.length, YAML_PATHS.length);
    }

    /** Maps environment variable names to dot-path config keys. */
    private static final String[][] ENV_MAP = {
        // Legacy names — kept working forever.
        {"TESTLOOKUP_URL",         "server.url"},
        {"TESTLOOKUP_TOKEN",       "auth.token"},
        {"TESTLOOKUP_API_KEY",     "auth.api_key"},
        {"TESTLOOKUP_PROJECT_ID",  "project.id"},
        {"TESTLOOKUP_BUILD",       "ci.build_number"},
        {"TESTLOOKUP_BRANCH",      "ci.branch"},
        {"TESTLOOKUP_COMMIT",      "ci.commit_hash"},
        {"TESTLOOKUP_UPLOAD_MODE", "upload.mode"},
        // Canonical names matching the ReportPortal rp.* convention.
        {"TESTLOOKUP_ENDPOINT",    "server.url"},
        {"TESTLOOKUP_PROJECT",     "project.id"},
        {"TESTLOOKUP_LAUNCH",      "reporting.launch_name"},
        {"TESTLOOKUP_SUITE",       "reporting.suite_name"},
        {"TESTLOOKUP_RELEASE",     "reporting.release_name"},
        {"TESTLOOKUP_FRAMEWORK",   "reporting.framework"},
    };

    /** Maps JVM system property names to dot-path config keys. */
    private static final String[][] SYSPROP_MAP = {
        {"testlookup.url",        "server.url"},
        {"testlookup.endpoint",   "server.url"},
        {"testlookup.token",      "auth.token"},
        {"testlookup.apiKey",     "auth.api_key"},
        {"testlookup.api-key",    "auth.api_key"},   // kebab-case alias
        {"testlookup.api.key",    "auth.api_key"},   // dotted (canonical)
        {"testlookup.projectId",  "project.id"},
        {"testlookup.project",    "project.id"},
        {"testlookup.launch",     "reporting.launch_name"},
        {"testlookup.suite",      "reporting.suite_name"},
        {"testlookup.release",    "reporting.release_name"},
        {"testlookup.build",      "ci.build_number"},
        {"testlookup.branch",     "ci.branch"},
        {"testlookup.commit",     "ci.commit_hash"},
        {"testlookup.framework",  "reporting.framework"},
    };

    /**
     * Maps canonical dotted properties keys to nested config dot-paths.
     * Used by {@link #parseProperties(File)} and {@link #parsePropertiesStream}.
     */
    private static final String[][] PROPERTIES_MAP = {
        {"testlookup.endpoint",   "server.url"},
        {"testlookup.url",        "server.url"},
        {"testlookup.token",      "auth.token"},
        {"testlookup.api.key",    "auth.api_key"},
        {"testlookup.api_key",    "auth.api_key"},
        {"testlookup.apiKey",     "auth.api_key"},
        {"testlookup.project",    "project.id"},
        {"testlookup.launch",     "reporting.launch_name"},
        {"testlookup.suite",      "reporting.suite_name"},
        {"testlookup.release",    "reporting.release_name"},
        {"testlookup.build",      "ci.build_number"},
        {"testlookup.branch",     "ci.branch"},
        {"testlookup.commit",     "ci.commit_hash"},
        {"testlookup.framework",  "reporting.framework"},
    };

    private ConfigLoader() { }

    /**
     * Load config from file + env vars + system properties.
     *
     * @return nested Map representing the merged config (never null)
     */
    @SuppressWarnings("unchecked")
    public static Map<String, Object> load() {
        Map<String, Object> config = new HashMap<>();

        // 1a. Try classpath testlookup.properties (Java convention:
        // src/test/resources/testlookup.properties lands at /testlookup.properties).
        // Lowest precedence among the file sources.
        Map<String, Object> classpathCfg = parsePropertiesFromClasspath("testlookup.properties");
        if (!classpathCfg.isEmpty()) {
            config = classpathCfg;
        }

        // 1b. Filesystem config file (cwd / .testlookup / ~/.testlookup).
        // Overrides the classpath file when both exist.
        File configFile = findConfigFile();
        if (configFile != null) {
            Map<String, Object> fileCfg = configFile.getName().endsWith(".properties")
                ? parseProperties(configFile)
                : parseYaml(configFile);
            mergeInto(config, fileCfg);
        }

        // 2. Overlay environment variables
        for (String[] mapping : ENV_MAP) {
            String val = System.getenv(mapping[0]);
            if (val != null && !val.isEmpty()) {
                setNested(config, mapping[1], val);
            }
        }

        // 3. Overlay JVM system properties (higher precedence than env vars)
        for (String[] mapping : SYSPROP_MAP) {
            String val = System.getProperty(mapping[0]);
            if (val != null && !val.isEmpty()) {
                setNested(config, mapping[1], val);
            }
        }

        return config;
    }

    /**
     * Get a nested string value by dot-path (e.g. "server.url").
     *
     * @return the value, or {@code defaultVal} if missing or empty
     */
    @SuppressWarnings("unchecked")
    public static String getString(Map<String, Object> config, String dotPath, String defaultVal) {
        String[] parts = dotPath.split("\\.");
        Object cur = config;
        for (String p : parts) {
            if (cur instanceof Map) {
                cur = ((Map<String, Object>) cur).get(p);
            } else {
                return defaultVal;
            }
            if (cur == null) return defaultVal;
        }
        String s = cur.toString();
        return s.isEmpty() ? defaultVal : s;
    }

    /**
     * Get a nested int value by dot-path.
     *
     * @return the value, or {@code defaultVal} if missing or not a number
     */
    public static int getInt(Map<String, Object> config, String dotPath, int defaultVal) {
        String s = getString(config, dotPath, null);
        if (s == null) return defaultVal;
        try {
            return Integer.parseInt(s);
        } catch (NumberFormatException e) {
            return defaultVal;
        }
    }

    /**
     * Check if any configuration source is available (file, env vars, or system properties).
     *
     * @return true if at least one config value is resolvable
     */
    public static boolean isConfigured() {
        // Check system properties
        for (String[] mapping : SYSPROP_MAP) {
            String val = System.getProperty(mapping[0]);
            if (val != null && !val.isEmpty()) return true;
        }
        // Check environment variables
        for (String[] mapping : ENV_MAP) {
            String val = System.getenv(mapping[0]);
            if (val != null && !val.isEmpty()) return true;
        }
        // Check config file (filesystem)
        if (findConfigFile() != null) return true;
        // Check classpath testlookup.properties
        ClassLoader cl = Thread.currentThread().getContextClassLoader();
        if (cl != null && cl.getResource("testlookup.properties") != null) return true;
        return false;
    }

    // ── Internal ────────────────────────────────────────────────────���────────

    static File findConfigFile() {
        // Project-level paths (relative to cwd) — properties first, then YAML.
        for (String path : SEARCH_PATHS) {
            File f = new File(path);
            if (f.isFile() && f.canRead()) return f;
        }
        // User home paths — properties first, then YAML.
        Path home = Paths.get(System.getProperty("user.home"), ".testlookup");
        for (String name : new String[] {"testlookup.properties", "config.yaml"}) {
            File homeFile = home.resolve(name).toFile();
            if (homeFile.isFile() && homeFile.canRead()) return homeFile;
        }
        return null;
    }

    @SuppressWarnings("unchecked")
    private static Map<String, Object> parseYaml(File file) {
        try {
            ObjectMapper yamlMapper = new ObjectMapper(new YAMLFactory());
            Map<String, Object> data = yamlMapper.readValue(file, Map.class);
            return data != null ? data : new HashMap<>();
        } catch (IOException e) {
            LOG.log(Level.WARNING, "Failed to parse config file " + file + ": " + e.getMessage());
            return new HashMap<>();
        } catch (NoClassDefFoundError e) {
            // jackson-dataformat-yaml not on classpath — degrade gracefully
            LOG.log(Level.FINE, "YAML parser not available, skipping config file");
            return new HashMap<>();
        }
    }

    @SuppressWarnings("unchecked")
    private static void setNested(Map<String, Object> config, String dotPath, String value) {
        String[] parts = dotPath.split("\\.");
        Map<String, Object> cur = config;
        for (int i = 0; i < parts.length - 1; i++) {
            cur = (Map<String, Object>) cur.computeIfAbsent(parts[i], k -> new HashMap<>());
        }
        cur.put(parts[parts.length - 1], value);
    }

    private static Map<String, Object> parseProperties(File file) {
        try (InputStream in = new FileInputStream(file)) {
            return parsePropertiesStream(in);
        } catch (IOException e) {
            LOG.log(Level.WARNING, "Failed to parse properties file " + file + ": " + e.getMessage());
            return new HashMap<>();
        }
    }

    private static Map<String, Object> parsePropertiesFromClasspath(String resourceName) {
        ClassLoader cl = Thread.currentThread().getContextClassLoader();
        if (cl == null) return new HashMap<>();
        try (InputStream in = cl.getResourceAsStream(resourceName)) {
            if (in == null) return new HashMap<>();
            return parsePropertiesStream(in);
        } catch (IOException e) {
            LOG.log(Level.WARNING, "Failed to read classpath " + resourceName + ": " + e.getMessage());
            return new HashMap<>();
        }
    }

    private static Map<String, Object> parsePropertiesStream(InputStream in) throws IOException {
        Properties props = new Properties();
        props.load(in);
        Map<String, Object> config = new HashMap<>();
        for (String[] mapping : PROPERTIES_MAP) {
            String val = props.getProperty(mapping[0]);
            if (val != null && !val.isEmpty()) {
                setNested(config, mapping[1], val);
            }
        }
        // Also accept any unmapped key for forward-compat: leave in flat form.
        for (String key : props.stringPropertyNames()) {
            boolean known = false;
            for (String[] m : PROPERTIES_MAP) {
                if (m[0].equals(key)) { known = true; break; }
            }
            if (!known && key.startsWith("testlookup.")) {
                LOG.log(Level.FINE, "Unknown testlookup.properties key '" + key + "' — ignored");
            }
        }
        return config;
    }

    @SuppressWarnings("unchecked")
    private static void mergeInto(Map<String, Object> base, Map<String, Object> overlay) {
        for (Map.Entry<String, Object> e : overlay.entrySet()) {
            Object overlayVal = e.getValue();
            Object baseVal = base.get(e.getKey());
            if (overlayVal instanceof Map && baseVal instanceof Map) {
                mergeInto((Map<String, Object>) baseVal, (Map<String, Object>) overlayVal);
            } else if (overlayVal != null) {
                base.put(e.getKey(), overlayVal);
            }
        }
    }
}
