package com.bazarbozorg.backtest.server.docs;

import java.util.LinkedHashMap;
import java.util.Map;

/**
 * Which Markdown files the Docs tab offers, and under which URL slug. Adding a
 * doc is a row here and nothing else: {@code /api/docs} lists these, the
 * client draws one tab per entry, and a revision is recorded for each.
 *
 * <p>The first two are the project's own docs: how to operate it, and what to
 * type. The last four are the design set (R59: "the whole design set,
 * Requirements included"), served out of {@code docs/readonly-doc/}. The older
 * copy under {@code docs/reference/qqq-backtesting-model/} is deliberately not
 * served: it still fits normalization statistics on each feature's full
 * history, which is a description of a data leak.
 *
 * <p>{@code file} is relative to {@code docs.dir}, and its case is
 * load-bearing: Windows and macOS would open {@code DESIGN.md} for a file
 * named {@code Design.md}, and Docker's build context and any Linux deploy
 * will not. {@code DocsTest} checks each path against the real directory
 * entry, case and all, and against the image's COPY and the compose mounts.
 */
public final class DocRegistry {
    private DocRegistry() {
    }

    public record Doc(String name, String file, String label, String slug) {
    }

    /** Where the authoritative design set lives. */
    public static final String DESIGN_DOC_DIR = "docs/readonly-doc/";

    public static final Map<String, Doc> DOCS = new LinkedHashMap<>();
    /** slug to doc, so the client can address a doc by the name in its URL. */
    public static final Map<String, Doc> BY_SLUG = new LinkedHashMap<>();
    /** The old server-rendered doc URLs, and the slug each now lands on. */
    public static final Map<String, String> LEGACY = new LinkedHashMap<>();

    static {
        add("GETTING_STARTED", "docs/readonly-doc/backTesting-10-Getting-Started.md",
                "Getting Started", "getting-started");
        add("README", "README.md", "README", "readme");
        add("QQQ_REQUIREMENTS", "docs/readonly-doc/backTesting-01-Requirements.md",
                "QQQ Requirements", "qqq-requirements");
        add("QQQ_DESIGN", "docs/readonly-doc/backTesting-03-Design-Notes.md",
                "QQQ Design Notes", "qqq-design");
        add("QQQ_ARCHITECTURE", "docs/readonly-doc/backTesting-04-Architecture.md",
                "QQQ Architecture", "qqq-architecture");
        add("QQQ_FEATURES", "docs/readonly-doc/backTesting-02-Features.md",
                "QQQ Features", "qqq-features");

        LEGACY.put("getting-started", "getting-started");
        LEGACY.put("readme", "readme");
        // The repo-root ARCHITECTURE.md these served was deleted; the QQQ
        // architecture is the only Architecture document left.
        LEGACY.put("architecture", "qqq-architecture");
        LEGACY.put("claude", "qqq-architecture");
    }

    private static void add(String name, String file, String label, String slug) {
        Doc doc = new Doc(name, file, label, slug);
        DOCS.put(name, doc);
        BY_SLUG.put(slug, doc);
    }
}
