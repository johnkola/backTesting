package com.bazarbozorg.backtest.server;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertTrue;

import com.bazarbozorg.backtest.server.docs.DocRegistry;
import com.bazarbozorg.backtest.server.docs.Markdown;
import com.google.gson.JsonObject;
import java.net.URI;
import java.net.http.HttpClient;
import java.net.http.HttpRequest;
import java.net.http.HttpResponse;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.ArrayList;
import java.util.HashSet;
import java.util.List;
import java.util.Set;
import java.util.regex.Matcher;
import java.util.regex.Pattern;
import java.util.stream.Stream;
import org.junit.jupiter.api.Test;

/** The Docs tab's routes, the registry's files, and every link the project docs make into the tab. */
class DocsTest extends ServiceTest {
    static final Path ROOT = Path.of("..");

    static String read(String file) throws Exception {
        return Files.readString(ROOT.resolve(file));
    }

    // --- The registry -----------------------------------------------------------------

    @Test
    void everyEntryPointsAtAFileThatExistsSpelledExactlyRightWithContent() throws Exception {
        assertTrue(DocRegistry.DOCS.size() >= 6);
        for (DocRegistry.Doc doc : DocRegistry.DOCS.values()) {
            // Case and all: Windows would open DESIGN.md for Design.md, a Linux deploy will not.
            Path dir = ROOT;
            for (String segment : doc.file().split("/")) {
                try (Stream<Path> entries = Files.list(dir)) {
                    assertTrue(entries.anyMatch(e -> e.getFileName().toString().equals(segment)),
                            doc.file() + ": no entry spelled '" + segment + "' in " + dir);
                }
                dir = dir.resolve(segment);
            }
            assertTrue(read(doc.file()).trim().length() > 100, doc.file() + " is empty");
            assertTrue(doc.slug().matches("[a-z0-9-]+"), doc.slug());
            assertFalse(doc.label().isBlank());
        }
        assertEquals(DocRegistry.DOCS.size(), DocRegistry.BY_SLUG.size(), "slugs are unique");
        assertEquals(DocRegistry.DOCS.size(), DocRegistry.DOCS.values().stream()
                .map(DocRegistry.Doc::file).distinct().count(), "no doc shadows another");
    }

    @Test
    void theDesignSetIsServedOutOfReadonlyDocNotTheStaleReferenceCopy() {
        for (DocRegistry.Doc doc : DocRegistry.DOCS.values()) {
            assertFalse(doc.file().contains("docs/reference/"), doc.file());
            if (doc.name().startsWith("QQQ_")) {
                assertTrue(doc.file().startsWith(DocRegistry.DESIGN_DOC_DIR), doc.file());
            }
        }
    }

    @Test
    void everyRegisteredFileIsCopiedIntoTheImageAndMountedInCompose() throws Exception {
        String dockerfile = read("server/Dockerfile");
        String compose = read("docker-compose.yml");
        for (DocRegistry.Doc doc : DocRegistry.DOCS.values()) {
            assertTrue(dockerfile.contains(doc.file()), doc.file() + " is not COPYed into the image");
            assertTrue(compose.contains("/app/docs/" + doc.file()),
                    doc.file() + " is not mounted in compose");
        }
    }

    @Test
    void everyOldDocUrlRedirectsToASlugTheRegistryKnows() throws Exception {
        assertTrue(DocRegistry.LEGACY.size() >= 4);
        DocRegistry.LEGACY.values().forEach(slug ->
                assertTrue(DocRegistry.BY_SLUG.containsKey(slug), "no doc with slug " + slug));

        HttpClient plain = HttpClient.newBuilder().followRedirects(HttpClient.Redirect.NEVER).build();
        HttpResponse<String> old = plain.send(HttpRequest.newBuilder(
                URI.create("http://localhost:" + port + "/claude?rev=3")).build(),
                HttpResponse.BodyHandlers.ofString());
        assertEquals(301, old.statusCode());
        assertEquals("http://localhost:3000/docs/qqq-architecture?rev=3",
                old.headers().firstValue("location").orElseThrow());
        HttpResponse<String> history = plain.send(HttpRequest.newBuilder(
                URI.create("http://localhost:" + port + "/readme/history")).build(),
                HttpResponse.BodyHandlers.ofString());
        assertEquals("http://localhost:3000/docs/readme/history",
                history.headers().firstValue("location").orElseThrow());
        HttpResponse<String> root = plain.send(HttpRequest.newBuilder(
                URI.create("http://localhost:" + port + "/")).build(),
                HttpResponse.BodyHandlers.ofString());
        assertEquals(302, root.statusCode());
    }

    // --- The anchors and the links (G69) ----------------------------------------------

    @Test
    void headingIdsSlugAsGitHubDoesAndNumberRepeats() {
        assertEquals(List.of("build--run", "model-cache", "model-cache-1"),
                Markdown.headingIds("# Build & Run\n\n## Model cache\n\ntext\n\n## Model cache\n"));
        assertEquals(List.of("real"),
                Markdown.headingIds("```\n# not a heading\n```\n\n# Real\n"));
    }

    @Test
    void theRendererGivesHeadingsThoseIdsAndReadsTablesAsGitHubDoes() {
        String html = Markdown.render("# Build & Run\n\n## A `code` title\n\n## A `code` title\n\n"
                + "| a | b |\n|---|---|\n| 1 | 2 |\n\n~~gone~~ https://example.com\n");
        assertTrue(html.contains("<h1 id=\"build--run\">Build &amp; Run</h1>"), html);
        assertTrue(html.contains("<h2 id=\"a-code-title\">"), html);
        assertTrue(html.contains("<h2 id=\"a-code-title-1\">"), html);
        assertTrue(html.contains("<table>") && html.contains("<del>gone</del>"), html);
        assertTrue(html.contains("<a href=\"https://example.com\">"), html);
    }

    @Test
    void everyRegisteredDocRendersWithTheIdsTheLinkCheckExpects() throws Exception {
        // One rule, two users: if the renderer and headingIds ever disagreed,
        // a link could pass the check below and land nowhere in the app.
        Pattern heading = Pattern.compile("<h[1-6] id=\"([^\"]*)\"");
        for (DocRegistry.Doc doc : DocRegistry.DOCS.values()) {
            String markdown = read(doc.file());
            List<String> rendered = new ArrayList<>();
            Matcher m = heading.matcher(Markdown.render(markdown));
            while (m.find()) {
                rendered.add(m.group(1));
            }
            assertEquals(Markdown.headingIds(markdown), rendered, doc.file());
        }
    }

    @Test
    void everyDocsLinkAndAnchorInTheProjectDocsLandsOnAHeading() throws Exception {
        // Only the two project docs: the design set is the author's and may
        // not be edited here, so a broken link in it could not be fixed.
        Pattern link = Pattern.compile("\\]\\(([^)\\s]+)\\)");
        Pattern docs = Pattern.compile("^/docs/([^#?]+)(?:\\?[^#]*)?(?:#(.+))?$");
        List<String> problems = new ArrayList<>();
        for (String name : List.of("GETTING_STARTED", "README")) {
            DocRegistry.Doc own = DocRegistry.DOCS.get(name);
            String markdown = read(own.file());
            Set<String> ownIds = new HashSet<>(Markdown.headingIds(markdown));
            Character fence = null;
            int n = 0;
            for (String line : markdown.split("\\r?\\n", -1)) {
                n++;
                Matcher f = Pattern.compile("^\\s*(`{3,}|~{3,})").matcher(line);
                if (f.find()) {
                    char mark = f.group(1).charAt(0);
                    fence = fence == null ? (Character) mark : fence == mark ? null : fence;
                    continue;
                }
                if (fence != null) {
                    continue;
                }
                Matcher m = link.matcher(line);
                while (m.find()) {
                    String target = m.group(1);
                    String where = own.file() + ":" + n + " " + target;
                    if (target.startsWith("#")) {
                        if (!ownIds.contains(target.substring(1))) {
                            problems.add(where + ": no such heading here");
                        }
                        continue;
                    }
                    Matcher d = docs.matcher(target);
                    if (!d.matches()) {
                        continue;
                    }
                    DocRegistry.Doc doc = DocRegistry.BY_SLUG.get(d.group(1));
                    if (doc == null) {
                        problems.add(where + ": no doc with slug \"" + d.group(1) + "\"");
                    } else if (d.group(2) != null
                            && !Markdown.headingIds(read(doc.file())).contains(d.group(2))) {
                        problems.add(where + ": no heading \"#" + d.group(2) + "\" in " + doc.file());
                    }
                }
            }
        }
        assertEquals(List.of(), problems);
    }

    // --- The routes -------------------------------------------------------------------

    @Test
    void aDocIsServedRenderedAndReadingItRecordsARevision() throws Exception {
        assertEquals(DocRegistry.DOCS.size(), get("/api/docs").at("items").getAsJsonArray().size());
        JsonObject readme = get("/api/docs/readme").json();
        assertEquals("README", readme.get("name").getAsString());
        assertTrue(readme.get("html").getAsString().contains("<h1 id="));
        assertTrue(readme.get("revision").isJsonNull());

        JsonObject history = get("/api/docs/readme/history").json();
        JsonObject newest = history.getAsJsonArray("items").get(0).getAsJsonObject();
        assertEquals(64, newest.get("contentHash").getAsString().length());
        assertTrue(newest.get("sizeBytes").getAsLong() > 100);
        // The same text read twice is one revision.
        get("/api/docs/readme");
        assertEquals(history.getAsJsonArray("items").size(),
                get("/api/docs/readme/history").at("items").getAsJsonArray().size());

        JsonObject rev = get("/api/docs/readme?rev=" + newest.get("id").getAsString()).json();
        assertEquals(newest.get("id").getAsString(),
                rev.getAsJsonObject("revision").get("id").getAsString());
        assertEquals(readme.get("html").getAsString(), rev.get("html").getAsString());

        assertEquals(404, get("/api/docs/nope").status());
        assertEquals(404, get("/api/docs/nope/history").status());
        assertEquals(400, get("/api/docs/readme?rev=x").status());
        assertEquals(404, get("/api/docs/readme?rev=999999999").status());
    }
}
