package com.bazarbozorg.backtest.config;

import org.slf4j.Logger;
import org.slf4j.LoggerFactory;

import java.io.IOException;
import java.io.InputStream;
import java.util.Properties;

public class AppConfig {

    private static final Logger logger = LoggerFactory.getLogger(AppConfig.class);
    private static final String CONFIG_FILE = "application.properties";

    private static volatile AppConfig instance;

    private final String dbUrl;
    private final String dbUser;
    private final String dbPassword;
    private final int dbPoolMaxSize;
    private final int dbPoolMinIdle;
    private final long dbPoolConnectionTimeoutMs;
    private final double defaultInitialCapital;
    private final String defaultCommissionType;
    private final double defaultCommissionValue;
    private final String defaultSlippageType;
    private final double defaultSlippageValue;

    private AppConfig() {
        Properties props = new Properties();
        try (InputStream is = getClass().getClassLoader().getResourceAsStream(CONFIG_FILE)) {
            if (is != null) {
                props.load(is);
                logger.info("Loaded configuration from {}", CONFIG_FILE);
            } else {
                logger.warn("Configuration file {} not found on classpath, using defaults", CONFIG_FILE);
            }
        } catch (IOException e) {
            logger.error("Error loading configuration file {}, using defaults", CONFIG_FILE, e);
        }

        this.dbUrl = props.getProperty("db.url", "jdbc:postgresql://localhost:5432/backtest");
        this.dbUser = props.getProperty("db.user", "backtest");
        this.dbPassword = props.getProperty("db.password", "backtest");
        this.dbPoolMaxSize = Integer.parseInt(props.getProperty("db.pool.maxSize", "10"));
        this.dbPoolMinIdle = Integer.parseInt(props.getProperty("db.pool.minIdle", "2"));
        this.dbPoolConnectionTimeoutMs = Long.parseLong(
                props.getProperty("db.pool.connectionTimeoutMs", "10000"));
        this.defaultInitialCapital = Double.parseDouble(
                props.getProperty("default.initial.capital", "10000.0"));
        this.defaultCommissionType = props.getProperty("default.commission.type", "percentage");
        this.defaultCommissionValue = Double.parseDouble(
                props.getProperty("default.commission.value", "0.001"));
        this.defaultSlippageType = props.getProperty("default.slippage.type", "percentage");
        this.defaultSlippageValue = Double.parseDouble(
                props.getProperty("default.slippage.value", "0.0005"));
    }

    public static AppConfig getInstance() {
        if (instance == null) {
            synchronized (AppConfig.class) {
                if (instance == null) {
                    instance = new AppConfig();
                }
            }
        }
        return instance;
    }

    /**
     * JDBC URL, with {@code PG*} environment variables taking precedence over
     * {@code application.properties} when they are set.
     *
     * <p>The properties file hard-codes {@code localhost:5432}, which is right
     * for a developer running the CLI and wrong for anything inside a
     * container, where Postgres answers on the compose service name. The Node
     * server has always resolved this the same way ({@code db.js} prefers env
     * vars when set); Java did not, so the engine container could not reach the
     * database at all until it did.
     *
     * <p>{@code PGHOST} alone is enough — the rest fall back to the values in
     * the properties file, then to Postgres's own conventional defaults.
     */
    public String getDbUrl() {
        String host = env("PGHOST");
        if (host == null) {
            return dbUrl;
        }
        String port = firstNonBlank(env("PGPORT"), "5432");
        String database = firstNonBlank(env("PGDATABASE"), "backtest");
        return "jdbc:postgresql://" + host + ":" + port + "/" + database;
    }

    public String getDbUser() {
        return firstNonBlank(env("PGUSER"), dbUser);
    }

    public String getDbPassword() {
        return firstNonBlank(env("PGPASSWORD"), dbPassword);
    }

    /** Environment variable, or null when unset or blank. */
    private static String env(String name) {
        String value = System.getenv(name);
        return value != null && !value.isBlank() ? value : null;
    }

    private static String firstNonBlank(String preferred, String fallback) {
        return preferred != null && !preferred.isBlank() ? preferred : fallback;
    }

    public int getDbPoolMaxSize() {
        return dbPoolMaxSize;
    }

    public int getDbPoolMinIdle() {
        return dbPoolMinIdle;
    }

    public long getDbPoolConnectionTimeoutMs() {
        return dbPoolConnectionTimeoutMs;
    }

    public double getDefaultInitialCapital() {
        return defaultInitialCapital;
    }

    public String getDefaultCommissionType() {
        return defaultCommissionType;
    }

    public double getDefaultCommissionValue() {
        return defaultCommissionValue;
    }

    public String getDefaultSlippageType() {
        return defaultSlippageType;
    }

    public double getDefaultSlippageValue() {
        return defaultSlippageValue;
    }
}
