package com.bazarbozorg.backtest.cli;

import com.bazarbozorg.backtest.loader.LoaderClient;
import com.google.gson.JsonArray;
import com.google.gson.JsonElement;
import com.google.gson.JsonObject;
import com.bazarbozorg.backtest.util.TableFormatter;
import picocli.CommandLine.Command;
import picocli.CommandLine.Option;

import java.util.ArrayList;
import java.util.List;

/**
 * Lists trained models cached on disk, by asking the loader — which owns the
 * model registry since the Python port, so it is the only component that knows
 * what {@code MODELS_DIR} actually contains.
 *
 * <p>This exists because {@code run --model-version} needs a version id and
 * there was no way to discover one from the terminal: its own help text used to
 * send you to the web UI.
 */
@Command(name = "list-models", description = "List trained models cached by the loader")
public class ListModelsCommand implements Runnable {

    @Option(names = {"-s", "--strategy"},
            description = "Only show models for this strategy (e.g. nn-feedforward)")
    private String strategy;

    @Option(names = {"--full-key"},
            description = "Print the whole 64-char cache key instead of the first 12")
    private boolean fullKey;

    @Override
    public void run() {
        String path = "/api/nn/models"
                + (strategy != null ? "?strategy=" + LoaderClient.encode(strategy) : "");

        JsonObject response;
        try {
            response = LoaderClient.get(path);
        } catch (LoaderClient.LoaderUnavailableException e) {
            System.err.println("Cannot list models: " + e.getMessage());
            System.err.println("The loader owns the model registry — start it with "
                    + "`docker compose up -d loader`, or set LOADER_URL if it runs elsewhere.");
            return;
        } catch (RuntimeException e) {
            System.err.println("Failed to list models: " + e.getMessage());
            return;
        }

        JsonArray items = response.getAsJsonArray("items");
        if (items == null || items.isEmpty()) {
            System.out.println(strategy != null
                    ? "No cached models for strategy '" + strategy + "'."
                    : "No cached models. Train one with `train -s <strategy> -i <SYMBOL>`.");
            return;
        }

        List<String> headers = List.of(
                "Strategy", "Cache key", "Version", "Created", "Inputs", "Hidden", "Layers");

        List<List<String>> rows = new ArrayList<>();
        for (JsonElement item : items) {
            JsonObject m = item.getAsJsonObject();
            String key = orDash(LoaderClient.string(m, "cacheKey"));
            if (!fullKey && key.length() > 12) {
                key = key.substring(0, 12);
            }
            rows.add(List.of(
                    orDash(LoaderClient.string(m, "strategy")),
                    key,
                    orDash(LoaderClient.string(m, "versionId")),
                    orDash(LoaderClient.string(m, "createdAt")),
                    intOrDash(m, "inputSize"),
                    intOrDash(m, "hiddenSize"),
                    intOrDash(m, "numHidden")));
        }

        System.out.println();
        System.out.printf("  %d cached model(s) in %s:%n%n",
                items.size(), orDash(LoaderClient.string(response, "modelsDir")));
        System.out.println(TableFormatter.formatTable(headers, rows));
        if (!fullKey) {
            System.out.println("  Cache keys truncated to 12 chars — use --full-key for the whole hash.");
        }
        System.out.println("  Pin one on a backtest with: run --model-version <Version>");
    }

    private static String orDash(String s) {
        return s != null && !s.isBlank() ? s : "—";
    }

    private static String intOrDash(JsonObject obj, String field) {
        JsonElement el = obj.get(field);
        return el != null && !el.isJsonNull() ? String.valueOf(el.getAsInt()) : "—";
    }
}
