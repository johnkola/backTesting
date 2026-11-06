package com.bazarbozorg.backtest.api;

import com.bazarbozorg.backtest.config.AppConfig;
import com.bazarbozorg.backtest.data.BacktestResultRepository;
import com.bazarbozorg.backtest.data.DatabaseManager;
import com.bazarbozorg.backtest.data.WalkforwardRepository;
import com.bazarbozorg.backtest.engine.BacktestEngine;
import com.bazarbozorg.backtest.engine.BacktestResult;
import com.bazarbozorg.backtest.loader.LoaderAggregator;
import com.bazarbozorg.backtest.model.commission.CommissionModel;
import com.bazarbozorg.backtest.model.commission.FixedCommission;
import com.bazarbozorg.backtest.model.commission.PercentageCommission;
import com.bazarbozorg.backtest.model.enums.Timeframe;
import com.bazarbozorg.backtest.model.WalkforwardFold;
import com.bazarbozorg.backtest.model.WalkforwardRun;
import com.bazarbozorg.backtest.model.slippage.FixedSlippage;
import com.bazarbozorg.backtest.model.slippage.PercentageSlippage;
import com.bazarbozorg.backtest.model.slippage.SlippageModel;
import com.bazarbozorg.backtest.strategy.StrategyRegistry;
import com.bazarbozorg.backtest.strategy.TradingStrategy;
import com.bazarbozorg.backtest.strategy.persistence.ModelLoadPolicy;
import com.bazarbozorg.backtest.strategy.persistence.ModelNotCachedException;
import com.bazarbozorg.backtest.util.DateTimeUtils;
import com.google.gson.JsonElement;
import com.google.gson.JsonObject;
import com.google.gson.JsonParser;
import com.sun.net.httpserver.HttpExchange;
import com.sun.net.httpserver.HttpServer;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;

import java.io.IOException;
import java.net.InetSocketAddress;
import java.sql.Connection;
import java.time.ZonedDateTime;
import java.util.ArrayList;
import java.util.HashMap;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.Optional;
import java.util.concurrent.Executors;

import static com.bazarbozorg.backtest.api.HttpSupport.*;

/**
 * HTTP surface for the parts of the system only Java can serve: the strategy
 * registry and the backtest engine. Everything else the web client needs is
 * already answered by Node (reads) or the Python loader (imports, aggregation,
 * NN) — this closes the gap where the UI could display backtest results it had
 * no way to produce.
 *
 * <p>Endpoints:
 * <ul>
 *   <li>{@code GET  /api/strategies} — registered strategies with their default parameters</li>
 *   <li>{@code POST /api/run} — run a backtest, persist it, return the result</li>
 *   <li>{@code DELETE /api/results/{id}} — delete one saved result</li>
 *   <li>{@code GET  /api/health} — liveness, and whether the database answers</li>
 * </ul>
 *
 * <p><strong>Runs are synchronous.</strong> A backtest over a few thousand bars
 * is about a second, so the request holds until it finishes and returns the
 * saved result — the same stance {@code nn_api.py} takes on training. If
 * backtests grow to where that's uncomfortable, this is the place to put a job
 * queue: return 202 with an id and let the client poll. The handler pool is
 * bounded so a burst of requests queues rather than spawning unbounded JVM work.
 *
 * <p>No authentication: it binds inside the compose network, and Node proxies
 * it. If this is ever exposed beyond localhost, {@code /api/run} and the delete
 * need a token first — both execute work or destroy data.
 */
public class EngineApi {

    private static final Logger logger = LoggerFactory.getLogger(EngineApi.class);

    /** Backtests are CPU-bound; more threads than cores just thrashes. */
    private static final int HANDLER_THREADS =
            Math.max(2, Runtime.getRuntime().availableProcessors());

    private final DatabaseManager databaseManager;
    private HttpServer server;

    public EngineApi(DatabaseManager databaseManager) {
        this.databaseManager = databaseManager;
    }

    public void start(int port) throws IOException {
        server = HttpServer.create(new InetSocketAddress(port), 0);
        server.setExecutor(Executors.newFixedThreadPool(HANDLER_THREADS));

        server.createContext("/api/strategies", ex -> serve(ex, "GET", this::listStrategies));
        server.createContext("/api/run", ex -> serve(ex, "POST", this::runBacktest));
        server.createContext("/api/results", ex -> serve(ex, "DELETE", this::deleteResult));
        // One context for both the list and {id}: the JDK server matches
        // prefixes, not patterns, so the handler dispatches on whether a
        // trailing segment is present -- the same shape /api/results uses.
        server.createContext("/api/walkforward/runs",
                ex -> serve(ex, "GET", this::walkforwardRuns));
        server.createContext("/api/health", ex -> serve(ex, "GET", this::health));

        server.start();
        logger.info("Engine API listening on :{} ({} handler threads)", port, HANDLER_THREADS);
    }

    public void stop() {
        if (server != null) {
            server.stop(0);
        }
    }

    // ---- GET /api/strategies -------------------------------------------------

    /**
     * Everything a client needs to build a strategy picker and a parameter
     * form: the name {@code -s} accepts, a description, and the default
     * parameters with their values. Instantiates each strategy because
     * defaults live on the instance, not the registry.
     */
    private Object listStrategies(HttpExchange exchange) {
        StrategyRegistry registry = StrategyRegistry.getInstance();
        List<Map<String, Object>> items = new ArrayList<>();

        for (String name : registry.getAvailableStrategies()) {
            Optional<TradingStrategy> strategy = registry.getStrategy(name);
            if (strategy.isEmpty()) {
                continue;
            }
            Map<String, Object> item = new LinkedHashMap<>();
            item.put("name", strategy.get().getName());
            item.put("description", strategy.get().getDescription());
            item.put("defaultParameters", strategy.get().getDefaultParameters());
            // Tells the client whether to offer the model-version picker and
            // whether a run can fail for want of a cached model.
            item.put("requiresTrainedModel", strategy.get()
                    instanceof com.bazarbozorg.backtest.strategy.persistence.PersistableModelStrategy);
            items.add(item);
        }

        Map<String, Object> body = new LinkedHashMap<>();
        body.put("items", items);
        return body;
    }

    // ---- POST /api/run -------------------------------------------------------

    /**
     * Runs a backtest and persists it, mirroring the {@code run} subcommand
     * option for option so the two can't drift: same defaults, same
     * {@link ModelLoadPolicy#LOAD_ONLY} policy, same aggregate-if-missing step.
     */
    private Object runBacktest(HttpExchange exchange) {
        JsonObject body = readJson(exchange);

        String strategyName = requireString(body, "strategy");
        String symbol = requireString(body, "instrument");
        String timeframeCode = Optional.ofNullable(optString(body, "timeframe")).orElse("D1");
        String sourceName = Optional.ofNullable(optString(body, "source")).orElse("default");
        String modelVersion = optString(body, "modelVersion");
        boolean aggregateMissing = optBool(body, "aggregateMissing", true);

        TradingStrategy strategy = StrategyRegistry.getInstance().getStrategy(strategyName)
                .orElseThrow(() -> badRequest("unknown strategy: " + strategyName
                        + " (available: " + StrategyRegistry.getInstance()
                        .getAvailableStrategies() + ")"));

        Timeframe timeframe;
        try {
            timeframe = Timeframe.fromCode(timeframeCode);
        } catch (IllegalArgumentException e) {
            throw badRequest(e.getMessage());
        }

        ZonedDateTime from = parseDate(body, "from");
        ZonedDateTime to = parseDate(body, "to");

        AppConfig config = AppConfig.getInstance();
        Double capital = optDouble(body, "capital");
        double initialCapital = capital != null ? capital : config.getDefaultInitialCapital();

        Map<String, String> params = new HashMap<>();
        JsonElement rawParams = body.get("parameters");
        if (rawParams != null && rawParams.isJsonObject()) {
            for (Map.Entry<String, JsonElement> e : rawParams.getAsJsonObject().entrySet()) {
                if (!e.getValue().isJsonNull()) {
                    params.put(e.getKey(), e.getValue().getAsString());
                }
            }
        }

        if (aggregateMissing) {
            try {
                new LoaderAggregator(databaseManager).buildIfMissing(symbol, sourceName, timeframe);
            } catch (RuntimeException e) {
                // Same stance as the CLI: a loader that's down doesn't fail the
                // run, because the candles may already be there.
                logger.warn("Aggregate-if-missing skipped for {} {}: {}",
                        symbol, timeframe, e.getMessage());
            }
        }

        BacktestEngine engine = new BacktestEngine(databaseManager,
                commissionModel(config), slippageModel(config), initialCapital);

        BacktestResult result;
        try {
            result = engine.run(symbol, timeframe, strategy, params, from, to, sourceName,
                    ModelLoadPolicy.LOAD_ONLY, modelVersion);
        } catch (ModelNotCachedException e) {
            // 409: the request is well-formed, the server just has no model for
            // it yet. The client can act on this by training first.
            throw new HttpError(409, e.getMessage()
                    + " — train it first via POST /api/nn/train or `train -s " + strategyName
                    + " -i " + symbol + " -t " + timeframeCode + " --source " + sourceName + "`");
        } catch (IllegalArgumentException | IllegalStateException e) {
            // Unknown instrument/source, or no candles in range.
            throw badRequest(e.getMessage());
        }

        if (result == null) {
            throw new HttpError(500, "the backtest produced no result");
        }

        new BacktestResultRepository(databaseManager).save(result);
        logger.info("Ran {} on {} ({}) via API", strategyName, symbol, timeframe);

        return result;
    }

    // ---- DELETE /api/results/{id} -------------------------------------------

    private Object deleteResult(HttpExchange exchange) {
        String idSegment = lastPathSegment(exchange, "/api/results")
                .orElseThrow(() -> badRequest("expected /api/results/{id}"));

        long id;
        try {
            id = Long.parseLong(idSegment);
        } catch (NumberFormatException e) {
            throw badRequest("result id must be a number, got: " + idSegment);
        }

        boolean deleted = new BacktestResultRepository(databaseManager).deleteById(id);
        if (!deleted) {
            throw notFound("no saved backtest result with id " + id);
        }

        Map<String, Object> body = new LinkedHashMap<>();
        body.put("status", "deleted");
        body.put("id", id);
        return body;
    }

    // ---- GET /api/walkforward/runs[/{id}] ------------------------------------

    /**
     * The walk-forward record: every run with its folds summarised, or one run
     * with its folds listed.
     *
     * <p>This is the read the Training tab was missing. The runner used to print
     * folds to a terminal and persist nothing, so the tab could list the models
     * on disk and say nothing about how any of them scored. Python writes the
     * runs now; this serves them.
     *
     * <p><strong>Holdout runs are excluded unless asked for.</strong> The design
     * allows the test block to be spent once, to confirm a variant already
     * chosen on the validation folds. Listing those runs beside the ordinary
     * ones is how someone starts comparing variants on the holdout, which turns
     * it into training data. {@code ?includeTest=true} is the deliberate act.
     *
     * <p>Query parameters: {@code timeframe}, {@code includeTest},
     * {@code limit}. On the detail route they are ignored &mdash; an id is an
     * id.
     */
    private Object walkforwardRuns(HttpExchange exchange) {
        WalkforwardRepository repo = new WalkforwardRepository(databaseManager);
        Optional<String> idSegment = lastPathSegment(exchange, "/api/walkforward/runs");

        if (idSegment.isPresent()) {
            long id = parseRunId(idSegment.get());
            WalkforwardRun run = repo.findRun(id)
                    .orElseThrow(() -> notFound("no walk-forward run with id " + id));
            List<WalkforwardFold> folds = repo.findFolds(id);

            Map<String, Object> body = new LinkedHashMap<>();
            body.put("run", runPayload(run));
            body.put("folds", folds);
            return body;
        }

        Map<String, String> params = query(exchange);
        String timeframe = params.get("timeframe");
        boolean includeTest = "true".equalsIgnoreCase(params.get("includeTest"));
        int limit = parseLimit(params.get("limit"));

        List<WalkforwardRun> runs = repo.findRuns(timeframe, includeTest, limit);
        Map<String, Object> body = new LinkedHashMap<>();
        body.put("items", runs.stream().map(EngineApi::runPayload).toList());
        // Echoed back so a client can tell a filtered list from a complete one
        // -- an empty list means something different under each.
        body.put("timeframe", timeframe);
        body.put("includeTest", includeTest);
        body.put("limit", limit);
        return body;
    }

    /**
     * A run as the client wants it, with {@code hyperparamsJson} replaced by the
     * parsed object.
     *
     * <p>The record keeps the raw text because an entity row stays pure data
     * with no Gson knowledge, the same rule {@code BacktestResultRow} follows
     * for {@code resultJson}. Parsing belongs out here, at the edge. Text that
     * will not parse is passed through under {@code hyperparamsRaw} rather than
     * dropped: it is still the only record of what the run was launched with.
     */
    private static Map<String, Object> runPayload(WalkforwardRun run) {
        Map<String, Object> out = new LinkedHashMap<>();
        out.put("id", run.id());
        out.put("label", run.label());
        out.put("instrumentId", run.instrumentId());
        out.put("symbol", run.symbol());
        out.put("sourceId", run.sourceId());
        out.put("sourceName", run.sourceName());
        out.put("timeframe", run.timeframe());
        out.put("featureSetId", run.featureSetId());
        out.put("inputWidth", run.inputWidth());
        out.put("objective", run.objective());
        out.put("loss", run.loss());
        out.put("costBps", run.costBps());
        out.put("park", run.park());
        out.put("threshold", run.threshold());
        out.put("trainStart", run.trainStart());
        out.put("firstVal", run.firstVal());
        out.put("lastVal", run.lastVal());
        out.put("isTest", run.isTest());
        out.put("startedAt", run.startedAt());
        out.put("finishedAt", run.finishedAt());
        out.put("inProgress", run.inProgress());
        out.put("folds", run.folds());
        out.put("foldsWon", run.foldsWon());
        out.put("meanExcess", run.meanExcess());
        out.put("meanStratReturn", run.meanStratReturn());
        out.put("meanHoldReturn", run.meanHoldReturn());
        out.put("meanStratSharpe", run.meanStratSharpe());
        out.put("meanHoldSharpe", run.meanHoldSharpe());
        out.put("worstStratDrawdown", run.worstStratDrawdown());
        out.put("worstHoldDrawdown", run.worstHoldDrawdown());
        out.put("totalTrades", run.totalTrades());
        out.put("meanDaysOut", run.meanDaysOut());
        out.put("meanDriftTax", run.meanDriftTax());
        out.put("meanCommissions", run.meanCommissions());
        out.put("meanTiming", run.meanTiming());
        out.put("beatsHoldOnReturn", run.beatsHoldOnReturn());

        String raw = run.hyperparamsJson();
        JsonObject parsed = null;
        if (raw != null && !raw.isBlank()) {
            try {
                JsonElement el = JsonParser.parseString(raw);
                if (el.isJsonObject()) {
                    parsed = el.getAsJsonObject();
                }
            } catch (RuntimeException e) {
                logger.warn("walkforward_runs.hyperparams_json for run {} is not JSON",
                        run.id(), e);
            }
        }
        out.put("hyperparams", parsed);
        if (parsed == null && raw != null && !raw.isBlank()) {
            out.put("hyperparamsRaw", raw);
        }
        return out;
    }

    private static long parseRunId(String segment) {
        try {
            return Long.parseLong(segment);
        } catch (NumberFormatException e) {
            throw badRequest("run id must be a number, got '" + segment + "'");
        }
    }

    /** Absent or unparseable means the default, not an error. */
    private static int parseLimit(String raw) {
        if (raw == null || raw.isBlank()) {
            return WalkforwardRepository.DEFAULT_LIMIT;
        }
        try {
            return Integer.parseInt(raw);
        } catch (NumberFormatException e) {
            throw badRequest("limit must be a number, got '" + raw + "'");
        }
    }

    // ---- GET /api/health -----------------------------------------------------

    private Object health(HttpExchange exchange) {
        Map<String, Object> body = new LinkedHashMap<>();
        body.put("status", "ok");
        body.put("strategies", StrategyRegistry.getInstance().getAvailableStrategies().size());
        // try-with-resources matters more here than anywhere else in this class:
        // this endpoint is polled — the API's /api/health probes it on every
        // client page load — and the connection was previously never returned to
        // the pool, so the pool drained and the endpoint hung once it was.
        try (Connection conn = databaseManager.getConnection()) {
            body.put("database", conn.isValid(2) ? "up" : "down");
        } catch (Exception e) {
            body.put("database", "down");
        }
        return body;
    }

    // ---- helpers -------------------------------------------------------------

    private static ZonedDateTime parseDate(JsonObject body, String field) {
        String raw = optString(body, field);
        if (raw == null || raw.isBlank()) {
            return null;
        }
        try {
            return DateTimeUtils.parse(raw);
        } catch (RuntimeException e) {
            throw badRequest("'" + field + "' is not a date this server understands "
                    + "(yyyy-MM-dd or yyyy-MM-dd HH:mm:ss): " + raw);
        }
    }

    private static CommissionModel commissionModel(AppConfig config) {
        return "fixed".equalsIgnoreCase(config.getDefaultCommissionType())
                ? new FixedCommission(config.getDefaultCommissionValue())
                : new PercentageCommission(config.getDefaultCommissionValue());
    }

    private static SlippageModel slippageModel(AppConfig config) {
        return "fixed".equalsIgnoreCase(config.getDefaultSlippageType())
                ? new FixedSlippage(config.getDefaultSlippageValue())
                : new PercentageSlippage(config.getDefaultSlippageValue());
    }
}
