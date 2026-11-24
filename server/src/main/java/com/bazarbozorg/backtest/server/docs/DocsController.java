package com.bazarbozorg.backtest.server.docs;

import com.bazarbozorg.backtest.server.api.ApiException;
import com.bazarbozorg.backtest.server.api.Wire;
import jakarta.servlet.http.HttpServletRequest;
import java.io.IOException;
import java.net.URI;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.security.MessageDigest;
import java.security.NoSuchAlgorithmException;
import java.util.HexFormat;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.http.HttpStatus;
import org.springframework.http.ResponseEntity;
import org.springframework.jdbc.core.simple.JdbcClient;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PathVariable;
import org.springframework.web.bind.annotation.RequestParam;
import org.springframework.web.bind.annotation.RestController;

/**
 * The docs as data: the client renders the HTML inside the same layout and
 * theme as every other page ({@code /docs}).
 *
 * <p>What a change must keep:
 * <ul>
 *   <li><b>Reading a doc snapshots it when its content has changed</b>
 *       ({@code doc_revisions}, one row per distinct content). The design set
 *       is edited by hand outside this repo's tooling, so this is the only
 *       record of what it said last week. A failed snapshot never fails the
 *       read.</li>
 *   <li><b>A legacy URL redirects to a slug the registry knows</b>: a
 *       redirect to an unknown slug is a 301 into a 404 ({@code DocsTest}
 *       holds the targets to the registry).</li>
 * </ul>
 */
@RestController
public class DocsController {
    private static final Logger log = LoggerFactory.getLogger(DocsController.class);

    private final JdbcClient db;
    private final Path docsDir;
    private final String clientUrl;

    public DocsController(JdbcClient db, @Value("${docs.dir}") String docsDir,
            @Value("${client.url}") String clientUrl) {
        this.db = db;
        this.docsDir = Path.of(docsDir);
        this.clientUrl = clientUrl.replaceAll("/$", "");
    }

    @GetMapping("/api/docs")
    public Map<String, Object> list() {
        return Map.of("items", DocRegistry.DOCS.values().stream().map(d -> {
            Map<String, Object> item = new LinkedHashMap<>();
            item.put("name", d.name());
            item.put("label", d.label());
            item.put("slug", d.slug());
            return item;
        }).toList());
    }

    private static DocRegistry.Doc known(String slug) {
        DocRegistry.Doc doc = DocRegistry.BY_SLUG.get(slug);
        if (doc == null) {
            throw ApiException.notFound("unknown doc: " + slug);
        }
        return doc;
    }

    /** One rendered doc; {@code ?rev=N} is a captured revision instead of the live file. */
    @GetMapping("/api/docs/{slug}")
    public ResponseEntity<Object> one(@PathVariable String slug,
            @RequestParam(required = false) String rev) {
        DocRegistry.Doc doc = known(slug);
        String live;
        try {
            live = Files.readString(docsDir.resolve(doc.file()), StandardCharsets.UTF_8);
        } catch (IOException e) {
            return ResponseEntity.status(HttpStatus.INTERNAL_SERVER_ERROR).body(Map.of("error",
                    "failed to read " + doc.file() + ": " + e.getMessage()));
        }
        try {
            // One row per distinct content: the same text read twice is one revision.
            db.sql("INSERT INTO doc_revisions (doc_name, content, content_hash) VALUES (?, ?, ?) "
                            + "ON CONFLICT (doc_name, content_hash) DO NOTHING")
                    .params(doc.name(), live, sha256(live)).update();
        } catch (RuntimeException e) {
            log.error("could not snapshot {}", doc.name(), e);
        }

        Map<String, Object> body = new LinkedHashMap<>();
        body.put("name", doc.name());
        body.put("label", doc.label());
        body.put("slug", doc.slug());
        if (rev == null) {
            body.put("html", Markdown.render(live));
            body.put("revision", null);
            return ResponseEntity.ok(body);
        }
        if (!rev.matches("\\d{1,18}")) {
            throw ApiException.badRequest("rev must be a non-negative integer");
        }
        return db.sql("SELECT id, content, captured_at FROM doc_revisions "
                        + "WHERE doc_name = ? AND id = ?")
                .params(doc.name(), Long.parseLong(rev))
                .query((rs, n) -> {
                    body.put("html", Markdown.render(rs.getString("content")));
                    Map<String, Object> revision = new LinkedHashMap<>();
                    revision.put("id", Wire.id(rs, "id"));
                    revision.put("capturedAt", Wire.instant(rs, "captured_at"));
                    body.put("revision", revision);
                    return ResponseEntity.<Object>ok(body);
                })
                .optional()
                .orElseThrow(() -> ApiException.notFound("revision " + rev + " not found"));
    }

    /** The captured revisions, newest first. */
    @GetMapping("/api/docs/{slug}/history")
    public Map<String, Object> history(@PathVariable String slug) {
        DocRegistry.Doc doc = known(slug);
        List<Map<String, Object>> items = db.sql("SELECT id, content_hash, captured_at, "
                        + "length(content) AS size_bytes FROM doc_revisions WHERE doc_name = ? "
                        + "ORDER BY captured_at DESC, id DESC LIMIT 200")
                .param(doc.name())
                .query((rs, n) -> {
                    Map<String, Object> item = new LinkedHashMap<>();
                    item.put("id", Wire.id(rs, "id"));
                    item.put("capturedAt", Wire.instant(rs, "captured_at"));
                    item.put("contentHash", rs.getString("content_hash"));
                    item.put("sizeBytes", rs.getLong("size_bytes"));
                    return item;
                }).list();
        Map<String, Object> body = new LinkedHashMap<>();
        body.put("name", doc.name());
        body.put("label", doc.label());
        body.put("items", items);
        return body;
    }

    private static String sha256(String content) {
        try {
            return HexFormat.of().formatHex(MessageDigest.getInstance("SHA-256")
                    .digest(content.getBytes(StandardCharsets.UTF_8)));
        } catch (NoSuchAlgorithmException e) {
            throw new IllegalStateException(e);
        }
    }

    // --- Redirects --------------------------------------------------------------------
    // This service is the API. `/` and the old server-rendered doc URLs are
    // bookmarked and linked from commit messages; they live in the client now,
    // so they redirect there, query string included (so ?rev=N survives).

    @GetMapping("/")
    public ResponseEntity<Void> root() {
        return ResponseEntity.status(HttpStatus.FOUND).location(URI.create(clientUrl)).build();
    }

    @GetMapping({"/getting-started", "/readme", "/architecture", "/claude"})
    public ResponseEntity<Void> legacy(HttpServletRequest request) {
        String target = DocRegistry.LEGACY.get(request.getRequestURI().substring(1));
        String query = request.getQueryString();
        return ResponseEntity.status(HttpStatus.MOVED_PERMANENTLY).location(URI.create(
                clientUrl + "/docs/" + target + (query == null ? "" : "?" + query))).build();
    }

    @GetMapping({"/getting-started/history", "/readme/history", "/architecture/history",
            "/claude/history"})
    public ResponseEntity<Void> legacyHistory(HttpServletRequest request) {
        String slug = request.getRequestURI().split("/")[1];
        return ResponseEntity.status(HttpStatus.MOVED_PERMANENTLY).location(URI.create(
                clientUrl + "/docs/" + DocRegistry.LEGACY.get(slug) + "/history")).build();
    }
}
