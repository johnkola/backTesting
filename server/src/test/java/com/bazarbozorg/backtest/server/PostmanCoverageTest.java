package com.bazarbozorg.backtest.server;

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
 * The Node API has had this check since its routes were written; this is the
 * same check for the service that replaces it.
 *
 * <p>A request counts when it uses the {@code {{serverUrl}}} variable, has the
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
            if (raw.startsWith("{{serverUrl}}")) {
                String path = raw.substring("{{serverUrl}}".length()).split("\\?")[0];
                out.add(request.get("method").getAsString() + " " + path);
            }
        }
    }

    @Test
    void everyRouteHasAPostmanRequest() throws Exception {
        List<String> requests = new ArrayList<>();
        collect(JsonParser.parseString(Files.readString(COLLECTION)), requests);
        assertFalse(requests.isEmpty(), "no {{serverUrl}} requests found in " + COLLECTION);

        TreeSet<String> missing = new TreeSet<>();
        TreeSet<String> routes = new TreeSet<>();
        mappings.getHandlerMethods().forEach((info, handler) -> {
            if (!handler.getBeanType().getPackageName().startsWith("com.bazarbozorg")) {
                return;
            }
            for (String pattern : info.getPatternValues()) {
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
    }
}
