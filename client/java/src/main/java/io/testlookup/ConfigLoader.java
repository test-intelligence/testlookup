package io.testlookup;

import com.fasterxml.jackson.databind.ObjectMapper;
import com.fasterxml.jackson.dataformat.yaml.YAMLFactory;

import java.io.File;
import java.io.IOException;
import java.nio.file.Path;
import java.nio.file.Paths;
import java.util.Collections;
import java.util.HashMap;
import java.util.Map;
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

    private static final String[] SEARCH_PATHS = {
        "testlookup.yaml",
        ".testlookup" + File.separator + "config.yaml",
    };

    /** Maps environment variable names to dot-path config keys. */
    private static final String[][] ENV_MAP = {
        {"TESTLOOKUP_URL",         "server.url"},
        {"TESTLOOKUP_TOKEN",       "auth.token"},
        {"TESTLOOKUP_API_KEY",     "auth.api_key"},
        {"TESTLOOKUP_PROJECT_ID",  "project.id"},
        {"TESTLOOKUP_BUILD",       "ci.build_number"},
        {"TESTLOOKUP_BRANCH",      "ci.branch"},
        {"TESTLOOKUP_COMMIT",      "ci.commit_hash"},
        {"TESTLOOKUP_UPLOAD_MODE", "upload.mode"},
    };

    /** Maps JVM system property names to dot-path config keys. */
    private static final String[][] SYSPROP_MAP = {
        {"testlookup.url",        "server.url"},
        {"testlookup.token",      "auth.token"},
        {"testlookup.apiKey",     "auth.api_key"},
        {"testlookup.api-key",    "auth.api_key"},   // kebab-case alias
        {"testlookup.projectId",  "project.id"},
        {"testlookup.build",      "ci.build_number"},
        {"testlookup.branch",     "ci.branch"},
        {"testlookup.commit",     "ci.commit_hash"},
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

        // 1. Load from config file
        File configFile = findConfigFile();
        if (configFile != null) {
            config = parseYaml(configFile);
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
        // Check config file
        return findConfigFile() != null;
    }

    // ── Internal ────────────────────────────────────────────────────���────────

    static File findConfigFile() {
        // Project-level paths (relative to cwd)
        for (String path : SEARCH_PATHS) {
            File f = new File(path);
            if (f.isFile() && f.canRead()) return f;
        }
        // User home path
        Path homeCfg = Paths.get(System.getProperty("user.home"), ".testlookup", "config.yaml");
        File homeFile = homeCfg.toFile();
        if (homeFile.isFile() && homeFile.canRead()) return homeFile;
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
}
