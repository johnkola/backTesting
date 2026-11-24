package com.bazarbozorg.backtest.server;

import com.google.gson.JsonElement;
import com.google.gson.JsonObject;
import com.google.gson.JsonParser;
import java.net.URI;
import java.net.http.HttpClient;
import java.net.http.HttpRequest;
import java.net.http.HttpResponse;
import java.sql.Connection;
import java.sql.DriverManager;
import java.sql.PreparedStatement;
import java.sql.ResultSet;
import java.sql.SQLException;
import java.sql.Statement;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.AfterAll;
import org.junit.jupiter.api.Assumptions;
import org.junit.jupiter.api.BeforeAll;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.boot.test.context.SpringBootTest;

/**
 * The service started for real on a free port, called over HTTP, against the
 * real database. Skipped when Postgres is not reachable.
 *
 * <p>The jobs table is a copy ({@value #TABLE}): a job put in the real one
 * would be claimed by the worker that is running. Each test starts with the
 * copy empty; where a test needs a worker it plays one with plain SQL.
 */
@SpringBootTest(webEnvironment = SpringBootTest.WebEnvironment.RANDOM_PORT,
        properties = {"jobs.table=" + ServiceTest.TABLE,
                // The test classes' services can be up together; see application.properties.
                "spring.datasource.hikari.maximum-pool-size=2",
                // Gradle runs the tests from server/: the docs are one level up.
                "docs.dir=..", "models.dir=" + ServiceTest.MODELS_DIR})
public abstract class ServiceTest {
    public static final String TABLE = "jobs.jobs_test_server";
    /** A models directory of the tests' own; the real one is not read. */
    public static final String MODELS_DIR = "build/test-models";
    private static final HttpClient HTTP = HttpClient.newHttpClient();

    @Value("${local.server.port}")
    protected int port;

    public record Reply(int status, String text, HttpResponse<String> raw) {
        public JsonObject json() {
            return JsonParser.parseString(text).getAsJsonObject();
        }

        public JsonElement at(String field) {
            return json().get(field);
        }
    }

    static String env(String name, String fallback) {
        String v = System.getenv(name);
        return v == null || v.isBlank() ? fallback : v;
    }

    protected static Connection connect() throws SQLException {
        return DriverManager.getConnection(
                "jdbc:postgresql://" + env("PGHOST", "localhost") + ":" + env("PGPORT", "5432")
                        + "/" + env("PGDATABASE", "backtest"),
                env("PGUSER", "backtest"), env("PGPASSWORD", "backtest"));
    }

    /**
     * Runs before the service is started for the class: skip it all when there
     * is no database, and otherwise rebuild the copy, so it always has the
     * real table's columns.
     */
    @BeforeAll
    static void copyOfTheJobsTable() {
        DriverManager.setLoginTimeout(3);
        try (Connection c = connect(); Statement s = c.createStatement()) {
            s.execute("DROP TABLE IF EXISTS " + TABLE);
            s.execute("CREATE TABLE " + TABLE + " (LIKE jobs.jobs INCLUDING ALL)");
        } catch (SQLException e) {
            Assumptions.abort("Postgres is not reachable: " + e.getMessage());
        }
    }

    @AfterAll
    static void dropTheCopy() {
        try (Connection c = connect(); Statement s = c.createStatement()) {
            s.execute("DROP TABLE IF EXISTS " + TABLE);
        } catch (SQLException e) {
            // Nothing was created, so there is nothing to drop.
        }
    }

    @BeforeEach
    void emptyTable() throws SQLException {
        sql("TRUNCATE " + TABLE + " RESTART IDENTITY");
    }

    protected static void sql(String statement, Object... params) throws SQLException {
        try (Connection c = connect(); PreparedStatement p = c.prepareStatement(statement)) {
            for (int i = 0; i < params.length; i++) {
                p.setObject(i + 1, params[i]);
            }
            p.execute();
        }
    }

    protected static String scalar(String query, Object... params) throws SQLException {
        try (Connection c = connect(); PreparedStatement p = c.prepareStatement(query)) {
            for (int i = 0; i < params.length; i++) {
                p.setObject(i + 1, params[i]);
            }
            try (ResultSet rs = p.executeQuery()) {
                return rs.next() ? rs.getString(1) : null;
            }
        }
    }

    /** Put a job in the copy as the web side would, in a given state. Returns its id. */
    protected static long job(String type, String status) throws SQLException {
        return Long.parseLong(scalar("INSERT INTO " + TABLE + " (type, engine, status, inputs) "
                + "VALUES (?, 'python', ?, '{\"n\": 1}'::jsonb) RETURNING id", type, status));
    }

    protected Reply call(String method, String path) throws Exception {
        return send(HttpRequest.newBuilder(URI.create("http://localhost:" + port + path))
                .method(method, HttpRequest.BodyPublishers.noBody()));
    }

    protected Reply get(String path) throws Exception {
        return call("GET", path);
    }

    protected Reply send(HttpRequest.Builder request) throws Exception {
        HttpResponse<String> r = HTTP.send(request.build(), HttpResponse.BodyHandlers.ofString());
        return new Reply(r.statusCode(), r.body(), r);
    }
}
