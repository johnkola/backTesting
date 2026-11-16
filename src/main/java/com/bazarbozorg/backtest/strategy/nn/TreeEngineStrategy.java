package com.bazarbozorg.backtest.strategy.nn;

import com.google.gson.JsonObject;

import java.util.LinkedHashMap;
import java.util.Map;

/**
 * A tree engine's saved model, backtested here (B9, G35): XGBoost or LightGBM,
 * trained by the loader on the same candle features, labels and purged split as
 * {@code nn-feedforward}, and served by the same {@code /api/nn/predict_range}.
 * Only the engine and its settings differ, so this is the network's RPC client
 * with those two swapped.
 *
 * <p>The engine's own settings are not listed here: any parameter that is not
 * one of the shared inputs ({@link #SHARED_PARAMETERS}) is sent as
 * {@code engine_settings}, and the loader checks it against its registry
 * ({@code nn/engines.py}) and fills the defaults. One list, the runner's (R22).
 */
public class TreeEngineStrategy extends NeuralNetworkStrategy {

    private final String engine;
    private final String title;

    private TreeEngineStrategy(String engine, String title) {
        this.engine = engine;
        this.title = title;
    }

    public static TreeEngineStrategy xgboost() {
        return new TreeEngineStrategy("xgboost", "XGBoost");
    }

    public static TreeEngineStrategy lightgbm() {
        return new TreeEngineStrategy("lightgbm", "LightGBM");
    }

    @Override
    public String getName() {
        return engine;
    }

    @Override
    public String getDescription() {
        return title + " gradient boosting (Python loader); settings as the loader registers them";
    }

    @Override
    protected String engine() {
        return engine;
    }

    @Override
    public Map<String, String> getDefaultParameters() {
        Map<String, String> defaults = new LinkedHashMap<>();
        defaults.put("lookbackWindow", "20");
        defaults.put("forwardBars", "5");
        defaults.put("buyThreshold", "0.02");
        defaults.put("sellThreshold", "-0.02");
        defaults.put("trainSplitRatio", "0.8");
        return defaults;
    }

    @Override
    protected void addModelParams(JsonObject body) {
        body.add("engine_settings", engineSettings(engine, parameters));
    }

    /** Every parameter but the shared ones, as numbers; the loader checks them. */
    static JsonObject engineSettings(String engine, Map<String, String> parameters) {
        JsonObject settings = new JsonObject();
        if (parameters == null) {
            return settings;
        }
        for (Map.Entry<String, String> e : parameters.entrySet()) {
            if (SHARED_PARAMETERS.contains(e.getKey())) {
                continue;
            }
            try {
                settings.addProperty(e.getKey(), Double.parseDouble(e.getValue()));
            } catch (NumberFormatException ex) {
                throw new IllegalArgumentException(engine + " setting " + e.getKey()
                        + " must be a number, not " + e.getValue(), ex);
            }
        }
        return settings;
    }
}
