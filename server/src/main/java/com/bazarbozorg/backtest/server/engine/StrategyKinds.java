package com.bazarbozorg.backtest.server.engine;

import com.bazarbozorg.backtest.server.jobs.Job;
import com.bazarbozorg.backtest.server.jobs.JobCalls;
import com.bazarbozorg.backtest.server.jobs.RequestJobs;
import java.time.Duration;
import java.util.HashMap;
import java.util.Map;
import org.springframework.stereotype.Service;
import tools.jackson.databind.JsonNode;
import tools.jackson.databind.ObjectMapper;

/**
 * What kind each registered strategy is, as the engine's registry says
 * ({@code GET /api/strategies}, {@code kind}): {@code model}, or
 * {@code benchmark} for the five indicator rules kept runnable for comparison
 * only (Design-Gaps C8).
 *
 * <p>The list is the engine's; nothing is listed here. A result names its
 * strategy and the label travels with it from this one place, so Results and
 * a result's page cannot disagree with the Run tab about what a rule is.
 *
 * <p>The last good answer is kept for {@link #TTL}. When the engine does not
 * answer, the previous answer is used if there is one, else no kinds at all (a
 * result page must still render), and the engine is not asked again for
 * {@link #RETRY}, so a list of results does not wait on it page after page.
 */
@Service
public class StrategyKinds {
    static final Duration TTL = Duration.ofSeconds(60);
    static final Duration RETRY = Duration.ofSeconds(10);
    static final Duration WAIT = Duration.ofSeconds(3);
    private static final String REQUEST =
            "{\"request\": {\"method\": \"GET\", \"path\": \"/api/strategies\", \"query\": {}, \"body\": null}}";

    private final JobCalls calls;
    private final ObjectMapper json;
    private volatile Map<String, String> kinds = Map.of();
    private volatile long nextAskAt = Long.MIN_VALUE;

    public StrategyKinds(JobCalls calls, ObjectMapper json) {
        this.calls = calls;
        this.json = json;
    }

    /** The kind of the strategy a result ran, or null when it is not known. Never guessed. */
    public String kindOf(String strategyName) {
        return read().get(strategyName);
    }

    private Map<String, String> read() {
        long now = System.nanoTime();
        if (nextAskAt != Long.MIN_VALUE && now < nextAskAt) {
            return kinds;
        }
        try {
            Job job = calls.call("strategies", EngineRoutes.ENGINE, REQUEST, WAIT, true);
            if (!job.status().equals("done")) {
                throw new IllegalStateException("the job was " + job.status());
            }
            JsonNode body = RequestJobs.body(json, json.readTree(job.result()));
            kinds = kindsFrom(body == null ? json.createArrayNode() : body.path("items"));
            nextAskAt = System.nanoTime() + TTL.toNanos();
        } catch (RuntimeException e) {
            // Keep what was known; ask again after a short while.
            nextAskAt = System.nanoTime() + RETRY.toNanos();
        }
        return kinds;
    }

    /** Name to kind, from the engine's {@code items}. Rows without a kind are left out. */
    static Map<String, String> kindsFrom(JsonNode items) {
        Map<String, String> out = new HashMap<>();
        for (JsonNode s : items) {
            String kind = s.path("kind").asString("");
            if (s.path("name").isString() && (kind.equals("model") || kind.equals("benchmark"))) {
                out.put(s.path("name").asString(), kind);
            }
        }
        return out;
    }
}
