package com.bazarbozorg.backtest.strategy.persistence;

import com.google.gson.Gson;
import com.google.gson.GsonBuilder;
import org.deeplearning4j.nn.multilayer.MultiLayerNetwork;
import org.deeplearning4j.util.ModelSerializer;
import org.nd4j.linalg.dataset.api.preprocessor.NormalizerMinMaxScaler;
import org.nd4j.linalg.dataset.api.preprocessor.serializer.NormalizerSerializer;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;

import java.io.IOException;
import java.io.UncheckedIOException;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.security.MessageDigest;
import java.security.NoSuchAlgorithmException;
import java.util.Map;
import java.util.Optional;
import java.util.TreeMap;

/**
 * Filesystem-backed cache of trained models.
 * <p>
 * Each entry lives in {@code <baseDir>/<strategyName>/<cacheKey>/} and contains:
 * <ul>
 *   <li>{@code model.zip}      &mdash; serialized {@link MultiLayerNetwork}</li>
 *   <li>{@code normalizer.bin} &mdash; serialized {@link NormalizerMinMaxScaler}</li>
 *   <li>{@code metadata.json}  &mdash; human-readable {@link ModelMetadata}</li>
 * </ul>
 * The cache key is a SHA-256 digest of a canonical, sorted concatenation of
 * the inputs that affect training output: strategy name, instrument id,
 * source id, timeframe, the training-data fingerprint (first/last bar epoch
 * seconds + bar count), all hyperparameters, and the DL4J version.
 */
public class ModelStore {

    private static final Logger logger = LoggerFactory.getLogger(ModelStore.class);

    private static final String MODEL_FILE = "model.zip";
    private static final String NORMALIZER_FILE = "normalizer.bin";
    private static final String METADATA_FILE = "metadata.json";

    private final Path baseDir;
    private final Gson gson = new GsonBuilder().setPrettyPrinting().create();

    public ModelStore(Path baseDir) {
        this.baseDir = baseDir;
    }

    /**
     * Computes the deterministic cache key from the given inputs. The inputs
     * map is treated as a set (not a list), so callers can pass a {@link Map}
     * in any order without affecting the result.
     */
    public static String computeCacheKey(Map<String, String> inputs) {
        TreeMap<String, String> sorted = new TreeMap<>(inputs);
        StringBuilder sb = new StringBuilder();
        for (Map.Entry<String, String> e : sorted.entrySet()) {
            sb.append(e.getKey()).append('=').append(e.getValue()).append('\n');
        }
        return sha256Hex(sb.toString());
    }

    /**
     * Returns the directory where the entry for the given strategy+key lives.
     * The directory may or may not exist on disk.
     */
    public Path entryDir(String strategyName, String cacheKey) {
        return baseDir.resolve(strategyName).resolve(cacheKey);
    }

    /**
     * Attempts to load a previously saved model. Returns empty if the entry
     * does not exist or any of its files are missing.
     * <p>
     * Throws an {@link UncheckedIOException} only on a real I/O failure
     * (corrupt file, permission denied) &mdash; missing files are a normal
     * cache miss, not an error.
     */
    public Optional<LoadedModel> load(String strategyName, String cacheKey) {
        Path dir = entryDir(strategyName, cacheKey);
        Path modelFile = dir.resolve(MODEL_FILE);
        Path normalizerFile = dir.resolve(NORMALIZER_FILE);
        Path metadataFile = dir.resolve(METADATA_FILE);

        if (!Files.exists(modelFile) || !Files.exists(normalizerFile) || !Files.exists(metadataFile)) {
            return Optional.empty();
        }

        try {
            MultiLayerNetwork network = ModelSerializer.restoreMultiLayerNetwork(modelFile.toFile());
            NormalizerMinMaxScaler normalizer = NormalizerSerializer.getDefault()
                    .restore(normalizerFile.toFile());
            String json = Files.readString(metadataFile, StandardCharsets.UTF_8);
            ModelMetadata metadata = gson.fromJson(json, ModelMetadata.class);
            return Optional.of(new LoadedModel(network, normalizer, metadata));
        } catch (IOException e) {
            throw new UncheckedIOException("Failed to load cached model from " + dir, e);
        } catch (Exception e) {
            // ND4J's NormalizerSerializer.restore declares throws Exception
            throw new RuntimeException("Failed to load cached model from " + dir, e);
        }
    }

    /**
     * Atomically saves the network, normalizer, and metadata under
     * {@code <baseDir>/<strategyName>/<cacheKey>/}. Existing files are
     * overwritten.
     */
    public void save(String strategyName,
                     String cacheKey,
                     MultiLayerNetwork network,
                     NormalizerMinMaxScaler normalizer,
                     ModelMetadata metadata) {
        Path dir = entryDir(strategyName, cacheKey);
        try {
            Files.createDirectories(dir);
            ModelSerializer.writeModel(network, dir.resolve(MODEL_FILE).toFile(), true);
            NormalizerSerializer.getDefault()
                    .write(normalizer, dir.resolve(NORMALIZER_FILE).toFile());
            String json = gson.toJson(metadata);
            Files.writeString(dir.resolve(METADATA_FILE), json, StandardCharsets.UTF_8);
            logger.info("Saved model to {}", dir);
        } catch (IOException e) {
            throw new UncheckedIOException("Failed to save model to " + dir, e);
        } catch (Exception e) {
            throw new RuntimeException("Failed to save model to " + dir, e);
        }
    }

    public Path baseDir() {
        return baseDir;
    }

    private static String sha256Hex(String input) {
        try {
            MessageDigest md = MessageDigest.getInstance("SHA-256");
            byte[] digest = md.digest(input.getBytes(StandardCharsets.UTF_8));
            StringBuilder sb = new StringBuilder(digest.length * 2);
            for (byte b : digest) {
                sb.append(String.format("%02x", b));
            }
            return sb.toString();
        } catch (NoSuchAlgorithmException e) {
            throw new IllegalStateException("SHA-256 unavailable", e);
        }
    }
}
