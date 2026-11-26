package com.bazarbozorg.backtest.strategy.nn;

import com.bazarbozorg.backtest.strategy.StrategyRegistry;
import com.google.gson.JsonObject;
import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Test;

import java.io.IOException;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.LinkedHashSet;
import java.util.List;
import java.util.Map;
import java.util.Set;
import java.util.regex.Matcher;
import java.util.regex.Pattern;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertNull;
import static org.junit.jupiter.api.Assertions.assertTrue;

/**
 * The train request's field names (step 5): one table, read by the engine's own
 * request and, through {@code GET /api/strategies}, by the Train page.
 *
 * <p>The page used to derive the names by converting the case, and two of the
 * network's did not match the loader's ({@code hidden_layer_size} for
 * {@code hidden_size}, {@code num_hidden_layers} for {@code num_hidden}), so a
 * changed setting trained at the default. It also sent no engine, so XGBoost
 * and LightGBM trained the network. These tests hold the table to the
 * strategies' parameters and to the loader's own request.
 */
class TrainFieldsTest {

    private static NeuralNetworkStrategy strategy(String name) {
        return (NeuralNetworkStrategy) StrategyRegistry.getInstance().getStrategy(name).orElseThrow();
    }

    /** The fields of {@code TrainRequest} in {@code python/nn/nn_api.py}. */
    private static Set<String> loaderFields() throws IOException {
        String src = Files.readString(Path.of("python/nn/nn_api.py"));
        int from = src.indexOf("class TrainRequest(");
        int to = src.indexOf("\nclass ", from + 1);
        Matcher m = Pattern.compile("(?m)^    (\\w+):").matcher(src.substring(from, to));
        Set<String> out = new LinkedHashSet<>();
        while (m.find()) out.add(m.group(1));
        return out;
    }

    @Test
    @DisplayName("each engine trains as itself")
    void eachEngineTrainsAsItself() {
        assertEquals("mlp", strategy("nn-feedforward").trainingEngine());
        assertEquals("xgboost", strategy("xgboost").trainingEngine());
        assertEquals("lightgbm", strategy("lightgbm").trainingEngine());
    }

    @Test
    @DisplayName("every parameter of the network has its field, by the loader's name")
    void theNetworksParametersAllHaveAField() {
        NeuralNetworkStrategy nn = strategy("nn-feedforward");
        Map<String, String> fields = nn.trainFields();
        assertEquals(nn.getDefaultParameters().keySet(), fields.keySet());
        assertEquals("hidden_size", fields.get("hiddenLayerSize"));
        assertEquals("num_hidden", fields.get("numHiddenLayers"));
        assertNull(nn.otherParametersField());
    }

    @Test
    @DisplayName("a tree engine names the shared five; its own settings go as engine_settings")
    void aTreeEngineSendsItsOwnAsEngineSettings() {
        for (String name : List.of("xgboost", "lightgbm")) {
            NeuralNetworkStrategy tree = strategy(name);
            assertEquals(NeuralNetworkStrategy.SHARED_FIELDS, tree.trainFields(), name);
            assertEquals("engine_settings", tree.otherParametersField(), name);
        }
    }

    @Test
    @DisplayName("every field named is one the loader's train request has")
    void everyFieldIsTheLoaders() throws IOException {
        Set<String> loader = loaderFields();
        assertTrue(loader.containsAll(Set.of("engine", "engine_settings", "mode")), loader.toString());
        for (String name : List.of("nn-feedforward", "xgboost", "lightgbm")) {
            for (String field : strategy(name).trainFields().values()) {
                assertTrue(loader.contains(field), name + ": the loader has no field " + field);
            }
        }
    }

    @Test
    @DisplayName("a whole-number field goes as one, a decimal as a decimal, a bad value not at all")
    void numbersKeepTheirKind() {
        JsonObject body = new JsonObject();
        NeuralNetworkStrategy.addTrainFields(body, strategy("nn-feedforward").trainFields(),
                Map.of("hiddenLayerSize", "128", "learningRate", "0.01", "numEpochs", "many"));
        assertEquals(128, body.get("hidden_size").getAsInt());
        assertTrue(body.get("hidden_size").getAsJsonPrimitive().getAsNumber() instanceof Integer);
        assertEquals(0.01, body.get("learning_rate").getAsDouble(), 0.0);
        assertFalse(body.has("num_epochs"), "a value that does not parse leaves the loader's default");
        assertFalse(body.has("hidden_layer_size"));
    }
}
