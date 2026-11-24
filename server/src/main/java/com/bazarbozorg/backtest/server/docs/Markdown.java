package com.bazarbozorg.backtest.server.docs;

import java.util.ArrayList;
import java.util.HashMap;
import java.util.List;
import java.util.Locale;
import java.util.Map;
import java.util.regex.Matcher;
import java.util.regex.Pattern;
import org.commonmark.Extension;
import org.commonmark.ext.autolink.AutolinkExtension;
import org.commonmark.ext.gfm.strikethrough.StrikethroughExtension;
import org.commonmark.ext.gfm.tables.TablesExtension;
import org.commonmark.ext.task.list.items.TaskListItemsExtension;
import org.commonmark.node.Heading;
import org.commonmark.node.Node;
import org.commonmark.parser.IncludeSourceSpans;
import org.commonmark.parser.Parser;
import org.commonmark.renderer.html.HtmlRenderer;

/**
 * Markdown to the HTML the client shows inside its own layout, with
 * GitHub-style heading anchors.
 *
 * <p>The anchors are the reason this is its own class. Every in-page link in
 * the docs ("see [Model cache](#model-cache)") and every {@code /docs/<slug>#anchor}
 * link lands on a heading id, and one rule gives the ids to both the renderer
 * and the link check in {@code DocsTest}: a link checked against a different
 * rule would pass there and land nowhere in the app (G69).
 *
 * <p>The id comes from the heading's <em>raw</em> Markdown, not its rendered
 * text: rendering escapes {@code &} to {@code &amp;}, which would turn "Build
 * & Run" into {@code build-amp-run} and break every link written against
 * GitHub's {@code #build--run}.
 */
public final class Markdown {
    private Markdown() {
    }

    private static final List<Extension> EXTENSIONS = List.of(TablesExtension.create(),
            StrikethroughExtension.create(), TaskListItemsExtension.create(),
            AutolinkExtension.create());
    private static final Parser PARSER = Parser.builder().extensions(EXTENSIONS)
            .includeSourceSpans(IncludeSourceSpans.BLOCKS).build();
    private static final Pattern ATX = Pattern.compile("^\\s{0,3}#{1,6}\\s+(.*?)\\s*#*\\s*$");
    private static final Pattern FENCE = Pattern.compile("^\\s*(`{3,}|~{3,})");

    /**
     * GitHub's heading-anchor rule: lowercase, drop punctuation, each space to
     * a hyphen. Each space, not each run of them: dropping the {@code &} from
     * "Build & Run" leaves two spaces, and GitHub turns both into hyphens.
     */
    public static String slugify(String text) {
        return text.toLowerCase(Locale.ROOT).trim()
                .replaceAll("[^\\w\\s-]", "")
                .replaceAll("\\s", "-");
    }

    /** The ids a document's headings get, in order: repeats are suffixed -1, -2, as GitHub does. */
    public static List<String> headingIds(String markdown) {
        List<String> ids = new ArrayList<>();
        Map<String, Integer> seen = new HashMap<>();
        Character fence = null;
        for (String line : markdown.split("\\r?\\n", -1)) {
            Matcher f = FENCE.matcher(line);
            if (f.find()) {
                char mark = f.group(1).charAt(0);
                if (fence == null) {
                    fence = mark;
                } else if (fence == mark) {
                    fence = null;
                }
                continue;
            }
            if (fence != null) {
                continue;
            }
            Matcher h = ATX.matcher(line);
            if (h.matches()) {
                ids.add(unique(seen, slugify(h.group(1))));
            }
        }
        return ids;
    }

    private static String unique(Map<String, Integer> seen, String base) {
        int n = seen.merge(base, 1, Integer::sum) - 1;
        return n == 0 ? base : base + "-" + n;
    }

    public static String render(String markdown) {
        String[] lines = markdown.split("\\r?\\n", -1);
        Node document = PARSER.parse(markdown);
        Map<Node, String> ids = new HashMap<>();
        Map<String, Integer> seen = new HashMap<>();
        for (Node node = document.getFirstChild(); node != null; node = next(document, node)) {
            if (node instanceof Heading heading && !heading.getSourceSpans().isEmpty()) {
                int at = heading.getSourceSpans().get(0).getLineIndex();
                String line = at < lines.length ? lines[at] : "";
                Matcher h = ATX.matcher(line);
                // A heading underlined with === or --- has no # to strip.
                ids.put(node, unique(seen, slugify(h.matches() ? h.group(1) : line.trim())));
            }
        }
        return HtmlRenderer.builder().extensions(EXTENSIONS)
                .attributeProviderFactory(context -> (node, tag, attributes) -> {
                    String id = ids.get(node);
                    if (id != null) {
                        attributes.put("id", id);
                    }
                })
                .build().render(document);
    }

    /** The next node of the document in reading order, into children first. */
    private static Node next(Node root, Node node) {
        if (node.getFirstChild() != null) {
            return node.getFirstChild();
        }
        while (node != null && node != root) {
            if (node.getNext() != null) {
                return node.getNext();
            }
            node = node.getParent();
        }
        return null;
    }
}
