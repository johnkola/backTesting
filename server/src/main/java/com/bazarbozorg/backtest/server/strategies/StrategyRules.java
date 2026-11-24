package com.bazarbozorg.backtest.server.strategies;

import java.time.LocalDate;
import java.time.format.DateTimeParseException;
import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.LinkedHashSet;
import java.util.List;
import java.util.Map;
import java.util.Set;
import tools.jackson.databind.JsonNode;

/**
 * What a saved strategy may say (Architecture §8.1a, §8.2a): the shape and the
 * bounds of every choice a training run makes. Pure: no database, no clock.
 *
 * <p>Moved from {@code web/api/strategies.js} unchanged in what it accepts and
 * in the words of every refusal. What a change must keep:
 * <ul>
 *   <li><b>Every problem is reported, not only the first</b>, so the builder
 *       can show the whole form's worth of mistakes at once.</li>
 *   <li><b>The windows must not overlap and must be in time order</b>; a
 *       strategy where they do is refused here, on the server, not only in the
 *       form.</li>
 *   <li><b>Nothing in the objective is left blank.</b> Every default is filled,
 *       so a saved row states every value and stays meaningful if a default in
 *       code later changes. A setting that would silently do nothing (class
 *       weights on a money loss, a tax rate with the switch off, a margin dial
 *       under Strategy 1) is refused, never stored.</li>
 *   <li><b>No list of engines, model types or validation schemes lives
 *       here</b> (G54, R22): they are the runner's, read from the Python
 *       worker by the route and handed in.</li>
 *   <li><b>Whole numbers are stored as whole numbers</b> ({@code 5}, not
 *       {@code 5.0}): Python reads these rows, and a horizon of {@code 5.0}
 *       bars is not a count.</li>
 * </ul>
 *
 * <p>The spec tables mirror {@code python/nn/train.py} (hyperparameters),
 * {@code walkforward.py} (objective defaults) and
 * {@code python/nn/label_kinds.py} (label kinds).
 */
public final class StrategyRules {
    private StrategyRules() {
    }

    public static final int MAX_NAME_LEN = 80;
    public static final int MAX_NOTES_LEN = 500;
    static final String DEFAULT_ACTOR = "web";
    public static final List<String> WINDOWS = List.of("train", "validation", "test", "prediction");
    public static final List<String> REQUIRED_WINDOWS = List.of("train", "validation", "test");
    static final List<String> WINDOW_KINDS = List.of("expanding", "sliding");
    static final int MAX_WINDOW_YEARS = 50;

    static final List<String> LOSSES = List.of("ce", "return", "sharpe", "calmar");
    static final List<String> VERDICTS = List.of("return", "sharpe", "drawdown");
    static final List<String> PARKS = List.of("cash", "bills", "bonds");
    static final List<String> EXECUTIONS = List.of("signals", "margin");
    static final double DEFAULT_POSITION_THRESHOLD = 0.5;
    static final double DEFAULT_LONG_THRESHOLD = 0.75;
    static final int MAX_COST_BPS = 100;
    static final int MAX_TAX_RATE_PCT = 100;
    static final int MARGIN_CEILING_PCT = 50;

    private static final String D9_REASON = "engines learn signals only and know nothing about "
            + "costs, tax or margin (D9, R75): the profit step applies them afterwards, and "
            + "changing them recalculates a run without retraining";
    /** The objective's old training switches: off is ignored, on is refused, neither is stored. */
    private static final Map<String, String> TRAINING_SWITCHES = ordered(
            "taxTrain", "train with tax", "marginTrain", "train with margin");

    /** Hyperparameter bounds. */
    public static final Map<String, Map<String, Object>> PARAM_SPEC = new LinkedHashMap<>();
    /** What a run is trained to want and charged; every key a saved row states. */
    public static final Map<String, Object> OBJECTIVE_DEFAULTS = new LinkedHashMap<>();
    /** Strategy 2's dials, filled when absent. Percent of the core; spread in points over Fed funds. */
    public static final Map<String, Object> MARGIN_DEFAULTS = new LinkedHashMap<>();
    /** Each prediction target's naive baseline (R72, B8). */
    public static final Map<String, Object> NAIVE_BASELINES = new LinkedHashMap<>();
    /** The registered label kinds (G49, R71), each with its target and thresholds. */
    public static final Map<String, Map<String, Object>> LABEL_KINDS = new LinkedHashMap<>();
    /** What the form needs to draw the objective section from the same rules. */
    public static final Map<String, Object> OBJECTIVE_SPEC = new LinkedHashMap<>();

    static {
        param("lookbackWindow", "int", 1, 500, "Lookback window");
        param("forwardBars", "int", 1, 100, "Forward bars");
        param("buyThreshold", "float", 0, 1, "Buy threshold");
        param("sellThreshold", "float", -1, 0, "Sell threshold");
        param("hiddenLayerSize", "int", 1, 4096, "Hidden layer size");
        param("numHiddenLayers", "int", 1, 16, "Hidden layers");
        param("dropoutRate", "float", 0, 0.95, "Dropout rate");
        param("learningRate", "float", 1e-8, 1, "Learning rate");
        param("numEpochs", "int", 1, 10000, "Epochs");
        // Walk-forward only: blank is off, and Epochs is then the cap.
        param("earlyStoppingPatience", "int", 1, 1000, "Early stopping patience");
        param("batchSize", "int", 1, 8192, "Batch size");
        param("trainSplitRatio", "float", 0.05, 0.95, "Train split ratio");
        param("seed", "int", 0, 2147483647, "Seed");

        OBJECTIVE_DEFAULTS.put("loss", "ce");
        OBJECTIVE_DEFAULTS.put("classWeights", false);
        OBJECTIVE_DEFAULTS.put("positionThreshold", null);
        OBJECTIVE_DEFAULTS.put("verdict", "return");
        OBJECTIVE_DEFAULTS.put("costBps", 5);
        OBJECTIVE_DEFAULTS.put("park", "cash");
        OBJECTIVE_DEFAULTS.put("taxScore", false);
        OBJECTIVE_DEFAULTS.put("taxRatePct", null);
        OBJECTIVE_DEFAULTS.put("execution", "signals");
        OBJECTIVE_DEFAULTS.put("leveragePct", null);
        OBJECTIVE_DEFAULTS.put("hedgePct", null);
        OBJECTIVE_DEFAULTS.put("marginSpreadPct", null);
        OBJECTIVE_DEFAULTS.put("maintenancePct", null);
        OBJECTIVE_DEFAULTS.put("longThreshold", null);

        MARGIN_DEFAULTS.put("leveragePct", 20);
        MARGIN_DEFAULTS.put("hedgePct", 20);
        MARGIN_DEFAULTS.put("marginSpreadPct", 1.5);
        MARGIN_DEFAULTS.put("maintenancePct", 30);

        NAIVE_BASELINES.put("class", "the training majority class");
        NAIVE_BASELINES.put("direction", "always up");
        NAIVE_BASELINES.put("return", "the historical mean return");

        labelKind("forward_return_3class", "class", new LinkedHashMap<>());
        Map<String, Object> direction = new LinkedHashMap<>();
        direction.put("buyThreshold", labelParam(0, 1, 0.55, "P(up) that calls BUY"));
        direction.put("sellThreshold", labelParam(0, 1, 0.45, "P(up) that calls SELL"));
        labelKind("next_bar_direction", "direction", direction);
        Map<String, Object> forward = new LinkedHashMap<>();
        forward.put("minReturn", labelParam(0, 0.99, 0, "Forecast return a call must clear"));
        labelKind("forward_return", "return", forward);

        OBJECTIVE_SPEC.put("defaults", OBJECTIVE_DEFAULTS);
        OBJECTIVE_SPEC.put("losses", LOSSES);
        OBJECTIVE_SPEC.put("verdicts", VERDICTS);
        OBJECTIVE_SPEC.put("parks", PARKS);
        OBJECTIVE_SPEC.put("maxCostBps", MAX_COST_BPS);
        OBJECTIVE_SPEC.put("maxTaxRatePct", MAX_TAX_RATE_PCT);
        OBJECTIVE_SPEC.put("defaultPositionThreshold", DEFAULT_POSITION_THRESHOLD);
        OBJECTIVE_SPEC.put("executions", EXECUTIONS);
        OBJECTIVE_SPEC.put("marginDefaults", MARGIN_DEFAULTS);
        OBJECTIVE_SPEC.put("defaultLongThreshold", DEFAULT_LONG_THRESHOLD);
        OBJECTIVE_SPEC.put("marginCeilingPct", MARGIN_CEILING_PCT);
    }

    private static void param(String key, String kind, Number min, Number max, String label) {
        Map<String, Object> spec = new LinkedHashMap<>();
        spec.put("kind", kind);
        spec.put("min", min);
        spec.put("max", max);
        spec.put("label", label);
        PARAM_SPEC.put(key, spec);
    }

    private static Map<String, Object> labelParam(Number min, Number max, Number dflt, String label) {
        Map<String, Object> spec = new LinkedHashMap<>();
        spec.put("min", min);
        spec.put("max", max);
        spec.put("default", dflt);
        spec.put("label", label);
        return spec;
    }

    private static void labelKind(String kind, String target, Map<String, Object> params) {
        Map<String, Object> spec = new LinkedHashMap<>();
        spec.put("target", target);
        spec.put("params", params);
        LABEL_KINDS.put(kind, spec);
    }

    private static Map<String, String> ordered(String... pairs) {
        Map<String, String> out = new LinkedHashMap<>();
        for (int i = 0; i < pairs.length; i += 2) {
            out.put(pairs[i], pairs[i + 1]);
        }
        return out;
    }

    /** A checked strategy: either its normalised value, or every problem found. */
    public record Checked(Map<String, Object> value, List<String> errors) {
        public boolean ok() {
            return errors.isEmpty();
        }
    }

    // --- The loose reading a form's values need ----------------------------------

    private static boolean absent(JsonNode n) {
        return n == null || n.isMissingNode() || n.isNull();
    }

    private static boolean blank(JsonNode n) {
        return absent(n) || (n.isString() && n.asString().isEmpty());
    }

    /** A number as a form sends it: a number, or text that reads as one. NaN when it is neither. */
    static double number(JsonNode n) {
        if (n == null || n.isMissingNode()) {
            return Double.NaN;
        }
        if (n.isNumber()) {
            return n.asDouble();
        }
        if (n.isNull()) {
            return 0;
        }
        if (n.isBoolean()) {
            return n.asBoolean() ? 1 : 0;
        }
        if (n.isString()) {
            String s = n.asString().trim();
            if (s.isEmpty()) {
                return 0;
            }
            try {
                return Double.parseDouble(s);
            } catch (NumberFormatException e) {
                return Double.NaN;
            }
        }
        return Double.NaN;
    }

    private static boolean finite(double n) {
        return !Double.isNaN(n) && !Double.isInfinite(n);
    }

    private static boolean whole(double n) {
        return finite(n) && n == Math.rint(n);
    }

    /** {@code 5}, not {@code 5.0}, for a whole number; the value as it is otherwise. */
    static Number tidy(double n) {
        if (whole(n) && Math.abs(n) < 1e15) {
            return (long) n;
        }
        return n;
    }

    private static Number tidy(Object n) {
        return n == null ? null : tidy(((Number) n).doubleValue());
    }

    private static String text(JsonNode n) {
        return n != null && n.isString() ? n.asString().trim() : "";
    }

    /** A bound as the refusal prints it: {@code 1}, {@code 0.95}, {@code 1e-8}. */
    private static String shown(Object n) {
        if (!(n instanceof Number num)) {
            return String.valueOf(n);
        }
        return String.valueOf(tidy(num.doubleValue())).replace(".0E", "e").replace("E", "e");
    }

    // --- The strategy -------------------------------------------------------------

    /**
     * Validates a strategy body. {@code registry} is the runner's engines and
     * {@code options} its model types and validation schemes; either may be
     * null, and the fields they would hold are then only required to be present.
     */
    public static Checked validate(JsonNode body, JsonNode registry, JsonNode options) {
        List<String> errors = new ArrayList<>();
        JsonNode b = body != null && body.isObject() ? body : null;

        String name = text(field(b, "name"));
        if (name.isEmpty()) {
            errors.add("name is required");
        } else if (name.length() > MAX_NAME_LEN) {
            errors.add("name must be " + MAX_NAME_LEN + " characters or fewer");
        }
        String instrument = text(field(b, "instrument"));
        if (instrument.isEmpty()) {
            errors.add("instrument is required");
        }
        String source = text(field(b, "source"));
        if (source.isEmpty()) {
            errors.add("source is required");
        }
        String timeframe = text(field(b, "timeframe"));
        if (timeframe.isEmpty()) {
            errors.add("timeframe is required");
        }
        String modelType = text(field(b, "modelType"));
        if (modelType.isEmpty()) {
            errors.add("modelType is required");
        } else if (options != null && !strings(options.get("modelTypes")).contains(modelType)) {
            errors.add("unknown modelType: " + modelType + " (the runner has "
                    + String.join(", ", strings(options.get("modelTypes"))) + ")");
        }
        String scheme = text(field(b, "validationScheme"));
        if (scheme.isEmpty()) {
            errors.add("validationScheme is required");
        } else if (options != null && !strings(options.get("validationSchemes")).contains(scheme)) {
            errors.add("validationScheme " + scheme + " has no runner; only "
                    + String.join(", ", strings(options.get("validationSchemes"))) + " runs");
        }

        // A feature subset, picked from the catalogue. An empty list is refused
        // rather than read as "all": the list is an explicit, recorded choice.
        List<String> features = new ArrayList<>();
        JsonNode rawFeatures = field(b, "features");
        if (rawFeatures == null || !rawFeatures.isArray()) {
            errors.add("features must be a list of feature names");
        } else if (rawFeatures.isEmpty()) {
            errors.add("select at least one feature");
        } else {
            boolean named = true;
            for (JsonNode f : rawFeatures) {
                named &= f.isString() && !f.asString().trim().isEmpty();
            }
            if (!named) {
                errors.add("every feature must be a non-empty name");
            } else {
                Set<String> seen = new LinkedHashSet<>();
                Set<String> dupes = new LinkedHashSet<>();
                for (JsonNode f : rawFeatures) {
                    String one = f.asString().trim();
                    features.add(one);
                    if (!seen.add(one)) {
                        dupes.add(one);
                    }
                }
                if (!dupes.isEmpty()) {
                    errors.add("feature listed twice: " + String.join(", ", dupes));
                }
            }
        }

        // The windows, then their order. Order is checked only between windows
        // that parsed, so a malformed date reports as itself.
        Map<String, Object> windows = new LinkedHashMap<>();
        JsonNode rawWindows = field(b, "windows");
        for (String w : WINDOWS) {
            JsonNode raw = field(rawWindows, w);
            if (absent(raw)) {
                if (REQUIRED_WINDOWS.contains(w)) {
                    errors.add(w + " window is required");
                }
                continue;
            }
            Map<String, Object> parsed = readWindow(w, raw, errors);
            if (parsed != null) {
                windows.put(w, parsed);
            }
        }
        readTrainingWindow(field(rawWindows, "window"), windows, errors);

        List<String> present = WINDOWS.stream().filter(windows::containsKey).toList();
        for (int i = 0; i < present.size() - 1; i++) {
            String a = present.get(i);
            String c = present.get(i + 1);
            String aTo = (String) ((Map<?, ?>) windows.get(a)).get("to");
            String cFrom = (String) ((Map<?, ?>) windows.get(c)).get("from");
            if (cFrom.compareTo(aTo) <= 0) {
                errors.add(c + " window must start after " + a + " ends — " + a + " runs to "
                        + aTo + " and " + c + " starts " + cFrom);
            }
        }

        Map<String, Object> label = readLabel(field(b, "label"), errors);
        Map<String, Object> hyperparams = readHyperparams(field(b, "hyperparams"), errors);
        Map<String, Object> objective = readObjective(field(b, "objective"), errors);

        // G49: a direction or return target is fitted by its own loss.
        String target = label == null ? "class"
                : (String) LABEL_KINDS.get((String) label.get("kind")).get("target");
        if (objective != null && !target.equals("class") && !"ce".equals(objective.get("loss"))) {
            errors.add("a " + target + " target is fitted by its own loss; the money-trained '"
                    + objective.get("loss") + "' loss trains a position and would ignore it. Use 'ce'");
        }
        if (objective != null && target.equals("return")
                && Boolean.TRUE.equals(objective.get("classWeights"))) {
            errors.add("class weights need a class label, not a return target");
        }
        List<Map<String, Object>> engines = readEngines(field(b, "engines"), registry,
                objective == null ? null : (String) objective.get("loss"), errors);
        // Strategy 2 holds a QQQ core and hedges with PSQ, QQQ's inverse.
        if (objective != null && "margin".equals(objective.get("execution"))
                && !instrument.isEmpty() && !instrument.equals("QQQ")) {
            errors.add("execution 'margin' (Strategy 2) runs on QQQ only, not " + instrument
                    + ": its hedge is PSQ, the inverse of QQQ");
        }

        String notes = null;
        JsonNode rawNotes = field(b, "notes");
        if (!blank(rawNotes)) {
            if (!rawNotes.isString()) {
                errors.add("notes must be text");
            } else if (rawNotes.asString().length() > MAX_NOTES_LEN) {
                errors.add("notes must be " + MAX_NOTES_LEN + " characters or fewer");
            } else {
                notes = rawNotes.asString().trim();
            }
        }

        if (!errors.isEmpty()) {
            return new Checked(null, errors);
        }
        Map<String, Object> value = new LinkedHashMap<>();
        value.put("name", name);
        value.put("instrument", instrument);
        value.put("source", source);
        value.put("timeframe", timeframe);
        value.put("modelType", modelType);
        value.put("validationScheme", scheme);
        value.put("features", features);
        value.put("windows", windows);
        value.put("label", label);
        value.put("hyperparams", hyperparams);
        value.put("objective", objective);
        value.put("notes", notes);
        value.put("engines", engines);
        String actor = text(field(b, "actor"));
        value.put("actor", actor.isEmpty() ? DEFAULT_ACTOR : actor);
        return new Checked(value, errors);
    }

    private static JsonNode field(JsonNode object, String name) {
        return object != null && object.isObject() ? object.get(name) : null;
    }

    private static List<String> strings(JsonNode array) {
        List<String> out = new ArrayList<>();
        if (array != null && array.isArray()) {
            array.forEach(n -> out.add(n.asString()));
        }
        return out;
    }

    /** One window as {@code {from, to}}, inclusive ISO dates, or null with the errors added. */
    private static Map<String, Object> readWindow(String label, JsonNode raw, List<String> errors) {
        if (!raw.isObject()) {
            errors.add(label + " window must have a from and a to date");
            return null;
        }
        String from = raw.path("from").isString() ? raw.path("from").asString() : "";
        String to = raw.path("to").isString() ? raw.path("to").asString() : "";
        if (!from.matches("\\d{4}-\\d{2}-\\d{2}") || !to.matches("\\d{4}-\\d{2}-\\d{2}")) {
            errors.add(label + " window dates must be YYYY-MM-DD");
            return null;
        }
        // A day that does not exist ('2003-02-30') is refused, never rolled
        // over to a date nobody typed.
        try {
            LocalDate.parse(from);
            LocalDate.parse(to);
        } catch (DateTimeParseException e) {
            errors.add(label + " window has a date that does not exist");
            return null;
        }
        if (from.compareTo(to) > 0) {
            errors.add(label + " window ends before it starts");
            return null;
        }
        Map<String, Object> out = new LinkedHashMap<>();
        out.put("from", from);
        out.put("to", to);
        return out;
    }

    /**
     * How each fold's training window is cut (C6): absent or {@code expanding}
     * is saved as no setting, so the two spellings of the default are one
     * strategy; {@code sliding} needs its years. Shape and bounds only:
     * whether it fits the windows is the runner's answer.
     */
    private static void readTrainingWindow(JsonNode raw, Map<String, Object> windows,
            List<String> errors) {
        if (absent(raw)) {
            return;
        }
        String kind = raw.isObject() && raw.path("kind").isString() ? raw.path("kind").asString() : null;
        if (kind == null || !WINDOW_KINDS.contains(kind)) {
            errors.add("windows.window.kind must be one of " + String.join(", ", WINDOW_KINDS));
            return;
        }
        JsonNode trainYears = raw.get("trainYears");
        JsonNode stepYears = raw.get("stepYears");
        if (kind.equals("sliding")) {
            boolean trainOk = years(trainYears);
            boolean stepOk = absent(stepYears) || years(stepYears);
            if (!trainOk) {
                errors.add("a sliding window needs trainYears, a whole number from 1 to "
                        + MAX_WINDOW_YEARS);
            }
            if (!stepOk) {
                errors.add("stepYears must be a whole number from 1 to " + MAX_WINDOW_YEARS);
            }
            if (trainOk && stepOk) {
                Map<String, Object> window = new LinkedHashMap<>();
                window.put("kind", "sliding");
                window.put("trainYears", trainYears.asLong());
                window.put("stepYears", absent(stepYears) ? 1L : stepYears.asLong());
                windows.put("window", window);
            }
        } else if (!absent(trainYears) || !absent(stepYears)) {
            errors.add("an expanding window takes no trainYears or stepYears");
        }
    }

    private static boolean years(JsonNode n) {
        return n != null && n.isNumber() && whole(n.asDouble())
                && n.asDouble() >= 1 && n.asDouble() <= MAX_WINDOW_YEARS;
    }

    private static Map<String, Object> readLabel(JsonNode raw, List<String> errors) {
        if (raw == null || !raw.isObject()) {
            errors.add("label definition is required");
            return null;
        }
        String kind = text(raw.get("kind"));
        double horizon = number(raw.get("horizonBars"));
        boolean known = LABEL_KINDS.containsKey(kind);
        if (!known) {
            errors.add("label.kind must be one of " + String.join(", ", LABEL_KINDS.keySet())
                    + " (the registered kinds)");
        }
        if (!whole(horizon) || horizon < 1 || horizon > 100) {
            errors.add("label.horizonBars must be a whole number between 1 and 100");
        }
        if (!known || !whole(horizon)) {
            return null;
        }
        Map<String, Object> label = new LinkedHashMap<>();
        label.put("kind", kind);
        label.put("horizonBars", tidy(horizon));
        @SuppressWarnings("unchecked")
        Map<String, Map<String, Object>> params =
                (Map<String, Map<String, Object>>) LABEL_KINDS.get(kind).get("params");
        for (Map.Entry<String, Map<String, Object>> p : params.entrySet()) {
            Map<String, Object> spec = p.getValue();
            JsonNode given = raw.get(p.getKey());
            double n = blank(given) ? ((Number) spec.get("default")).doubleValue() : number(given);
            double min = ((Number) spec.get("min")).doubleValue();
            double max = ((Number) spec.get("max")).doubleValue();
            if (!finite(n) || n < min || n > max) {
                errors.add("label." + p.getKey() + " must be between " + shown(spec.get("min"))
                        + " and " + shown(spec.get("max")));
            } else {
                label.put(p.getKey(), tidy(n));
            }
        }
        if ("direction".equals(LABEL_KINDS.get(kind).get("target"))
                && label.containsKey("sellThreshold") && label.containsKey("buyThreshold")
                && !(((Number) label.get("sellThreshold")).doubleValue()
                        < ((Number) label.get("buyThreshold")).doubleValue())) {
            errors.add("label.sellThreshold must be below label.buyThreshold");
        }
        return label;
    }

    private static Map<String, Object> readHyperparams(JsonNode raw, List<String> errors) {
        Map<String, Object> out = new LinkedHashMap<>();
        if (absent(raw)) {
            return out;
        }
        if (!raw.isObject()) {
            errors.add("hyperparams must be an object");
            return out;
        }
        for (Map.Entry<String, JsonNode> e : raw.properties()) {
            Map<String, Object> spec = PARAM_SPEC.get(e.getKey());
            if (spec == null) {
                errors.add("unknown hyperparameter: " + e.getKey());
                continue;
            }
            if (blank(e.getValue())) {
                continue;
            }
            Number n = bounded(e.getValue(), spec, (String) spec.get("label"), errors);
            if (n != null) {
                out.put(e.getKey(), n);
            }
        }
        // The bounds force buy >= 0 >= sell. Both at zero is the one case they
        // allow, and it collapses the HOLD band, so it is refused.
        if (out.containsKey("buyThreshold") && out.containsKey("sellThreshold")
                && ((Number) out.get("sellThreshold")).doubleValue()
                        >= ((Number) out.get("buyThreshold")).doubleValue()) {
            errors.add("Sell threshold must be below buy threshold, with HOLD between them");
        }
        return out;
    }

    /** A setting held to its spec ({@code kind}, {@code min}, {@code max}); null with the error added. */
    private static Number bounded(JsonNode value, Map<String, Object> spec, String label,
            List<String> errors) {
        double n = number(value);
        if (!finite(n)) {
            errors.add(label + " must be a number");
            return null;
        }
        if ("int".equals(spec.get("kind")) && !whole(n)) {
            errors.add(label + " must be a whole number");
            return null;
        }
        double min = ((Number) spec.get("min")).doubleValue();
        double max = ((Number) spec.get("max")).doubleValue();
        if (n < min || n > max) {
            errors.add(label + " must be between " + shown(spec.get("min")) + " and "
                    + shown(spec.get("max")));
            return null;
        }
        return tidy(n);
    }

    /** A dial as a form sends it: null when blank, a number when it reads as one, else NaN. */
    private static Double dial(JsonNode n) {
        if (blank(n)) {
            return null;
        }
        return n.isNumber() || n.isString() ? number(n) : Double.NaN;
    }

    /**
     * Validates and completes {@code objective}; absent fields take their
     * default. Null, with the errors added, when anything is wrong.
     */
    static Map<String, Object> readObjective(JsonNode raw, List<String> errors) {
        if (!absent(raw) && !raw.isObject()) {
            errors.add("objective must be an object");
            return null;
        }
        int before = errors.size();
        if (!absent(raw)) {
            for (Map.Entry<String, JsonNode> e : raw.properties()) {
                String key = e.getKey();
                if (TRAINING_SWITCHES.containsKey(key)) {
                    if (e.getValue().isBoolean() && e.getValue().asBoolean()) {
                        errors.add("objective." + key + " (" + TRAINING_SWITCHES.get(key)
                                + ") is gone: " + D9_REASON);
                    } else if (!e.getValue().isBoolean()) {
                        errors.add("objective." + key + " must be false");
                    }
                } else if (!OBJECTIVE_DEFAULTS.containsKey(key)) {
                    errors.add("unknown objective setting: " + key);
                }
            }
        }
        String loss = choice(raw, "loss", LOSSES, errors);
        String verdict = choice(raw, "verdict", VERDICTS, errors);
        String park = choice(raw, "park", PARKS, errors);
        Boolean classWeights = flag(raw, "classWeights", errors);

        JsonNode rawCost = field(raw, "costBps");
        double cost = rawCost == null ? 5
                : rawCost.isNumber() ? rawCost.asDouble()
                : rawCost.isString() && !rawCost.asString().trim().isEmpty() ? number(rawCost)
                : Double.NaN;
        if (!finite(cost) || cost < 0 || cost > MAX_COST_BPS) {
            errors.add("objective.costBps must be a number between 0 and " + MAX_COST_BPS);
        }

        Double threshold = dial(field(raw, "positionThreshold"));
        if (threshold != null && (!finite(threshold) || threshold <= 0 || threshold >= 1)) {
            errors.add("objective.positionThreshold must be between 0 and 1, exclusive");
        }

        // Score after tax: a switch and a rate. A rate with the switch off
        // would be recorded and never applied, so it is refused.
        Boolean taxScore = flag(raw, "taxScore", errors);
        Double taxRate = dial(field(raw, "taxRatePct"));
        if (taxRate != null && (!finite(taxRate) || taxRate <= 0 || taxRate > MAX_TAX_RATE_PCT)) {
            errors.add("objective.taxRatePct must be above 0 and at most " + MAX_TAX_RATE_PCT);
        } else if (Boolean.TRUE.equals(taxScore) && taxRate == null) {
            errors.add("score after tax needs a tax rate");
        } else if (Boolean.FALSE.equals(taxScore) && taxRate != null) {
            errors.add("a tax rate only applies when scoring after tax");
        }
        if ("ce".equals(loss)) {
            if (threshold != null) {
                errors.add("a position threshold only applies to a money-trained loss, not ce");
            }
        } else if (loss != null) {
            if (Boolean.TRUE.equals(classWeights)) {
                errors.add("class weights only apply to the ce loss");
            }
            if (threshold == null) {
                threshold = DEFAULT_POSITION_THRESHOLD;
            }
        }

        Map<String, Object> margin = readExecution(raw, loss, threshold, errors);
        if (errors.size() > before) {
            return null;
        }
        Map<String, Object> out = new LinkedHashMap<>();
        out.put("loss", loss);
        out.put("classWeights", classWeights);
        out.put("positionThreshold", tidy(threshold));
        out.put("verdict", verdict);
        out.put("costBps", tidy(cost));
        out.put("park", park);
        out.put("taxScore", taxScore);
        out.put("taxRatePct", tidy(taxRate));
        out.putAll(margin);
        return out;
    }

    /** One of {@code allowed}, the default when absent; null with the error added otherwise. */
    private static String choice(JsonNode raw, String key, List<String> allowed, List<String> errors) {
        JsonNode given = field(raw, key);
        String value = given == null ? (String) OBJECTIVE_DEFAULTS.get(key)
                : given.isString() ? given.asString() : null;
        if (value == null || !allowed.contains(value)) {
            errors.add("objective." + key + " must be one of " + String.join(", ", allowed));
            return null;
        }
        return value;
    }

    private static Boolean flag(JsonNode raw, String key, List<String> errors) {
        JsonNode given = field(raw, key);
        if (given == null) {
            return (Boolean) OBJECTIVE_DEFAULTS.get(key);
        }
        if (!given.isBoolean()) {
            errors.add("objective." + key + " must be true or false");
            return null;
        }
        return given.asBoolean();
    }

    /**
     * Strategy 1 or 2, and Strategy 2's dials. Under Strategy 1 every dial
     * must be null: a leverage saved on a run that never borrowed would read
     * as one that did. Under Strategy 2 absent dials take their defaults, and
     * a leverage or hedge past 50% of the holding is refused, not clipped.
     */
    private static Map<String, Object> readExecution(JsonNode raw, String loss, Double threshold,
            List<String> errors) {
        List<String> dials = List.of("leveragePct", "hedgePct", "marginSpreadPct",
                "maintenancePct", "longThreshold");
        Map<String, Object> out = new LinkedHashMap<>();
        JsonNode given = field(raw, "execution");
        String execution = given == null ? "signals" : given.isString() ? given.asString() : null;
        if (execution == null || !EXECUTIONS.contains(execution)) {
            errors.add("objective.execution must be one of " + String.join(", ", EXECUTIONS));
            return out;
        }
        out.put("execution", execution);
        if (execution.equals("signals")) {
            for (String d : dials) {
                if (dial(field(raw, d)) != null) {
                    errors.add("objective." + d + " only applies to execution 'margin'");
                }
                out.put(d, null);
            }
            return out;
        }
        Map<String, Double> values = new LinkedHashMap<>();
        for (Map.Entry<String, Object> d : MARGIN_DEFAULTS.entrySet()) {
            Double v = dial(field(raw, d.getKey()));
            double value = v == null ? ((Number) d.getValue()).doubleValue() : v;
            values.put(d.getKey(), value);
            out.put(d.getKey(), finite(value) ? tidy(value) : null);
            if (!finite(value)) {
                errors.add("objective." + d.getKey() + " must be a number");
            }
        }
        for (String d : List.of("leveragePct", "hedgePct")) {
            double v = values.get(d);
            if (finite(v) && (v <= 0 || v > MARGIN_CEILING_PCT)) {
                errors.add("objective." + d + " must be above 0 and at most " + MARGIN_CEILING_PCT
                        + ": the margin position never exceeds 50% of the holding");
            }
        }
        double spread = values.get("marginSpreadPct");
        if (finite(spread) && (spread < 0 || spread > 20)) {
            errors.add("objective.marginSpreadPct must be between 0 and 20 points over Fed funds");
        }
        double maintenance = values.get("maintenancePct");
        if (finite(maintenance) && (maintenance <= 0 || maintenance >= 100)) {
            errors.add("objective.maintenancePct must be between 0 and 100, exclusive");
        }
        Double asked = dial(field(raw, "longThreshold"));
        if ("ce".equals(loss)) {
            if (asked != null) {
                errors.add("a long threshold only applies to a money-trained loss, not ce");
            }
            out.put("longThreshold", null);
        } else {
            double longAt = asked == null ? DEFAULT_LONG_THRESHOLD : asked;
            out.put("longThreshold", finite(longAt) ? tidy(longAt) : null);
            if (!finite(longAt) || longAt >= 1 || (threshold != null && longAt <= threshold)) {
                errors.add("objective.longThreshold must be above the position threshold and below 1");
            }
        }
        return out;
    }

    /**
     * A strategy's engines (B9, R21), checked against the runner's registry:
     * every engine it can run, with its settings and the losses it can train
     * on. {@code raw} is {@code {name: {enabled, settings}}}; absent, the
     * strategy is the network alone. One entry per registered engine, in
     * registry order, or null when the registry is missing.
     */
    static List<Map<String, Object>> readEngines(JsonNode raw, JsonNode registry, String loss,
            List<String> errors) {
        if (registry == null || !registry.isArray()) {
            if (!absent(raw)) {
                errors.add("the engine registry is unavailable (the loader did not answer), "
                        + "so the engines cannot be checked");
            }
            return null;
        }
        if (!absent(raw) && !raw.isObject()) {
            errors.add("engines must be an object of {enabled, settings} by engine name");
            return null;
        }
        List<String> names = new ArrayList<>();
        registry.forEach(e -> names.add(e.path("name").asString()));
        if (!absent(raw)) {
            for (String name : raw.propertyNames()) {
                if (!names.contains(name)) {
                    errors.add("engine " + name + " is not registered; the runner has "
                            + String.join(", ", names));
                }
            }
        }
        List<Map<String, Object>> out = new ArrayList<>();
        boolean any = false;
        for (JsonNode e : registry) {
            String name = e.path("name").asString();
            String title = e.path("title").asString();
            JsonNode g = absent(raw) ? null : raw.get(name);
            boolean enabled = absent(raw) ? name.equals("mlp")
                    : g != null && g.isObject() && g.path("enabled").isBoolean()
                            && g.path("enabled").asBoolean();
            Map<String, Object> settings = new LinkedHashMap<>();
            JsonNode rawSettings = g != null && g.isObject() ? g.get("settings") : null;
            if (rawSettings != null && rawSettings.isObject()) {
                for (Map.Entry<String, JsonNode> s : rawSettings.properties()) {
                    if (blank(s.getValue())) {
                        continue;
                    }
                    JsonNode spec = null;
                    for (JsonNode candidate : e.path("settings")) {
                        if (candidate.path("key").asString("").equals(s.getKey())) {
                            spec = candidate;
                        }
                    }
                    if (spec == null) {
                        errors.add(title + " has no setting " + s.getKey());
                        continue;
                    }
                    Map<String, Object> bounds = new LinkedHashMap<>();
                    bounds.put("kind", spec.path("kind").asString(""));
                    bounds.put("min", spec.path("min").asDouble());
                    bounds.put("max", spec.path("max").asDouble());
                    Number n = bounded(s.getValue(), bounds,
                            title + " " + spec.path("label").asString(), errors);
                    if (n != null) {
                        settings.put(s.getKey(), n);
                    }
                }
            }
            // Refused, never swapped (B9): the money-trained losses are the network's.
            if (enabled && loss != null && !strings(e.get("losses")).contains(loss)) {
                errors.add(title + " cannot train on the '" + loss + "' loss: the money-trained "
                        + "losses are written for the network, whose position head they "
                        + "differentiate through; " + title + " trains on the class label ('ce'). "
                        + "Switch it off or use 'ce'");
            }
            Map<String, Object> entry = new LinkedHashMap<>();
            entry.put("engine", name);
            entry.put("enabled", enabled);
            entry.put("settings", settings);
            out.add(entry);
            any |= enabled;
        }
        if (!any) {
            errors.add("switch at least one engine on");
        }
        return out;
    }
}
