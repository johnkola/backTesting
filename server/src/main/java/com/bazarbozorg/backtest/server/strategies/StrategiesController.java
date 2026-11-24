package com.bazarbozorg.backtest.server.strategies;

import com.bazarbozorg.backtest.server.api.ApiException;
import com.bazarbozorg.backtest.server.api.Wire;
import com.bazarbozorg.backtest.server.python.PythonGateway;
import java.sql.ResultSet;
import java.sql.SQLException;
import java.util.ArrayList;
import java.util.HashMap;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.Optional;
import org.springframework.http.HttpStatus;
import org.springframework.http.ResponseEntity;
import org.springframework.jdbc.core.simple.JdbcClient;
import org.springframework.transaction.support.TransactionTemplate;
import org.springframework.web.bind.annotation.DeleteMapping;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PathVariable;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.RequestParam;
import org.springframework.web.bind.annotation.RestController;
import tools.jackson.core.JacksonException;
import tools.jackson.databind.JsonNode;
import tools.jackson.databind.ObjectMapper;

/**
 * Saved strategies: the choices a training run makes, named and versioned
 * (Architecture §8.1a, §8.2a). Train and Run execute a saved strategy and make
 * no choices of their own.
 *
 * <p>What a change must keep:
 * <ul>
 *   <li><b>A save never overwrites.</b> Editing a strategy creates a new
 *       version, so every run still points at exactly what it was trained
 *       from. There is no update here, on purpose. The version is chosen
 *       inside the INSERT, so two saves at once cannot pick the same
 *       number.</li>
 *   <li><b>A version a run was trained from cannot be removed</b> (409):
 *       {@code walkforward_runs.strategy_id} points at it, and the windows,
 *       features and objective live only in this row. Removing every version
 *       of a name is all or nothing.</li>
 *   <li><b>What can run is the runner's answer.</b> The engines, model types
 *       and validation schemes are read from the Python worker, and a save
 *       asks it whether it would run the strategy; when it does not answer
 *       the save is refused (503), never guessed at. The shape checks are
 *       {@link StrategyRules}.</li>
 *   <li><b>A data-only feature is refused on save</b> rather than dropped at
 *       run time: the feature list is a claim about what the model trains
 *       on.</li>
 * </ul>
 */
@RestController
public class StrategiesController {
    private final JdbcClient db;
    private final TransactionTemplate tx;
    private final PythonGateway python;
    private final ObjectMapper json;

    public StrategiesController(JdbcClient db, TransactionTemplate tx, PythonGateway python,
            ObjectMapper json) {
        this.db = db;
        this.tx = tx;
        this.python = python;
        this.json = json;
    }

    private static boolean yes(String raw) {
        return raw != null && raw.matches("(?i)1|true|yes");
    }

    /** The runner's engines (B9, R22), or null with the reason. */
    private PythonGateway.Answer engineRegistry() {
        PythonGateway.Answer a = python.ask(PythonGateway.CALL, "GET", "/api/nn/engines", null, 5);
        if (a.error() != null) {
            return a;
        }
        JsonNode items = a.body() == null ? null : a.body().get("items");
        return new PythonGateway.Answer(items != null && items.isArray() ? items : null, null);
    }

    /** The runner's model types, validation schemes and unusable timeframes (G54), or null with the reason. */
    private PythonGateway.Answer runnerOptions() {
        PythonGateway.Answer a = python.ask(PythonGateway.CALL, "GET",
                "/api/nn/strategies/options", null, 5);
        if (a.error() != null) {
            return new PythonGateway.Answer(null, a.error());
        }
        for (String key : List.of("modelTypes", "validationSchemes", "unrunnableTimeframes")) {
            if (a.body() == null || !a.body().path(key).isArray()) {
                return new PythonGateway.Answer(null,
                        "the loader answered without the runner's options");
            }
        }
        return a;
    }

    @GetMapping("/api/strategies/saved")
    public Map<String, Object> list(@RequestParam(required = false) String name,
            @RequestParam(required = false) String latest) {
        PythonGateway.Answer registry = engineRegistry();
        PythonGateway.Answer runner = runnerOptions();
        Map<String, Object> body = new LinkedHashMap<>();
        body.put("items", rows(name == null || name.isEmpty() ? null : name, yes(latest)));
        // Read from the runner, never listed here (R22).
        body.put("engines", registry.error() == null ? registry.body() : null);
        body.put("enginesError", registry.error());
        // The form is generated from the same spec the validator enforces.
        body.put("paramSpec", StrategyRules.PARAM_SPEC);
        body.put("objectiveSpec", StrategyRules.OBJECTIVE_SPEC);
        body.put("labelKinds", StrategyRules.LABEL_KINDS);
        body.put("naiveBaselines", StrategyRules.NAIVE_BASELINES);
        // The runner's own lists (G54); null with `runnerError` when it did not answer.
        JsonNode options = runner.body();
        body.put("modelTypes", options == null ? null : options.get("modelTypes"));
        body.put("validationSchemes", options == null ? null : options.get("validationSchemes"));
        body.put("unrunnableTimeframes", options == null ? null : options.get("unrunnableTimeframes"));
        body.put("runnerError", runner.error());
        body.put("windows", StrategyRules.WINDOWS);
        body.put("requiredWindows", StrategyRules.REQUIRED_WINDOWS);
        return body;
    }

    @GetMapping("/api/strategies/saved/{id}")
    public Map<String, Object> one(@PathVariable String id) {
        return byId(numeric(id))
                .orElseThrow(() -> ApiException.notFound("no strategy with id " + id));
    }

    /**
     * Saves a new version. {@code ?check=1} answers what a save would and
     * stores nothing: the Strategy tab asks it while the form is filled, and a
     * refusal is then the answer (200, {@code {ok: false, errors}}).
     */
    @PostMapping("/api/strategies/saved")
    public ResponseEntity<Object> save(@RequestBody(required = false) JsonNode body,
            @RequestParam(required = false) String check) {
        boolean checkOnly = yes(check);
        PythonGateway.Answer registry = engineRegistry();
        PythonGateway.Answer runner = runnerOptions();
        if (registry.body() == null) {
            return unavailable("the engine registry is unavailable, so the engines cannot be "
                    + "checked: " + registry.error());
        }
        if (runner.body() == null) {
            return unavailable("the runner is unavailable, so the strategy cannot be checked: "
                    + runner.error());
        }
        StrategyRules.Checked checked = StrategyRules.validate(body, registry.body(), runner.body());
        if (!checked.ok()) {
            return refuse(checkOnly, checked.errors());
        }
        Map<String, Object> value = checked.value();
        @SuppressWarnings("unchecked")
        List<String> dataOnly = dataOnlyFeatures((List<String>) value.get("features"));
        if (!dataOnly.isEmpty()) {
            return refuse(checkOnly, dataOnly.stream().map(n -> n
                    + " is data only: stored for charts and rules, kept out of the network").toList());
        }
        // The runner's own answer, from the functions a run calls (G54): a
        // strategy it would refuse is not saved.
        PythonGateway.Answer verdict = python.ask("strategy-check", "POST",
                "/api/nn/strategies/check", value, 10);
        if (verdict.error() != null || verdict.body() == null) {
            return unavailable("the runner did not answer, so the strategy cannot be checked: "
                    + verdict.error());
        }
        if (!verdict.body().path("ok").asBoolean(false)) {
            List<String> reasons = new ArrayList<>();
            verdict.body().path("reasons").forEach(r -> reasons.add("the runner refuses it: "
                    + r.asString()));
            return refuse(checkOnly, reasons);
        }
        if (checkOnly) {
            Map<String, Object> ok = new LinkedHashMap<>();
            ok.put("ok", true);
            ok.put("errors", List.of());
            return ResponseEntity.ok(ok);
        }
        return ResponseEntity.status(HttpStatus.CREATED).body(insert(value));
    }

    private static ResponseEntity<Object> unavailable(String why) {
        return ResponseEntity.status(HttpStatus.SERVICE_UNAVAILABLE).body(Map.of("error", why));
    }

    private static ResponseEntity<Object> refuse(boolean checkOnly, List<String> errors) {
        Map<String, Object> body = new LinkedHashMap<>();
        if (checkOnly) {
            body.put("ok", false);
            body.put("errors", errors);
            return ResponseEntity.ok(body);
        }
        // 422, not 400: the body parsed, the values are what is wrong.
        body.put("error", String.join("; ", errors));
        body.put("errors", errors);
        return ResponseEntity.status(HttpStatus.UNPROCESSABLE_ENTITY).body(body);
    }

    /**
     * Removes one version, or with {@code ?all=1} every version of the name
     * the id belongs to (what the page's remove button sends).
     */
    @DeleteMapping("/api/strategies/saved/{id}")
    public ResponseEntity<Object> remove(@PathVariable String id,
            @RequestParam(required = false) String all) {
        long key = numeric(id);
        Map<String, Object> body = new LinkedHashMap<>();
        body.put("status", "deleted");
        if (!yes(all)) {
            ResponseEntity<Object> held = inUse(List.of(key));
            if (held != null) {
                return held;
            }
            if (db.sql("DELETE FROM strategies WHERE id = ?").param(key).update() == 0) {
                throw ApiException.notFound("no strategy with id " + id);
            }
            body.put("id", id);
            body.put("versionsDeleted", 1);
            return ResponseEntity.ok(body);
        }
        String name = db.sql("SELECT name FROM strategies WHERE id = ?").param(key)
                .query(String.class).optional()
                .orElseThrow(() -> ApiException.notFound("no strategy with id " + id));
        List<Long> ids = db.sql("SELECT id FROM strategies WHERE name = ? ORDER BY id").param(name)
                .query(Long.class).list();
        ResponseEntity<Object> held = inUse(ids);
        if (held != null) {
            return held;
        }
        body.put("name", name);
        body.put("versionsDeleted",
                db.sql("DELETE FROM strategies WHERE name = ?").param(name).update());
        return ResponseEntity.ok(body);
    }

    /** A 409 when a walk-forward run was trained from any of {@code ids}; null when none was. */
    private ResponseEntity<Object> inUse(List<Long> ids) {
        String marks = String.join(", ", ids.stream().map(i -> "?").toList());
        int runs = db.sql("SELECT COUNT(*)::int FROM walkforward_runs WHERE strategy_id IN ("
                + marks + ")").params(new ArrayList<Object>(ids)).query(Integer.class).single();
        if (runs == 0) {
            return null;
        }
        Map<String, Object> body = new LinkedHashMap<>();
        body.put("error", runs + " walk-forward run" + (runs == 1 ? "" : "s")
                + " trained from this strategy (id "
                + String.join(", ", ids.stream().map(String::valueOf).toList())
                + "); a version a run points at cannot be removed");
        body.put("runs", runs);
        return ResponseEntity.status(HttpStatus.CONFLICT).body(body);
    }

    private static long numeric(String id) {
        if (!id.matches("\\d{1,18}")) {
            throw ApiException.badRequest("id must be numeric");
        }
        return Long.parseLong(id);
    }

    // --- Rows -----------------------------------------------------------------------

    /** Every version, newest first; {@code latestOnly} keeps one row per name. */
    private List<Map<String, Object>> rows(String name, boolean latestOnly) {
        String sql;
        if (latestOnly) {
            sql = "SELECT s.* FROM strategies s JOIN (SELECT name, MAX(version) AS v FROM strategies "
                    + "GROUP BY name) m ON m.name = s.name AND m.v = s.version "
                    + (name != null ? "WHERE s.name = ? " : "") + "ORDER BY s.name ASC";
        } else {
            sql = name != null ? "SELECT * FROM strategies WHERE name = ? ORDER BY version DESC"
                    : "SELECT * FROM strategies ORDER BY name ASC, version DESC";
        }
        JdbcClient.StatementSpec query = db.sql(sql);
        return withEngines((name != null ? query.param(name) : query).query(this::item).list());
    }

    private Optional<Map<String, Object>> byId(long id) {
        return withEngines(db.sql("SELECT * FROM strategies WHERE id = ?").param(id)
                .query(this::item).list()).stream().findFirst();
    }

    /** Each strategy with its engines attached, read in one query. None stored: the network alone. */
    private List<Map<String, Object>> withEngines(List<Map<String, Object>> items) {
        if (items.isEmpty()) {
            return items;
        }
        Map<String, Map<String, Object>> by = new HashMap<>();
        String marks = String.join(", ", items.stream().map(i -> "?").toList());
        db.sql("SELECT strategy_id, engine, enabled, settings_json FROM strategy_engines "
                        + "WHERE strategy_id IN (" + marks + ") ORDER BY strategy_id, engine")
                .params(items.stream().map(i -> (Object) Long.parseLong((String) i.get("id"))).toList())
                .query((rs, n) -> {
                    Map<String, Object> engine = new LinkedHashMap<>();
                    engine.put("enabled", rs.getBoolean("enabled"));
                    engine.put("settings", parse(rs.getString("settings_json"), Map.of()));
                    by.computeIfAbsent(Wire.id(rs, "strategy_id"), k -> new LinkedHashMap<>())
                            .put(rs.getString("engine"), engine);
                    return engine;
                }).list();
        for (Map<String, Object> item : items) {
            Map<String, Object> engines = by.get(item.get("id"));
            item.put("enginesDefaulted", engines == null);
            item.put("engines", engines != null ? engines : networkAlone());
        }
        return items;
    }

    private static Map<String, Object> networkAlone() {
        Map<String, Object> mlp = new LinkedHashMap<>();
        mlp.put("enabled", true);
        mlp.put("settings", Map.of());
        Map<String, Object> out = new LinkedHashMap<>();
        out.put("mlp", mlp);
        return out;
    }

    private Object parse(String text, Object fallback) {
        if (text == null) {
            return fallback;
        }
        try {
            return json.readTree(text);
        } catch (JacksonException e) {
            return fallback;
        }
    }

    private Map<String, Object> item(ResultSet rs, int n) throws SQLException {
        Map<String, Object> item = new LinkedHashMap<>();
        item.put("id", Wire.id(rs, "id"));
        item.put("name", rs.getString("name"));
        item.put("version", rs.getInt("version"));
        item.put("instrument", rs.getString("instrument"));
        item.put("source", rs.getString("source"));
        item.put("timeframe", rs.getString("timeframe"));
        item.put("modelType", rs.getString("model_type"));
        item.put("validationScheme", rs.getString("validation_scheme"));
        item.put("features", parse(rs.getString("features_json"), List.of()));
        item.put("windows", parse(rs.getString("windows_json"), Map.of()));
        item.put("label", parse(rs.getString("label_json"), null));
        item.put("hyperparams", parse(rs.getString("hyperparams_json"), Map.of()));
        // A row saved before `objective` existed holds NULL: its values are the defaults.
        Map<String, Object> objective = new LinkedHashMap<>(StrategyRules.OBJECTIVE_DEFAULTS);
        String stored = rs.getString("objective_json");
        if (parse(stored, null) instanceof JsonNode node && node.isObject()) {
            node.properties().forEach(e -> objective.put(e.getKey(), e.getValue()));
        }
        item.put("objective", objective);
        item.put("objectiveDefaulted", stored == null);
        item.put("engines", null);
        item.put("enginesDefaulted", true);
        item.put("notes", rs.getString("notes"));
        item.put("createdAt", Wire.instant(rs, "created_at"));
        item.put("createdBy", rs.getString("created_by"));
        item.put("updatedAt", Wire.instant(rs, "updated_at"));
        item.put("updatedBy", rs.getString("updated_by"));
        return item;
    }

    /** The version and its engine rows land together or not at all. */
    private Map<String, Object> insert(Map<String, Object> v) {
        return tx.execute(status -> {
            String name = (String) v.get("name");
            Map<String, Object> item = db.sql("INSERT INTO strategies (name, version, instrument, "
                            + "source, timeframe, model_type, validation_scheme, features_json, "
                            + "windows_json, label_json, hyperparams_json, notes, created_by, "
                            + "updated_by, objective_json) "
                            + "SELECT ?::text, COALESCE((SELECT MAX(version) FROM strategies WHERE name = ?::text), 0) + 1, "
                            + "?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ? RETURNING *")
                    .params(name, name, v.get("instrument"), v.get("source"), v.get("timeframe"),
                            v.get("modelType"), v.get("validationScheme"),
                            json.writeValueAsString(v.get("features")),
                            json.writeValueAsString(v.get("windows")),
                            json.writeValueAsString(v.get("label")),
                            json.writeValueAsString(v.get("hyperparams")), v.get("notes"),
                            v.get("actor"), v.get("actor"),
                            json.writeValueAsString(v.get("objective")))
                    .query(this::item).single();
            @SuppressWarnings("unchecked")
            List<Map<String, Object>> engines = (List<Map<String, Object>>) v.get("engines");
            Map<String, Object> shown = new LinkedHashMap<>();
            for (Map<String, Object> e : engines) {
                db.sql("INSERT INTO strategy_engines (strategy_id, engine, enabled, settings_json) "
                                + "VALUES (?, ?, ?, ?)")
                        .params(Long.parseLong((String) item.get("id")), e.get("engine"),
                                e.get("enabled"), json.writeValueAsString(e.get("settings")))
                        .update();
                Map<String, Object> one = new LinkedHashMap<>();
                one.put("enabled", e.get("enabled"));
                one.put("settings", e.get("settings"));
                shown.put((String) e.get("engine"), one);
            }
            item.put("engines", shown);
            item.put("enginesDefaulted", false);
            return item;
        });
    }

    /** Which of {@code names} are data-only columns: stored for charts and rules, never a network input. */
    private List<String> dataOnlyFeatures(List<String> names) {
        if (names.isEmpty()) {
            return List.of();
        }
        String marks = String.join(", ", names.stream().map(n -> "?").toList());
        return db.sql("SELECT name FROM feature_definitions WHERE status = 'data-only' "
                        + "AND name IN (" + marks + ") ORDER BY name")
                .params(new ArrayList<Object>(names)).query(String.class).list();
    }
}
