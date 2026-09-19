package com.bazarbozorg.backtest.api;

import com.bazarbozorg.backtest.config.AppConfig;
import com.bazarbozorg.backtest.data.BacktestResultRepository;
import com.bazarbozorg.backtest.data.DatabaseManager;
import com.bazarbozorg.backtest.engine.BacktestEngine;
import com.bazarbozorg.backtest.engine.BacktestResult;
import com.bazarbozorg.backtest.loader.LoaderAggregator;
import com.bazarbozorg.backtest.model.commission.CommissionModel;
import com.bazarbozorg.backtest.model.commission.FixedCommission;
import com.bazarbozorg.backtest.model.commission.PercentageCommission;
import com.bazarbozorg.backtest.model.enums.Timeframe;
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
