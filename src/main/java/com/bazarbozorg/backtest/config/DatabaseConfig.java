package com.bazarbozorg.backtest.config;

import com.zaxxer.hikari.HikariConfig;
import com.zaxxer.hikari.HikariDataSource;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;

import javax.sql.DataSource;
import java.sql.Connection;
import java.sql.SQLException;

public class DatabaseConfig {

    private static final Logger logger = LoggerFactory.getLogger(DatabaseConfig.class);

    private final HikariDataSource dataSource;

    public DatabaseConfig() {
        AppConfig config = AppConfig.getInstance();

        HikariConfig hikariConfig = new HikariConfig();
        hikariConfig.setJdbcUrl(config.getDbUrl());
        hikariConfig.setUsername(config.getDbUser());
        hikariConfig.setPassword(config.getDbPassword());
        hikariConfig.setMaximumPoolSize(config.getDbPoolMaxSize());
        hikariConfig.setMinimumIdle(config.getDbPoolMinIdle());
        hikariConfig.setConnectionTimeout(config.getDbPoolConnectionTimeoutMs());
        hikariConfig.setPoolName("backtest-pool");
        hikariConfig.addDataSourceProperty("reWriteBatchedInserts", "true");

        this.dataSource = new HikariDataSource(hikariConfig);
        logger.info("Configured HikariCP PostgreSQL DataSource: url={}, maxPoolSize={}",
                config.getDbUrl(), config.getDbPoolMaxSize());
    }

    public Connection getConnection() throws SQLException {
        return dataSource.getConnection();
    }

    public DataSource getDataSource() {
        return dataSource;
    }

    public void close() {
        if (dataSource != null && !dataSource.isClosed()) {
            dataSource.close();
            logger.info("HikariCP DataSource closed");
        }
    }
}
