package com.bazarbozorg.backtest.server.jobs;

import com.bazarbozorg.backtest.server.api.ListQuery;
import com.bazarbozorg.backtest.server.api.Page;
import java.sql.ResultSet;
import java.sql.SQLException;
import java.time.OffsetDateTime;
import java.util.List;
import java.util.Optional;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.jdbc.core.simple.JdbcClient;
import org.springframework.stereotype.Repository;
import org.springframework.transaction.annotation.Transactional;

/**
 * Every statement this service runs on the jobs table (R60, R67; schema.sql).
 *
 * <p>The table has one rule about who writes what, and this class is the web
 * side of it: <b>insert a job, and set {@code cancel_requested}. Nothing
 * else.</b> Status, result, worker, heartbeat and the timestamps belong to the
 * worker that claimed the job ({@code python/jobs/queue.py} is the other
 * side). A method added here that updated any of those would let the web side
 * overwrite a worker, which is the thing the table exists to prevent; step
 * 16.7 turns the rule into database roles.
 *
 * <p>An insert notifies the {@code jobs_new} channel with the engine's name, in
 * the same transaction, so a waiting worker claims at once rather than at its
 * next poll.
 */
@Repository
public class JobsRepository {
    public static final String CHANNEL = "jobs_new";
    public static final List<String> ENGINES = List.of("python", "java-compute");

    static final ListQuery.Spec LIST = new ListQuery.Spec("-id", "id")
            .sort("id", "id")
            .sort("type", "type")
            .sort("status", "status")
            .sort("engine", "engine")
            .sort("attempts", "attempts")
            .sort("createdAt", "created_at")
            .sort("finishedAt", "finished_at")
            .filter("type", "type", ListQuery.Kind.TEXT)
            .filter("status", "status", ListQuery.Kind.TEXT)
            .filter("engine", "engine", ListQuery.Kind.TEXT)
            .filter("transient", "transient", ListQuery.Kind.BOOLEAN)
            .filter("cancelRequested", "cancel_requested", ListQuery.Kind.BOOLEAN);

    private static final String COLUMNS = "id, type, engine, status, requested_by, worker_id, "
            + "attempts, cancel_requested, transient, inputs::text AS inputs, "
            + "result::text AS result, error, created_at, claimed_at, heartbeat_at, finished_at";

    private final JdbcClient db;
    private final String table;

    public JobsRepository(JdbcClient db, @Value("${jobs.table}") String table) {
        // The table name is configuration, never request input; checked anyway
        // because it is written into SQL.
        if (!table.matches("[a-z_]+\\.[a-z_]+")) {
            throw new IllegalArgumentException("jobs.table must be schema.table, got " + table);
        }
        this.db = db;
        this.table = table;
    }

    /** Insert a job for an engine and wake its workers. {@code inputsJson} is a JSON object. */
    @Transactional
    public Job insert(String type, String engine, String inputsJson, boolean isTransient) {
        if (!ENGINES.contains(engine)) {
            throw new IllegalArgumentException("unknown engine " + engine);
        }
        Job job = db.sql("INSERT INTO " + table + " (type, engine, inputs, transient) "
                        + "VALUES (?, ?, ?::jsonb, ?) RETURNING " + COLUMNS)
                .params(type, engine, inputsJson, isTransient)
                .query(JobsRepository::map).single();
        db.sql("SELECT pg_notify(?, ?)").params(CHANNEL, engine).query((rs, n) -> 1).single();
        return job;
    }

    public Optional<Job> find(long id) {
        return db.sql("SELECT " + COLUMNS + " FROM " + table + " WHERE id = ?")
                .param(id).query(JobsRepository::map).optional();
    }

    public Page<Job> list(ListQuery q) {
        long total = db.sql("SELECT count(*) FROM " + table + q.where())
                .params(q.filterParams()).query(Long.class).single();
        List<Job> items = db.sql("SELECT " + COLUMNS + " FROM " + table + q.where()
                        + q.orderBy() + q.paging())
                .params(q.pageParams()).query(JobsRepository::map).list();
        return q.page(items, total);
    }

    /**
     * Ask a waiting or running job to stop. True when the request was
     * recorded; false when the job is already over (or does not exist).
     */
    public boolean requestCancel(long id) {
        return db.sql("UPDATE " + table + " SET cancel_requested = TRUE "
                        + "WHERE id = ? AND status IN ('queued', 'running')")
                .param(id).update() == 1;
    }

    private static Job map(ResultSet rs, int row) throws SQLException {
        return new Job(
                rs.getLong("id"), rs.getString("type"), rs.getString("engine"),
                rs.getString("status"), rs.getString("requested_by"), rs.getString("worker_id"),
                rs.getInt("attempts"), rs.getBoolean("cancel_requested"),
                rs.getBoolean("transient"), rs.getString("inputs"), rs.getString("result"),
                rs.getString("error"),
                rs.getObject("created_at", OffsetDateTime.class),
                rs.getObject("claimed_at", OffsetDateTime.class),
                rs.getObject("heartbeat_at", OffsetDateTime.class),
                rs.getObject("finished_at", OffsetDateTime.class));
    }
}
