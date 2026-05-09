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

    public String getDbUrl() {
        return dbUrl;
    }

    public String getDbUser() {
        return dbUser;
    }

    public String getDbPassword() {
        return dbPassword;
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
