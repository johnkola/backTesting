package com.bazarbozorg.backtest.server.imports;

import com.bazarbozorg.backtest.server.api.ListQuery;
import com.bazarbozorg.backtest.server.api.Page;
import com.bazarbozorg.backtest.server.api.Wire;
import com.bazarbozorg.backtest.server.python.PythonGateway;
import java.io.IOException;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.http.HttpStatus;
import org.springframework.http.ResponseEntity;
import org.springframework.jdbc.core.simple.JdbcClient;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestParam;
import org.springframework.web.bind.annotation.RestController;
import org.springframework.web.multipart.MultipartFile;
import tools.jackson.databind.JsonNode;
import tools.jackson.databind.ObjectMapper;

/**
 * Imports: the upload, the audit log of what was imported, and the version
 * history of each series' input file.
 *
 * <pre>
 *   POST /api/imports        multipart: file, symbol, type, timeframe[, source, force, aggregate_to, override_reason]
 *   GET  /api/imports        the audit log, one row per year slice; ListQuery
 *   GET  /api/import-files   one row per distinct file per series, newest first; ListQuery
 * </pre>
 *
 * <p><b>The upload is spooled, then imported by the worker.</b> This service
 * writes the file to {@code uploads.dir}, a directory the Python worker mounts
 * at the same path, and inserts an {@code import-upload} job that names it.
 * The form is passed as typed and checked on the Python side: which
 * timeframes and instrument types exist, and what a valid roll-up target is,
 * are the importer's rules. The spooled file is removed when the job has
 * ended, whatever its answer.
 *
 * <p>The two lists are reads of stored rows. In the audit log,
 * {@code currentId} is the newest row of the same slice: that is the version
 * the candles hold, and the only one that can be undone (G46).
 */
@RestController
public class ImportsController {
    static final ListQuery.Spec IMPORTS = new ListQuery.Spec("-imported", "-id")
            .sort("id", "di.id")
            .sort("imported", "di.imported_at")
            .sort("source", "ds.name")
            .sort("instrument", "i.symbol")
            .sort("timeframe", "di.timeframe")
            .sort("archive", "di.archive_path")
            .sort("file", "di.file_name")
            .sort("rows", "di.row_count")
            .filter("source", "ds.name", ListQuery.Kind.TEXT)
            .filter("instrument", "i.symbol", ListQuery.Kind.TEXT)
            .filter("timeframe", "di.timeframe", ListQuery.Kind.TEXT);

    static final ListQuery.Spec FILES = new ListQuery.Spec("-imported", "-id")
            .sort("id", "f.id")
            .sort("imported", "f.imported_at")
            .sort("source", "ds.name")
            .sort("instrument", "i.symbol")
            .sort("timeframe", "f.timeframe")
            .sort("rows", "f.row_count")
            .filter("source", "ds.name", ListQuery.Kind.TEXT)
            .filter("instrument", "i.symbol", ListQuery.Kind.TEXT)
            .filter("timeframe", "f.timeframe", ListQuery.Kind.TEXT)
            .filter("kind", "f.change_kind", ListQuery.Kind.TEXT);

    private static final String IMPORTS_FROM = " FROM data_imports di "
            + "JOIN data_sources ds ON ds.id = di.source_id "
            + "JOIN instruments i ON i.id = di.instrument_id";
    private static final String FILES_FROM = " FROM import_files f "
            + "JOIN data_sources ds ON ds.id = f.source_id "
            + "JOIN instruments i ON i.id = f.instrument_id";

    private final JdbcClient db;
    private final PythonGateway python;
    private final ObjectMapper json;
    private final Path spool;

    public ImportsController(JdbcClient db, PythonGateway python, ObjectMapper json,
            @Value("${uploads.dir}") String spool) {
        this.db = db;
        this.python = python;
        this.json = json;
        this.spool = Path.of(spool);
    }

    @PostMapping("/api/imports")
    public ResponseEntity<Object> upload(
            @RequestParam(required = false) MultipartFile file,
            @RequestParam(defaultValue = "") String symbol,
            @RequestParam(defaultValue = "") String type,
            @RequestParam(defaultValue = "") String timeframe,
            @RequestParam(defaultValue = "default") String source,
            @RequestParam(defaultValue = "false") String force,
            @RequestParam(name = "aggregate_to", defaultValue = "") String aggregateTo,
            @RequestParam(name = "override_reason", defaultValue = "") String overrideReason)
            throws IOException {
        if (file == null) {
            return ResponseEntity.status(HttpStatus.UNPROCESSABLE_ENTITY)
                    .body(Map.of("error", "file is required: the CSV to import"));
        }
        Files.createDirectories(spool);
        Path spooled = Files.createTempFile(spool, "upload-", ".csv");
        try {
            file.transferTo(spooled);
            // The worker runs as another user in another container.
            spooled.toFile().setReadable(true, false);
            Map<String, Object> inputs = new LinkedHashMap<>();
            inputs.put("path", spooled.toString().replace('\\', '/'));
            inputs.put("filename", file.getOriginalFilename() == null
                    || file.getOriginalFilename().isBlank() ? "upload.csv" : file.getOriginalFilename());
            inputs.put("symbol", symbol);
            inputs.put("type", type);
            inputs.put("timeframe", timeframe);
            inputs.put("source", source);
            inputs.put("force", force);
            inputs.put("aggregate_to", aggregateTo);
            inputs.put("override_reason", overrideReason);
            return python.run("import-upload", inputs, 900, false);
        } finally {
            Files.deleteIfExists(spooled);
        }
    }

    @GetMapping("/api/imports")
    public Page<Map<String, Object>> imports(@RequestParam Map<String, String> query) {
        ListQuery q = ListQuery.parse(IMPORTS, query);
        long total = db.sql("SELECT COUNT(*)" + IMPORTS_FROM + q.where())
                .params(q.filterParams()).query(Long.class).single();
        List<Map<String, Object>> items = db.sql(
                "SELECT di.id, di.source_id, ds.name AS source_name, di.instrument_id, "
                        + "i.symbol AS instrument_symbol, di.timeframe, di.file_path, di.file_name, "
                        + "di.row_count, di.imported_at, di.file_hash, di.archive_path, "
                        + "di.check_override_reason, di.check_override_errors, di.check_override_by, "
                        + "(SELECT max(d2.id) FROM data_imports d2 "
                        + "  WHERE d2.archive_path = di.archive_path AND d2.instrument_id = di.instrument_id "
                        + "    AND d2.source_id = di.source_id AND d2.timeframe = di.timeframe) AS current_id"
                        + IMPORTS_FROM + q.where() + q.orderBy() + q.paging())
                .params(q.pageParams())
                .query((rs, n) -> {
                    Map<String, Object> m = new LinkedHashMap<>();
                    m.put("id", Wire.id(rs, "id"));
                    m.put("sourceId", Wire.id(rs, "source_id"));
                    m.put("sourceName", rs.getString("source_name"));
                    m.put("instrumentId", Wire.id(rs, "instrument_id"));
                    m.put("instrumentSymbol", rs.getString("instrument_symbol"));
                    m.put("timeframe", rs.getString("timeframe"));
                    m.put("filePath", rs.getString("file_path"));
                    m.put("fileName", rs.getString("file_name"));
                    m.put("rowCount", rs.getInt("row_count"));
                    m.put("importedAt", Wire.instant(rs, "imported_at"));
                    m.put("fileHash", rs.getString("file_hash"));
                    m.put("archivePath", rs.getString("archive_path"));
                    // Check errors stored on purpose: why, how many, and who.
                    String reason = rs.getString("check_override_reason");
                    Map<String, Object> override = null;
                    if (reason != null && !reason.isEmpty()) {
                        override = new LinkedHashMap<>();
                        override.put("reason", reason);
                        override.put("errors", Wire.integer(rs, "check_override_errors"));
                        override.put("by", rs.getString("check_override_by"));
                    }
                    m.put("checkOverride", override);
                    m.put("currentId", Wire.id(rs, "current_id"));
                    return m;
                }).list();
        return q.page(items, total);
    }

    /**
     * Each file is labelled with how it differed from the version before:
     * {@code new}, {@code update} (only later days added), {@code fix} (stored
     * bars changed or dropped), {@code update+fix}, or {@code same}. So a
     * provider's revision, which changes history, can be told from a refreshed
     * download, which only extends it. The importer writes the rows
     * ({@code python/loader/csv_import.py} owns the rules).
     */
    @GetMapping("/api/import-files")
    public Map<String, Object> importFiles(@RequestParam Map<String, String> query) {
        ListQuery q = ListQuery.parse(FILES, query);
        long total = db.sql("SELECT COUNT(*)" + FILES_FROM + q.where())
                .params(q.filterParams()).query(Long.class).single();
        List<Map<String, Object>> items = db.sql(
                "SELECT f.*, ds.name AS source_name, i.symbol AS instrument_symbol"
                        + FILES_FROM + q.where() + q.orderBy() + q.paging())
                .params(q.pageParams())
                .query((rs, n) -> {
                    Map<String, Object> m = new LinkedHashMap<>();
                    m.put("id", Wire.id(rs, "id"));
                    m.put("sourceName", rs.getString("source_name"));
                    m.put("instrumentSymbol", rs.getString("instrument_symbol"));
                    m.put("timeframe", rs.getString("timeframe"));
                    m.put("fileName", rs.getString("file_name"));
                    m.put("fileHash", rs.getString("file_hash"));
                    m.put("byteSize", rs.getLong("byte_size"));
                    m.put("rowCount", rs.getInt("row_count"));
                    m.put("firstDate", Wire.instant(rs, "first_date"));
                    m.put("lastDate", Wire.instant(rs, "last_date"));
                    m.put("changeKind", rs.getString("change_kind"));
                    List<Object> years = new ArrayList<>();
                    for (JsonNode slice : parsed(rs.getString("slices_json"), "[]")) {
                        years.add(slice.get("year"));
                    }
                    m.put("years", years);
                    // Per year: added, changed, removed, laterOnly, examples, and
                    // the price counts on imports made since those were kept.
                    m.put("diff", parsed(rs.getString("diff_json"), "{}"));
                    m.put("importedAt", Wire.instant(rs, "imported_at"));
                    m.put("lastSeenAt", Wire.instant(rs, "last_seen_at"));
                    m.put("importedBy", rs.getString("created_by"));
                    return m;
                }).list();
        Page<Map<String, Object>> page = q.page(items, total);
        Map<String, Object> out = new LinkedHashMap<>();
        out.put("items", page.items());
        out.put("total", page.total());
        out.put("limit", page.limit());
        out.put("offset", page.offset());
        out.put("sort", page.sort());
        out.put("available", true);
        return out;
    }

    /** Stored JSON text, or the fallback when it is missing or not JSON. */
    private JsonNode parsed(String text, String fallback) {
        if (text != null) {
            try {
                return json.readTree(text);
            } catch (RuntimeException e) {
                // An unreadable column is shown as empty rather than failing the page.
            }
        }
        return json.readTree(fallback);
    }
}
