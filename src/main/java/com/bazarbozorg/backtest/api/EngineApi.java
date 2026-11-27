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
import com.google.gson.JsonArray;
import com.google.gson.JsonElement;
import com.google.gson.JsonObject;
import com.google.gson.JsonParser;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;

import java.sql.Connection;
import java.time.ZonedDateTime;
import java.util.ArrayList;
import java.util.HashMap;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.Optional;

import static com.bazarbozorg.backtest.api.HttpSupport.*;

/**
 * What the engine answers: the parts of the system only Java can serve, the
 * strategy registry, the backtest engine and the walk-forward record.
 *
 * <p>It serves no HTTP. A request reaches it as a {@code java-compute} job
 * ({@code jobs.JobWorker}), carrying the method, path, query and body the
 * Spring Boot service received, and {@link #answer} turns it into a status
 * and a JSON body, which the worker stores as the job's result. The paths:
 * <ul>
 *   <li>{@code GET  /api/strategies}: registered strategies with their default parameters</li>
 *   <li>{@code POST /api/run}: run a backtest, store it, return the result</li>
 *   <li>{@code DELETE /api/results/{id}}: delete one stored result</li>
 *   <li>{@code GET  /api/walkforward/runs[/{id}]}: the walk-forward record</li>
 *   <li>{@code POST /api/walkforward/onnx-check?run={id}}: the prediction check (R62)</li>
 *   <li>{@code GET  /api/health}: the registry's size, and whether the database answers</li>
 * </ul>
 *
 * <p><strong>A run is answered when it ends.</strong> A backtest over a few
 * thousand bars is about a second, so the job is waited on and its result is
 * the stored result. The worker's lanes bound how many run at once.
 */
public class EngineApi {

    private static final Logger logger = LoggerFactory.getLogger(EngineApi.class);

    private final DatabaseManager databaseManager;

    public EngineApi(DatabaseManager databaseManager) {
        this.databaseManager = databaseManager;
    }

    /** A request as a handler reads it, whether it came over HTTP or out of a job. */
    public record Request(String method, String path, Map<String, String> query,
                          JsonObject body) {}

    /** The status and the body, the body already JSON. */
    public record Answer(int status, JsonElement body) {}

    /**
     * Answers one request. Every outcome is an {@link Answer}: the handler's
     * object on success, {@code {"error": ...}} with the right status for an
     * {@link HttpError}, and a 500 with the exception's message for anything
     * else. The job worker stores this as the job's result, so a refusal is a
     * finished job, never a failed one.
     */
    public Answer answer(Request req) {
        try {
            return new Answer(200, GSON.toJsonTree(route(req)));
        } catch (HttpError e) {
            return new Answer(e.status, error(e.getMessage()));
        } catch (Exception e) {
            logger.error("Unhandled error answering {} {}", req.method(), req.path(), e);
            return new Answer(500, error(e.getMessage() != null ? e.getMessage() : e.toString()));
        }
    }

    private Object route(Request req) {
        String path = req.path();
        if (path.equals("/api/strategies")) {
            return only(req, "GET", this::listStrategies);
        }
        if (path.equals("/api/run")) {
            return only(req, "POST", this::runBacktest);
        }
        if (under(path, "/api/results")) {
            return only(req, "DELETE", this::deleteResult);
        }
        // The list and {id} share a handler, which looks for a trailing segment.
        if (under(path, "/api/walkforward/runs")) {
            return only(req, "GET", this::walkforwardRuns);
        }
        // The prediction check (R62, G77): POST /api/walkforward/onnx-check?run={id}.
        if (path.equals("/api/walkforward/onnx-check")) {
            return only(req, "POST", this::onnxCheck);
        }
        if (path.equals("/api/health")) {
            return only(req, "GET", this::health);
        }
        throw notFound("the engine has no route " + req.method() + " " + path);
    }

    private static boolean under(String path, String prefix) {
        return path.equals(prefix) || path.startsWith(prefix + "/");
    }

    private static Object only(Request req, String method,
                               java.util.function.Function<Request, Object> handler) {
        if (!method.equalsIgnoreCase(req.method())) {
            throw new HttpError(405, method + " only");
        }
        return handler.apply(req);
    }

    private static JsonObject requireBody(Request req) {
        if (req.body() == null) {
            throw badRequest("a JSON request body is required");
        }
        return req.body();
    }

    // ---- GET /api/strategies -------------------------------------------------

    /**
     * Everything a client needs to build a strategy picker and a parameter
     * form: the name {@code -s} accepts, a description, and the default
     * parameters with their values. Instantiates each strategy because
     * defaults live on the instance, not the registry.
     */
    private Object listStrategies(Request req) {
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
            // `model` or `benchmark` (C8): the five indicator rules are kept
            // runnable for comparison and are not offered as strategies.
            item.put("kind", registry.kindOf(name)
                    .orElse(StrategyRegistry.Kind.MODEL).label());
            item.put("defaultParameters", strategy.get().getDefaultParameters());
            // Tells the client whether to offer the model-version picker and
            // whether a run can fail for want of a cached model.
            item.put("requiresTrainedModel", strategy.get()
                    instanceof com.bazarbozorg.backtest.strategy.persistence.PersistableModelStrategy);
            // How the Train page trains it through the loader's train route
            //: the engine, and each parameter's field there. Absent
            // for a strategy that does not train there (nn-feature-store is
            // trained offline by nn.train_features).
            if (strategy.get() instanceof com.bazarbozorg.backtest.strategy.nn.NeuralNetworkStrategy nn) {
                Map<String, Object> train = new LinkedHashMap<>();
                train.put("engine", nn.trainingEngine());
                train.put("fields", nn.trainFields());
                if (nn.otherParametersField() != null) {
                    train.put("otherParameters", nn.otherParametersField());
                }
                item.put("train", train);
            }
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
    private Object runBacktest(Request req) {
        JsonObject body = requireBody(req);

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
        // The end of a range: a bare date covers the whole day.
        ZonedDateTime to = parseDate(body, "to", true);

        AppConfig config = AppConfig.getInstance();
        Double capital = optDouble(body, "capital");
        // Score after tax (G20): percent on each realized gain, or absent for before tax.
        Double taxRate = optDouble(body, "taxRate");
        if (taxRate != null && !(taxRate > 0.0 && taxRate < 100.0)) {
            throw badRequest("taxRate must be a percent above 0 and below 100, or absent for "
                    + "before tax, not " + taxRate);
        }
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
                commissionModel(config), slippageModel(config), initialCapital).withTaxRate(taxRate);

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

        return withSampleMark(result);
    }

    /**
     * The run's result as JSON, plus whether it is in-sample (R82, G71).
     *
     * <p>The model's training window is not part of the stored result's JSON,
     * so the Run tab showed a fresh return with no mark while Results marked
     * the same row. {@code inSample} is true, false, or null when the window
     * is unknown; it is absent for a strategy with no trained model.
     * {@code inSampleNote} is the sentence the CLI prints.
     */
    static JsonObject withSampleMark(com.bazarbozorg.backtest.engine.BacktestResult result) {
        JsonObject body = HttpSupport.GSON.toJsonTree(result).getAsJsonObject();
        com.bazarbozorg.backtest.engine.ModelWindow window = result.getModelWindow();
        if (window != null) {
            java.time.LocalDate start = result.getStartDate().toLocalDate();
            java.time.LocalDate end = result.getEndDate().toLocalDate();
            body.addProperty("inSample", window.overlaps(start, end));
            body.addProperty("inSampleNote", window.describe(start, end));
        }
        return body;
    }

    // ---- DELETE /api/results/{id} -------------------------------------------

    private Object deleteResult(Request req) {
        String idSegment = lastPathSegment(req.path(), "/api/results")
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
    /** A query value, with an empty one taken as absent. */
    private static String orNull(String value) {
        return value == null || value.isBlank() ? null : value.trim();
    }

    private Object walkforwardRuns(Request req) {
        WalkforwardRepository repo = new WalkforwardRepository(databaseManager);
        Optional<String> idSegment = lastPathSegment(req.path(), "/api/walkforward/runs");

        if (idSegment.isPresent()) {
            long id = parseRunId(idSegment.get());
            WalkforwardRun run = repo.findRun(id)
                    .orElseThrow(() -> notFound("no walk-forward run with id " + id));
            List<WalkforwardFold> folds = repo.findFolds(id);

            Map<String, Object> body = new LinkedHashMap<>();
            Map<String, Object> payload = runPayload(run);
            if (run.inProgress()) {
                payload.put("progress", repo.progress(run));
            }
            body.put("run", payload);
            body.put("folds", folds);
            // G11: every engine's loss curve, fold by fold.
            body.put("epochs", repo.findEpochCurves(id));
            // One line per engine over its folds (B9); the run's own figures
            // above are its first engine's.
            body.put("engineSummaries",
                    com.bazarbozorg.backtest.model.EngineSummary.of(folds, run.target()));
            // R62 (G77): each fold's network run in ONNX Runtime against
            // PyTorch's output. Empty for a run that was never checked.
            try {
                body.put("onnxChecks",
                        com.bazarbozorg.backtest.engine.OnnxCheck.stored(databaseManager, id));
            } catch (java.sql.SQLException e) {
                throw new IllegalStateException("could not read the ONNX checks of run " + id, e);
            }
            return body;
        }

        Map<String, String> params = req.query();
        String timeframe = params.get("timeframe");
        boolean includeTest = "true".equalsIgnoreCase(params.get("includeTest"));
        int limit = parseLimit(params.get("limit"));
        String symbol = orNull(params.get("symbol"));
        String source = orNull(params.get("source"));
        if ((symbol == null) != (source == null)) {
            throw badRequest("symbol and source go together");
        }

        List<WalkforwardRun> runs = repo.findRuns(timeframe, symbol, source, includeTest, limit);
        Map<String, Object> body = new LinkedHashMap<>();
        body.put("items", runs.stream().map(r -> {
            Map<String, Object> payload = runPayload(r);
            if (r.inProgress()) {
                payload.put("progress", repo.progress(r));   // G11: the live line
            }
            return payload;
        }).toList());
        // Echoed back so a client can tell a filtered list from a complete one
        // -- an empty list means something different under each.
        body.put("timeframe", timeframe);
        body.put("symbol", symbol);
        body.put("source", source);
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
        out.put("taxRate", run.taxRate());
        out.put("recalculatedFrom", run.recalculatedFrom());
        out.put("purgeBars", run.purgeBars());
        out.put("trainStart", run.trainStart());
        out.put("firstVal", run.firstVal());
        out.put("lastVal", run.lastVal());
        // The training window (C6): expanding from trainStart, or sliding with
        // its length and step in years.
        Map<String, Object> window = new LinkedHashMap<>();
        window.put("kind", run.trainYears() == null ? "expanding" : "sliding");
        window.put("trainYears", run.trainYears());
        window.put("stepYears", run.stepYears() == null ? 1 : run.stepYears());
        out.put("window", window);
        out.put("isTest", run.isTest());
        out.put("columns", jsonArray(run.columnsJson(), run.id(), "columns_json"));
        // Each engine trained, with its settings, library version and seed (B9);
        // null for a run from before, which trained the network alone.
        out.put("engines", jsonArray(run.enginesJson(), run.id(), "engines_json"));
        // What it predicted, and the label with its horizon and thresholds (G49);
        // a run from before was the 3-class label. Its own key: `label` is the
        // run's name.
        out.put("target", run.target() == null ? "class" : run.target());
        out.put("labelSpec", jsonObject(run.labelJson(), run.id(), "label_json"));
        out.put("strategy", strategyPayload(run));
        out.put("lineage", lineagePayload(run));
        out.put("execution", run.execution());
        out.put("beatsLeveredOnReturn", run.execution().beatsLeveredOnReturn(run.meanStratReturn()));
        out.put("startedAt", run.startedAt());
        out.put("finishedAt", run.finishedAt());
        out.put("inProgress", run.inProgress());
        // G11: why a run stopped short (a NaN or infinite loss), or null.
        out.put("failedReason", run.failedReason());
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
        out.put("meanGainsTax", run.meanGainsTax());
        out.put("meanTiming", run.meanTiming());
        // R72, B8: the naive baseline, and whether the model beats it.
        out.put("meanAccuracy", run.meanAccuracy());
        out.put("meanBaselineAcc", run.meanBaselineAcc());
        out.put("beatsNaive", run.beatsNaive());
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

    /**
     * Where a strategy run came from and what it covered, or null for a run
     * launched from flags.
     *
     * <p>The stored pairs and triples ({@code [name, why]},
     * {@code [name, values, bars]}) become named objects here, at the edge, so
     * the client reads {@code partial[0].values} rather than an index.
     */
    private static Map<String, Object> strategyPayload(WalkforwardRun run) {
        WalkforwardRun.StrategyProvenance p = run.strategy();
        if (p == null) {
            return null;
        }
        Map<String, Object> out = new LinkedHashMap<>();
        out.put("id", p.id());
        out.put("name", p.name());
        out.put("version", p.version());
        out.put("dropped", tuples(jsonArray(p.droppedJson(), run.id(), "dropped_json"),
                "name", "why"));
        out.put("partial", tuples(jsonArray(p.partialJson(), run.id(), "partial_json"),
                "name", "values", "bars"));
        out.put("trainFirst", p.trainFirst());
        out.put("trainBarsUsed", p.trainBarsUsed());
        out.put("trainBars", p.trainBars());
        out.put("testEnd", p.testEnd());
        return out;
    }

    /**
     * The run's input snapshot and code version (G3), or null for a run from
     * before G3. The stored JSON objects are parsed so the client reads
     * {@code lineage.parts.matrix}, not a string.
     */
    private static Map<String, Object> lineagePayload(WalkforwardRun run) {
        WalkforwardRun.Lineage l = run.lineage();
        if (l == null) {
            return null;
        }
        Map<String, Object> out = new LinkedHashMap<>();
        out.put("featureRunId", l.featureRunId());
        out.put("currentBuildId", l.currentBuildId());
        out.put("inputSnapshot", l.inputSnapshot());
        out.put("parts", jsonObject(l.snapshotJson(), run.id(), "snapshot_json"));
        out.put("codeHash", l.codeHash());
        out.put("gitSha", l.gitSha());
        out.put("libVersions", jsonObject(l.libVersionsJson(), run.id(), "lib_versions_json"));
        out.put("setRebuiltSince", l.setRebuiltSince());
        out.put("valuesChangedSince", l.valuesChangedSince());
        return out;
    }

    /** Stored JSON text as an object, or null when absent or not an object. */
    private static JsonObject jsonObject(String raw, long runId, String column) {
        if (raw == null || raw.isBlank()) {
            return null;
        }
        try {
            JsonElement el = JsonParser.parseString(raw);
            if (el.isJsonObject()) {
                return el.getAsJsonObject();
            }
        } catch (RuntimeException e) {
            logger.warn("walkforward_runs.{} for run {} is not JSON", column, runId, e);
        }
        return null;
    }

    /** Stored JSON text as an array, or null when absent or not an array. */
    private static JsonArray jsonArray(String raw, long runId, String column) {
        if (raw == null || raw.isBlank()) {
            return null;
        }
        try {
            JsonElement el = JsonParser.parseString(raw);
            if (el.isJsonArray()) {
                return el.getAsJsonArray();
            }
        } catch (RuntimeException e) {
            // Fall through: logged below, served as null rather than failing the read.
        }
        logger.warn("walkforward_runs.{} for run {} is not a JSON array", column, runId);
        return null;
    }

    /** {@code [[a, b], ...]} to {@code [{k1: a, k2: b}, ...]}; extra items are ignored. */
    private static List<Map<String, Object>> tuples(JsonArray rows, String... keys) {
        if (rows == null) {
            return null;
        }
        List<Map<String, Object>> out = new java.util.ArrayList<>();
        for (JsonElement row : rows) {
            if (!row.isJsonArray()) {
                continue;
            }
            JsonArray cells = row.getAsJsonArray();
            Map<String, Object> obj = new LinkedHashMap<>();
            for (int i = 0; i < keys.length && i < cells.size(); i++) {
                JsonElement cell = cells.get(i);
                obj.put(keys[i], cell.isJsonNull() ? null : cell);
            }
            out.add(obj);
        }
        return out;
    }

    /**
     * Runs the prediction check for one walk-forward run (R62, G77): every fold
     * whose network was exported to ONNX is run in ONNX Runtime on its own
     * scored inputs and compared with PyTorch's output. Stores a row per fold
     * and answers with them. A fold that differs is a warning in the answer,
     * never an error status: the check reports, it does not stop anything.
     */
    private Object onnxCheck(Request req) {
        String raw = req.query().get("run");
        if (raw == null || raw.isBlank()) {
            throw badRequest("expected ?run={id}");
        }
        long id = parseRunId(raw);
        new WalkforwardRepository(databaseManager).findRun(id)
                .orElseThrow(() -> notFound("no walk-forward run with id " + id));
        try {
            List<com.bazarbozorg.backtest.engine.OnnxCheck.FoldCheck> folds =
                    com.bazarbozorg.backtest.engine.OnnxCheck.checkRun(databaseManager, id,
                            com.bazarbozorg.backtest.engine.OnnxCheck.baseDir());
            Map<String, Object> body = new LinkedHashMap<>();
            body.put("runId", id);
            body.put("runtime", "onnxruntime "
                    + com.bazarbozorg.backtest.engine.OnnxCheck.runtimeVersion());
            body.put("checked", folds.stream().filter(f -> f.problem() == null).count());
            body.put("agreed", folds.stream().filter(
                    com.bazarbozorg.backtest.engine.OnnxCheck.FoldCheck::agreed).count());
            body.put("folds", folds);
            return body;
        } catch (java.sql.SQLException e) {
            throw new IllegalStateException("the ONNX check of run " + id + " failed", e);
        }
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

    private Object health(Request req) {
        Map<String, Object> body = new LinkedHashMap<>();
        body.put("status", "ok");
        body.put("strategies", StrategyRegistry.getInstance().getAvailableStrategies().size());
        // try-with-resources matters more here than anywhere else in this class:
        // health is polled, and a connection not returned each time drains the pool.
        try (Connection conn = databaseManager.getConnection()) {
            body.put("database", conn.isValid(2) ? "up" : "down");
        } catch (Exception e) {
            body.put("database", "down");
        }
        return body;
    }

    // ---- helpers -------------------------------------------------------------

    private static ZonedDateTime parseDate(JsonObject body, String field) {
        return parseDate(body, field, false);
    }

    private static ZonedDateTime parseDate(JsonObject body, String field, boolean rangeEnd) {
        String raw = optString(body, field);
        if (raw == null || raw.isBlank()) {
            return null;
        }
        ZonedDateTime parsed;
        try {
            parsed = rangeEnd ? DateTimeUtils.parseEnd(raw) : DateTimeUtils.parse(raw);
        } catch (RuntimeException e) {
            parsed = null;
        }
        // The parser answers null for a value it cannot read, and a null end
        // means "the whole series": a mistyped date widened the run without a
        // word. It is refused instead.
        if (parsed == null) {
            throw badRequest("'" + field + "' is not a date this server understands "
                    + "(yyyy-MM-dd, yyyy-MM-dd HH:mm:ss, or an ISO instant such as "
                    + "2016-01-01T00:00:00Z): " + raw);
        }
        return parsed;
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
