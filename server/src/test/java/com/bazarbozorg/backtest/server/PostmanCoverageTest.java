package com.bazarbozorg.backtest.server;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertTrue;

import com.google.gson.JsonElement;
import com.google.gson.JsonObject;
import com.google.gson.JsonParser;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.ArrayList;
import java.util.List;
import java.util.TreeSet;
import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.web.bind.annotation.RequestMethod;
import org.springframework.web.servlet.mvc.method.annotation.RequestMappingHandlerMapping;

/**
 * Every route this service has is in the Postman collection (Architecture §6:
 * "every endpoint ships with a JUnit test and a matching Postman request").
 * A rule nobody remembers under pressure, so it is checked here: add a route
 * without a request and this fails, naming the route.
 *
 * <p>A request counts when it uses the {@code {{baseUrl}}} variable, has the
 * route's method, and its path matches the route's pattern with any value (or
 * a {@code {{variable}}}) in place of each {@code {name}}.
 */
class PostmanCoverageTest extends ServiceTest {
    static final Path COLLECTION = Path.of("..", "postman", "backtest.postman_collection.json");

    @Autowired
    RequestMappingHandlerMapping mappings;

    private static void collect(JsonElement node, List<String> out) {
        if (node.isJsonArray()) {
            node.getAsJsonArray().forEach(n -> collect(n, out));
            return;
        }
        JsonObject o = node.getAsJsonObject();
        if (o.has("item")) {
            collect(o.get("item"), out);
        }
        if (o.has("request")) {
            JsonObject request = o.getAsJsonObject("request");
            String raw = request.getAsJsonObject("url").get("raw").getAsString();
            if (raw.startsWith("{{baseUrl}}")) {
                String path = raw.substring("{{baseUrl}}".length()).split("\\?")[0];
                out.add(request.get("method").getAsString() + " " + path);
            }
        }
    }

    @Test
    void everyRouteHasAPostmanRequest() throws Exception {
        List<String> requests = new ArrayList<>();
        collect(JsonParser.parseString(Files.readString(COLLECTION)), requests);
        assertFalse(requests.isEmpty(), "no {{baseUrl}} requests found in " + COLLECTION);

        TreeSet<String> missing = new TreeSet<>();
        TreeSet<String> routes = new TreeSet<>();
        mappings.getHandlerMethods().forEach((info, handler) -> {
            if (!handler.getBeanType().getPackageName().startsWith("com.bazarbozorg")) {
                return;
            }
            for (String pattern : info.getPatternValues()) {
                if (!pattern.startsWith("/api/")) {
                    // `/` and the old doc URLs only redirect into the client
                    // (DocsTest holds their targets to the registry).
                    continue;
                }
                for (RequestMethod method : info.getMethodsCondition().getMethods()) {
                    String route = method + " " + pattern;
                    routes.add(route);
                    String regex = method + " " + pattern.replaceAll("\\{[^/}]+}", "[^/]+");
                    if (requests.stream().noneMatch(r -> r.matches(regex))) {
                        missing.add(route);
                    }
                }
            }
        });
        assertFalse(routes.isEmpty(), "the service reported no routes of its own");
        assertTrue(missing.isEmpty(), "routes with no Postman request: " + missing);

        // The other direction: a renamed route leaves a request that 404s,
        // which the runner would catch only if someone runs it.
        List<String> patterns = routes.stream()
                .map(r -> r.replaceAll("\\{[^/}]+}", "[^/]+")).toList();
        List<String> orphans = requests.stream()
                .filter(r -> r.contains(" /api/"))
                .filter(r -> patterns.stream().noneMatch(r::matches)).toList();
        assertEquals(List.of(), orphans, "requests with no matching route");
    }

    private static void scripts(JsonElement node, String trail, List<String[]> out) {
        if (node.isJsonArray()) {
            node.getAsJsonArray().forEach(n -> scripts(n, trail, out));
            return;
        }
        JsonObject o = node.getAsJsonObject();
        String name = o.has("name") ? trail + " / " + o.get("name").getAsString() : trail;
        if (o.has("item")) {
            scripts(o.get("item"), name, out);
        }
        if (o.has("request")) {
            StringBuilder tests = new StringBuilder();
            if (o.has("event")) {
                for (JsonElement e : o.getAsJsonArray("event")) {
                    if (e.getAsJsonObject().get("listen").getAsString().equals("test")) {
                        e.getAsJsonObject().getAsJsonObject("script").getAsJsonArray("exec")
                                .forEach(line -> tests.append(line.getAsString()).append('\n'));
                    }
                }
            }
            out.add(new String[] {name, tests.toString(),
                    o.getAsJsonObject("request").getAsJsonObject("url").get("raw").getAsString()});
        }
    }

    @Test
    void everyRequestAssertsAStatusAndGoesThroughTheBaseUrl() throws Exception {
        List<String[]> all = new ArrayList<>();
        scripts(JsonParser.parseString(Files.readString(COLLECTION)).getAsJsonObject().get("item"),
                "", all);
        assertTrue(all.size() >= 20, "only " + all.size() + " requests");
        // A request with no pm.test is a page view, not a test.
        assertEquals(List.of(), all.stream().filter(r -> !r[1].contains("pm.test("))
                .map(r -> r[0]).toList(), "requests with no pm.test");
        assertEquals(List.of(), all.stream()
                .filter(r -> !r[1].contains("pm.response.to.have.status")
                        && !r[1].contains("pm.response.code"))
                .map(r -> r[0]).toList(), "requests that assert no status");
        // A hard-coded host would pass locally and fail in the docker
        // environment, which is why there are two environment files.
        assertEquals(List.of(), all.stream().filter(r -> !r[2].startsWith("{{baseUrl}}"))
                .map(r -> r[0]).toList(), "requests not addressed through {{baseUrl}}");
        for (String env : List.of("local", "docker")) {
            String file = Files.readString(Path.of("..", "postman", env + ".postman_environment.json"));
            assertTrue(file.contains("\"baseUrl\""), env + " environment sets no baseUrl");
        }
    }
}
