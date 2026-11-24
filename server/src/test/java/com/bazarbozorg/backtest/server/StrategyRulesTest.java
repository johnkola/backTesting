package com.bazarbozorg.backtest.server;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertNull;
import static org.junit.jupiter.api.Assertions.assertTrue;

import com.bazarbozorg.backtest.server.strategies.StrategyRules;
import java.util.List;
import java.util.Map;
import org.junit.jupiter.api.Test;
import tools.jackson.databind.JsonNode;
import tools.jackson.databind.ObjectMapper;
import tools.jackson.databind.node.ObjectNode;

/** What a saved strategy may say, without a database. */
class StrategyRulesTest {
    static final ObjectMapper JSON = new ObjectMapper();

    static final String REGISTRY = """
            [{"name": "mlp", "title": "Neural network", "losses": ["ce", "return", "sharpe", "calmar"],
              "settings": []},
             {"name": "xgboost", "title": "XGBoost", "losses": ["ce"],
              "settings": [{"key": "maxDepth", "label": "Max depth", "kind": "int", "min": 1, "max": 16}]}]""";
    static final String OPTIONS = """
            {"modelTypes": ["mlp"], "validationSchemes": ["walkforward"], "unrunnableTimeframes": []}""";

    static ObjectNode good() {
        return (ObjectNode) JSON.readTree("""
                {"name": " baseline ", "instrument": "QQQ", "source": "yahoo-adj", "timeframe": "D1",
                 "modelType": "mlp", "validationScheme": "walkforward",
                 "features": ["price_close", "vxn_close"],
                 "windows": {"train": {"from": "2003-01-01", "to": "2015-12-31"},
                             "validation": {"from": "2016-01-01", "to": "2021-12-31"},
                             "test": {"from": "2022-01-01", "to": "2026-06-30"}},
                 "label": {"kind": "forward_return_3class", "horizonBars": 5},
                 "hyperparams": {"lookbackWindow": "20", "numEpochs": 50, "dropoutRate": ""}}""");
    }

    static StrategyRules.Checked check(JsonNode body) {
        return StrategyRules.validate(body, JSON.readTree(REGISTRY), JSON.readTree(OPTIONS));
    }

    static void refused(JsonNode body, String... reasons) {
        StrategyRules.Checked c = check(body);
        assertFalse(c.ok());
        for (String reason : reasons) {
            assertTrue(c.errors().contains(reason), reason + " not in " + c.errors());
        }
    }

    static ObjectNode with(String path, String valueJson) {
        ObjectNode body = good();
        String[] keys = path.split("\\.");
        ObjectNode at = body;
        for (int i = 0; i < keys.length - 1; i++) {
            at = at.has(keys[i]) ? (ObjectNode) at.get(keys[i]) : at.putObject(keys[i]);
        }
        at.set(keys[keys.length - 1], JSON.readTree(valueJson));
        return body;
    }

    @Test
    void aWellFormedStrategyIsNormalisedWithEveryDefaultStated() {
        StrategyRules.Checked c = check(good());
        assertTrue(c.ok(), String.valueOf(c.errors()));
        Map<String, Object> v = c.value();
        assertEquals("baseline", v.get("name"));
        assertEquals("web", v.get("actor"));
        // Text from the form becomes a number, a blank is left out, and whole
        // numbers are stored whole: Python reads these rows.
        assertEquals("{\"lookbackWindow\":20,\"numEpochs\":50}",
                JSON.writeValueAsString(v.get("hyperparams")));
        assertEquals("{\"kind\":\"forward_return_3class\",\"horizonBars\":5}",
                JSON.writeValueAsString(v.get("label")));
        assertEquals("{\"loss\":\"ce\",\"classWeights\":false,\"positionThreshold\":null,"
                + "\"verdict\":\"return\",\"costBps\":5,\"park\":\"cash\",\"taxScore\":false,"
                + "\"taxRatePct\":null,\"execution\":\"signals\",\"leveragePct\":null,"
                + "\"hedgePct\":null,\"marginSpreadPct\":null,\"maintenancePct\":null,"
                + "\"longThreshold\":null}", JSON.writeValueAsString(v.get("objective")));
        // Absent engines: the network alone, one entry per registered engine.
        assertEquals("[{\"engine\":\"mlp\",\"enabled\":true,\"settings\":{}},"
                + "{\"engine\":\"xgboost\",\"enabled\":false,\"settings\":{}}]",
                JSON.writeValueAsString(v.get("engines")));
    }

    @Test
    void everyProblemIsReportedNotOnlyTheFirst() {
        StrategyRules.Checked c = check(JSON.readTree("{}"));
        assertEquals(List.of("name is required", "instrument is required", "source is required",
                "timeframe is required", "modelType is required", "validationScheme is required",
                "features must be a list of feature names", "train window is required",
                "validation window is required", "test window is required",
                "label definition is required"), c.errors());
        assertNull(c.value());
    }

    @Test
    void theRunnersListsDecideTheModelTypeAndTheScheme() {
        refused(with("modelType", "\"lstm\""), "unknown modelType: lstm (the runner has mlp)");
        refused(with("validationScheme", "\"kfold_time\""),
                "validationScheme kfold_time has no runner; only walkforward runs");
    }

    @Test
    void theWindowsMustExistBeInOrderAndNotOverlap() {
        refused(with("windows.validation", "{\"from\": \"2015-06-01\", \"to\": \"2021-12-31\"}"),
                "validation window must start after train ends — train runs to 2015-12-31 "
                        + "and validation starts 2015-06-01");
        refused(with("windows.train", "{\"from\": \"2003-02-30\", \"to\": \"2015-12-31\"}"),
                "train window has a date that does not exist");
        refused(with("windows.train", "{\"from\": \"2016\", \"to\": \"2015-12-31\"}"),
                "train window dates must be YYYY-MM-DD");
        refused(with("windows.test", "{\"from\": \"2026-01-01\", \"to\": \"2022-01-01\"}"),
                "test window ends before it starts");
        // A prediction window is optional, and held to the same order when present.
        refused(with("windows.prediction", "{\"from\": \"2026-06-30\", \"to\": \"2026-12-31\"}"),
                "prediction window must start after test ends — test runs to 2026-06-30 "
                        + "and prediction starts 2026-06-30");
    }

    @Test
    void aSlidingWindowNeedsItsYearsAndAnExpandingOneIsSavedAsNoSetting() {
        StrategyRules.Checked sliding = check(with("windows.window",
                "{\"kind\": \"sliding\", \"trainYears\": 10}"));
        assertEquals("{\"kind\":\"sliding\",\"trainYears\":10,\"stepYears\":1}",
                JSON.writeValueAsString(((Map<?, ?>) sliding.value().get("windows")).get("window")));
        StrategyRules.Checked expanding = check(with("windows.window", "{\"kind\": \"expanding\"}"));
        assertFalse(((Map<?, ?>) expanding.value().get("windows")).containsKey("window"));
        refused(with("windows.window", "{\"kind\": \"sliding\", \"trainYears\": 2.5}"),
                "a sliding window needs trainYears, a whole number from 1 to 50");
        refused(with("windows.window", "{\"kind\": \"expanding\", \"trainYears\": 3}"),
                "an expanding window takes no trainYears or stepYears");
        refused(with("windows.window", "{\"kind\": \"rolling\"}"),
                "windows.window.kind must be one of expanding, sliding");
    }

    @Test
    void featuresAreAnExplicitListWithNoRepeats() {
        refused(with("features", "[]"), "select at least one feature");
        refused(with("features", "[\"a\", \" \"]"), "every feature must be a non-empty name");
        refused(with("features", "[\"a\", \"b\", \"a\"]"), "feature listed twice: a");
    }

    @Test
    void hyperparametersAreHeldToTheirBounds() {
        refused(with("hyperparams.bogus", "1"), "unknown hyperparameter: bogus");
        refused(with("hyperparams.numEpochs", "\"many\""), "Epochs must be a number");
        refused(with("hyperparams.numEpochs", "1.5"), "Epochs must be a whole number");
        refused(with("hyperparams.learningRate", "2"), "Learning rate must be between 1e-8 and 1");
        refused(with("hyperparams", "{\"buyThreshold\": 0, \"sellThreshold\": 0}"),
                "Sell threshold must be below buy threshold, with HOLD between them");
    }

    @Test
    void aLabelsThresholdsTakeTheirDefaultsAndAreChecked() {
        StrategyRules.Checked c = check(with("label",
                "{\"kind\": \"next_bar_direction\", \"horizonBars\": \"1\"}"));
        assertEquals("{\"kind\":\"next_bar_direction\",\"horizonBars\":1,\"buyThreshold\":0.55,"
                + "\"sellThreshold\":0.45}", JSON.writeValueAsString(c.value().get("label")));
        refused(with("label", "{\"kind\": \"next_bar_direction\", \"horizonBars\": 1, "
                + "\"buyThreshold\": 0.4}"), "label.sellThreshold must be below label.buyThreshold");
        refused(with("label", "{\"kind\": \"nope\", \"horizonBars\": 0}"),
                "label.kind must be one of forward_return_3class, next_bar_direction, forward_return "
                        + "(the registered kinds)",
                "label.horizonBars must be a whole number between 1 and 100");
    }

    @Test
    void anObjectiveSettingThatWouldDoNothingIsRefused() {
        refused(with("objective", "{\"positionThreshold\": 0.6}"),
                "a position threshold only applies to a money-trained loss, not ce");
        refused(with("objective", "{\"loss\": \"sharpe\", \"classWeights\": true}"),
                "class weights only apply to the ce loss");
        refused(with("objective", "{\"taxRatePct\": 25}"),
                "a tax rate only applies when scoring after tax");
        refused(with("objective", "{\"taxScore\": true}"), "score after tax needs a tax rate");
        refused(with("objective", "{\"leveragePct\": 20}"),
                "objective.leveragePct only applies to execution 'margin'");
        refused(with("objective", "{\"surprise\": 1}"), "unknown objective setting: surprise");
        refused(with("objective", "{\"costBps\": 500}"),
                "objective.costBps must be a number between 0 and 100");

        // A money-trained loss takes the default cut; a tax rate is kept with its switch.
        Map<?, ?> o = (Map<?, ?>) check(with("objective", "{\"loss\": \"sharpe\", "
                + "\"taxScore\": true, \"taxRatePct\": \"25\", \"costBps\": \"2.5\"}"))
                .value().get("objective");
        assertEquals(0.5, o.get("positionThreshold"));
        assertEquals(25L, o.get("taxRatePct"));
        assertEquals(2.5, o.get("costBps"));
    }

    @Test
    void theOldTrainingSwitchesAreRefusedOnAndIgnoredOff() {
        StrategyRules.Checked on = check(with("objective", "{\"taxTrain\": true}"));
        assertTrue(on.errors().get(0).startsWith("objective.taxTrain (train with tax) is gone: "));
        StrategyRules.Checked off = check(with("objective", "{\"marginTrain\": false}"));
        assertTrue(off.ok());
        assertFalse(((Map<?, ?>) off.value().get("objective")).containsKey("marginTrain"));
    }

    @Test
    void strategyTwoFillsItsDialsAndRefusesMoreThanHalfTheHolding() {
        Map<?, ?> o = (Map<?, ?>) check(with("objective",
                "{\"execution\": \"margin\", \"loss\": \"sharpe\"}")).value().get("objective");
        assertEquals(20L, o.get("leveragePct"));
        assertEquals(1.5, o.get("marginSpreadPct"));
        assertEquals(0.75, o.get("longThreshold"));
        refused(with("objective", "{\"execution\": \"margin\", \"leveragePct\": 60}"),
                "objective.leveragePct must be above 0 and at most 50: the margin position never "
                        + "exceeds 50% of the holding");
        refused(with("objective", "{\"execution\": \"margin\", \"loss\": \"sharpe\", "
                + "\"positionThreshold\": 0.8}"),
                "objective.longThreshold must be above the position threshold and below 1");
        ObjectNode other = with("objective", "{\"execution\": \"margin\"}");
        other.put("instrument", "SPY");
        refused(other, "execution 'margin' (Strategy 2) runs on QQQ only, not SPY: its hedge is "
                + "PSQ, the inverse of QQQ");
    }

    @Test
    void enginesAreCheckedAgainstTheRunnersRegistry() {
        refused(with("engines", "{\"catboost\": {\"enabled\": true}}"),
                "engine catboost is not registered; the runner has mlp, xgboost",
                "switch at least one engine on");
        refused(with("engines", "{\"xgboost\": {\"enabled\": true, \"settings\": {\"maxDepth\": 99}}}"),
                "XGBoost Max depth must be between 1 and 16");
        refused(with("engines", "{\"xgboost\": {\"enabled\": true, \"settings\": {\"eta\": 1}}}"),
                "XGBoost has no setting eta");
        ObjectNode money = with("engines", "{\"mlp\": {\"enabled\": true}, \"xgboost\": {\"enabled\": true}}");
        money.set("objective", JSON.readTree("{\"loss\": \"sharpe\"}"));
        assertTrue(check(money).errors().get(0).startsWith("XGBoost cannot train on the 'sharpe' loss"));
        StrategyRules.Checked c = check(with("engines",
                "{\"xgboost\": {\"enabled\": true, \"settings\": {\"maxDepth\": \"6\"}}}"));
        assertEquals("[{\"engine\":\"mlp\",\"enabled\":false,\"settings\":{}},"
                + "{\"engine\":\"xgboost\",\"enabled\":true,\"settings\":{\"maxDepth\":6}}]",
                JSON.writeValueAsString(c.value().get("engines")));
    }

    @Test
    void aTargetIsFittedByItsOwnLoss() {
        ObjectNode body = with("label", "{\"kind\": \"forward_return\", \"horizonBars\": 5}");
        body.set("objective", JSON.readTree("{\"loss\": \"sharpe\"}"));
        refused(body, "a return target is fitted by its own loss; the money-trained 'sharpe' loss "
                + "trains a position and would ignore it. Use 'ce'");
        ObjectNode weights = with("label", "{\"kind\": \"forward_return\", \"horizonBars\": 5}");
        weights.set("objective", JSON.readTree("{\"classWeights\": true}"));
        refused(weights, "class weights need a class label, not a return target");
    }
}
