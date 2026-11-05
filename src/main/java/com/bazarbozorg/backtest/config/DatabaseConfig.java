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

        // Who this process is, for the audit columns. schema.sql's
        // audit_actor() reads app.actor and falls back to the database role,
        // so without this every row the engine or CLI writes is attributed to
        // the login rather than to the job -- the design wants the job.
        //
        // connectionInitSql runs once per physical connection, which is where
        // a session setting belongs: Hikari hands the same backend out
        // repeatedly, so a per-query SET would be pure overhead.
        //
        // The value is quoted with quote_literal rather than interpolated,
        // because DB_ACTOR comes from the environment.
        hikariConfig.setConnectionInitSql(
                "SELECT set_config('app.actor', " + quoteLiteral(dbActor()) + ", false)");

        this.dataSource = new HikariDataSource(hikariConfig);
        logger.info("Configured HikariCP PostgreSQL DataSource: url={}, maxPoolSize={}",
                config.getDbUrl(), config.getDbPoolMaxSize());
    }

    /**
     * This process's name for the audit columns, overridable per invocation
     * with $DB_ACTOR (e.g. DB_ACTOR=engine:train) so a row traces to the task.
     */
    private static String dbActor() {
        String actor = System.getenv("DB_ACTOR");
        return (actor == null || actor.isBlank()) ? "engine" : actor;
    }

    /** Single-quotes a string for inlining into SQL, doubling any quote in it. */
    private static String quoteLiteral(String value) {
        return "'" + value.replace("'", "''") + "'";
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
