package com.bazarbozorg.backtest.strategy.persistence;

import com.google.gson.Gson;
import com.google.gson.GsonBuilder;
import org.nd4j.linalg.api.ndarray.INDArray;
import org.nd4j.linalg.factory.Nd4j;
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
 * Filesystem-backed cache of unnormalized feature matrices.
 * <p>
 * Each entry lives in {@code <baseDir>/<cacheKey>/} (note: no strategy prefix —
 * features are strategy-agnostic, so any NN strategy sharing the same feature
 * schema can reuse them) and contains:
 * <ul>
 *   <li>{@code features.bin}  &mdash; Nd4j-native binary of the {@link INDArray}</li>
 *   <li>{@code metadata.json} &mdash; human-readable {@link FeatureMetadata}</li>
 * </ul>
 * The cache key is a SHA-256 of inputs that affect feature output and only those:
 * instrument id, source id, timeframe, lookback window, features-per-bar,
 * feature-schema version, plus the underlying BarSeries fingerprint
 * (first/last bar epoch + bar count). Notably absent: model hyperparameters,
 * label parameters, and the DL4J version &mdash; this cache stays valid when any
 * of those change.
 */
public class FeatureStore {

    private static final Logger logger = LoggerFactory.getLogger(FeatureStore.class);

    private static final String FEATURES_FILE = "features.bin";
    private static final String METADATA_FILE = "metadata.json";

    private final Path baseDir;
    private final Gson gson = new GsonBuilder().setPrettyPrinting().create();

    public FeatureStore(Path baseDir) {
        this.baseDir = baseDir;
    }

    /**
     * Computes the deterministic cache key from the given inputs. Mirrors
     * {@link ModelStore#computeCacheKey}; kept separate to avoid coupling
     * the two stores' key formats.
     */
    public static String computeCacheKey(Map<String, String> inputs) {
        TreeMap<String, String> sorted = new TreeMap<>(inputs);
        StringBuilder sb = new StringBuilder();
        for (Map.Entry<String, String> e : sorted.entrySet()) {
            sb.append(e.getKey()).append('=').append(e.getValue()).append('\n');
        }
        return sha256Hex(sb.toString());
    }

    public Path entryDir(String cacheKey) {
        return baseDir.resolve(cacheKey);
    }

    /**
     * Returns the cached matrix, or empty when the entry is missing. Throws
     * on real I/O failure (corrupt file, permission denied) so callers can
     * distinguish miss-by-absence from miss-by-corruption.
     */
    public Optional<INDArray> load(String cacheKey) {
        Path dir = entryDir(cacheKey);
        Path featuresFile = dir.resolve(FEATURES_FILE);
        Path metadataFile = dir.resolve(METADATA_FILE);

        if (!Files.exists(featuresFile) || !Files.exists(metadataFile)) {
            return Optional.empty();
        }

        try {
            INDArray matrix = Nd4j.readBinary(featuresFile.toFile());
            return Optional.of(matrix);
        } catch (IOException e) {
            throw new UncheckedIOException("Failed to load cached features from " + dir, e);
        }
    }

    /**
     * Writes the matrix + metadata under {@code <baseDir>/<cacheKey>/}.
     * Existing files are overwritten.
     */
    public void save(String cacheKey, INDArray matrix, FeatureMetadata metadata) {
        Path dir = entryDir(cacheKey);
        try {
            Files.createDirectories(dir);
            Nd4j.saveBinary(matrix, dir.resolve(FEATURES_FILE).toFile());
            String json = gson.toJson(metadata);
            Files.writeString(dir.resolve(METADATA_FILE), json, StandardCharsets.UTF_8);
            logger.info("Saved features to {} (shape={}x{})", dir,
                    matrix.rows(), matrix.columns());
        } catch (IOException e) {
            throw new UncheckedIOException("Failed to save features to " + dir, e);
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
