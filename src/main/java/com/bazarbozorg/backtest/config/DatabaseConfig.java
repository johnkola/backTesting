package com.bazarbozorg.backtest.config;

import org.h2.jdbcx.JdbcDataSource;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;

import javax.sql.DataSource;
import java.sql.Connection;
import java.sql.SQLException;

public class DatabaseConfig {

    private static final Logger logger = LoggerFactory.getLogger(DatabaseConfig.class);

    private final JdbcDataSource dataSource;

    public DatabaseConfig() {
        AppConfig config = AppConfig.getInstance();
        this.dataSource = new JdbcDataSource();
        this.dataSource.setURL(config.getDbUrl());
        this.dataSource.setUser(config.getDbUser());
        this.dataSource.setPassword(config.getDbPassword());
        logger.info("Configured H2 DataSource with URL: {}", config.getDbUrl());
    }

    public Connection getConnection() throws SQLException {
        return dataSource.getConnection();
    }

    public DataSource getDataSource() {
        return dataSource;
    }
}
