package com.bazarbozorg.backtest.jobs;

import com.bazarbozorg.backtest.data.DatabaseManager;

import java.sql.Connection;
import java.sql.PreparedStatement;
import java.sql.ResultSet;
import java.sql.SQLException;
import java.util.Optional;

/**
 * Every statement the engine runs on the jobs table (R67 a, R70; schema.sql).
 *
 * <p>The Java counterpart of {@code python/jobs/queue.py}, and it keeps the
 * same rules, because both workers share the one table:
 * <ul>
 *   <li><b>A job has one holder.</b> The claim picks its row with
 *       {@code FOR UPDATE SKIP LOCKED}; every later write names the worker and
 *       matches on it, so a worker that lost its claim changes nothing.</li>
 *   <li><b>Only its own engine claims a job</b>: {@value #ENGINE} here. The
 *       engine also <em>inserts</em> jobs for the Python worker
 *       ({@link PythonCalls}), which is the only way it reaches Python.</li>
 *   <li><b>Silence is the only reason a job is taken again</b>, up to
 *       {@value #MAX_ATTEMPTS} claims.</li>
 * </ul>
 *
 * <p>Each method is one short transaction on its own connection: the work
 * happens between calls, outside any lock.
 */
public final class JobQueue {

    public static final String ENGINE = "java-compute";
    public static final String PYTHON = "python";
    public static final String CHANNEL = "jobs_new";
    public static final int HEARTBEAT_S = 30;
    public static final int TIMEOUT_S = 120;
    public static final int MAX_ATTEMPTS = 3;

    /** A job row, as much of it as the engine reads. */
    public record Job(long id, String type, String status, String workerId, int attempts,
                      boolean cancelRequested, String inputs, String result, String error) {
        public boolean ended() {
            return status.equals("done") || status.equals("failed") || status.equals("cancelled");
        }
    }

    private static final String COLUMNS = "id, type, status, worker_id, attempts, "
            + "cancel_requested, inputs::text AS inputs, result::text AS result, error";

    private final DatabaseManager db;
    private final String table;

    public JobQueue(DatabaseManager db) {
        this(db, tableName());
    }

    public JobQueue(DatabaseManager db, String table) {
        // Configuration, never request input; checked anyway because it is
        // written into SQL.
        if (!table.matches("[a-z_]+\\.[a-z_]+")) {
            throw new IllegalArgumentException("the jobs table must be schema.table, got " + table);
        }
        this.db = db;
        this.table = table;
    }

    /** {@code $JOBS_TABLE}, so a test can point a worker at a copy of the table. */
    static String tableName() {
        String env = System.getenv("JOBS_TABLE");
        return env == null || env.isBlank() ? "jobs.jobs" : env;
    }

    /** Insert a job for an engine and wake its workers. */
    public Job insert(String type, String engine, String inputsJson, boolean isTransient) {
        try (Connection c = db.getConnection()) {
            c.setAutoCommit(false);
            try {
                Job job;
                try (PreparedStatement ps = c.prepareStatement("INSERT INTO " + table
                        + " (type, engine, inputs, transient) VALUES (?, ?, ?::jsonb, ?) "
                        + "RETURNING " + COLUMNS)) {
                    ps.setString(1, type);
                    ps.setString(2, engine);
                    ps.setString(3, inputsJson);
                    ps.setBoolean(4, isTransient);
                    try (ResultSet rs = ps.executeQuery()) {
                        rs.next();
                        job = map(rs);
                    }
                }
                try (PreparedStatement ps = c.prepareStatement("SELECT pg_notify(?, ?)")) {
                    ps.setString(1, CHANNEL);
                    ps.setString(2, engine);
                    ps.execute();
                }
                c.commit();
                return job;
            } finally {
                c.setAutoCommit(true);
            }
        } catch (SQLException e) {
            throw new IllegalStateException("could not insert a " + type + " job", e);
        }
    }

    public Optional<Job> find(long id) {
        try (Connection c = db.getConnection();
             PreparedStatement ps = c.prepareStatement(
                     "SELECT " + COLUMNS + " FROM " + table + " WHERE id = ?")) {
            ps.setLong(1, id);
            try (ResultSet rs = ps.executeQuery()) {
                return rs.next() ? Optional.of(map(rs)) : Optional.empty();
            }
        } catch (SQLException e) {
            throw new IllegalStateException("could not read job " + id, e);
        }
    }

    /** Ask a waiting or running job to stop. */
    public void requestCancel(long id) {
        try (Connection c = db.getConnection();
             PreparedStatement ps = c.prepareStatement("UPDATE " + table
                     + " SET cancel_requested = TRUE WHERE id = ? "
                     + "AND status IN ('queued', 'running')")) {
            ps.setLong(1, id);
            ps.executeUpdate();
        } catch (SQLException e) {
            throw new IllegalStateException("could not ask job " + id + " to stop", e);
        }
    }

    /**
     * Take the oldest job this engine may run, or empty. Queued jobs, and
     * running ones gone silent, are both up for it. A queued job that was
     * asked to stop before anyone took it is marked cancelled first.
     */
    public Optional<Job> claim(String workerId) {
        try (Connection c = db.getConnection()) {
            try (PreparedStatement ps = c.prepareStatement("UPDATE " + table
                    + " SET status = 'cancelled', finished_at = now() "
                    + "WHERE engine = ? AND cancel_requested AND status = 'queued'")) {
                ps.setString(1, ENGINE);
                ps.executeUpdate();
            }
            try (PreparedStatement ps = c.prepareStatement("UPDATE " + table
                    + " SET status = 'running', worker_id = ?, attempts = attempts + 1, "
                    + "claimed_at = now(), heartbeat_at = now() "
                    + "WHERE id = (SELECT id FROM " + table
                    + " WHERE engine = ? AND NOT cancel_requested AND attempts < ? "
                    + "AND (status = 'queued' OR (status = 'running' "
                    + "AND heartbeat_at < now() - make_interval(secs => ?))) "
                    + "ORDER BY id FOR UPDATE SKIP LOCKED LIMIT 1) RETURNING " + COLUMNS)) {
                ps.setString(1, workerId);
                ps.setString(2, ENGINE);
                ps.setInt(3, MAX_ATTEMPTS);
                ps.setInt(4, TIMEOUT_S);
                try (ResultSet rs = ps.executeQuery()) {
                    return rs.next() ? Optional.of(map(rs)) : Optional.empty();
                }
            }
        } catch (SQLException e) {
            throw new IllegalStateException("could not claim a job", e);
        }
    }

    /** Say the job is alive. False when the claim is no longer this worker's. */
    public boolean beat(long id, String workerId) {
        try (Connection c = db.getConnection();
             PreparedStatement ps = c.prepareStatement("UPDATE " + table
                     + " SET heartbeat_at = now() "
                     + "WHERE id = ? AND worker_id = ? AND status = 'running'")) {
            ps.setLong(1, id);
            ps.setString(2, workerId);
            return ps.executeUpdate() == 1;
        } catch (SQLException e) {
            throw new IllegalStateException("could not beat job " + id, e);
        }
    }

    /** End a job this worker holds. False when it no longer holds it. */
    public boolean finish(long id, String workerId, String status, String resultJson,
                          String error) {
        if (!status.equals("done") && !status.equals("failed")) {
            throw new IllegalArgumentException("not a final status: " + status);
        }
        try (Connection c = db.getConnection();
             PreparedStatement ps = c.prepareStatement("UPDATE " + table
                     + " SET status = ?, finished_at = now(), heartbeat_at = now(), "
                     + "result = coalesce(?::jsonb, result), error = ? "
                     + "WHERE id = ? AND worker_id = ? AND status = 'running'")) {
            ps.setString(1, status);
            ps.setString(2, resultJson);
            ps.setString(3, error);
            ps.setLong(4, id);
            ps.setString(5, workerId);
            return ps.executeUpdate() == 1;
        } catch (SQLException e) {
            throw new IllegalStateException("could not finish job " + id, e);
        }
    }

    private static Job map(ResultSet rs) throws SQLException {
        return new Job(rs.getLong("id"), rs.getString("type"), rs.getString("status"),
                rs.getString("worker_id"), rs.getInt("attempts"),
                rs.getBoolean("cancel_requested"), rs.getString("inputs"),
                rs.getString("result"), rs.getString("error"));
    }
}
