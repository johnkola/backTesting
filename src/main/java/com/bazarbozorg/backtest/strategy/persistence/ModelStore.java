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
import java.time.Instant;
import java.time.ZoneOffset;
import java.time.format.DateTimeFormatter;
import java.util.Comparator;
import java.util.Map;
import java.util.Optional;
import java.util.TreeMap;
import java.util.regex.Pattern;
import java.util.stream.Stream;

/**
 * Filesystem-backed cache of trained models.
 * <p>
 * Each entry lives in {@code <baseDir>/<strategyName>/<cacheKey>/<versionId>/}
 * and contains:
 * <ul>
 *   <li>{@code model.zip}      &mdash; serialized {@link MultiLayerNetwork}</li>
 *   <li>{@code normalizer.bin} &mdash; serialized {@link NormalizerMinMaxScaler}</li>
 *   <li>{@code metadata.json}  &mdash; human-readable {@link ModelMetadata}</li>
 * </ul>
 * The cache key is a SHA-256 digest of a canonical, sorted concatenation of
 * the inputs that affect training output: strategy name, instrument id,
 * source id, timeframe, the training-data fingerprint (first/last bar epoch
 * seconds + bar count), all hyperparameters, and the DL4J version.
 * <p>
 * The {@code versionId} (an ISO-8601-ish compact UTC timestamp, e.g.
 * {@code 20260511T134522.123Z}) lets multiple training runs against the
 * same cache key coexist instead of overwriting each other. {@link #load}
 * returns the lexicographically-latest version. Legacy flat-layout entries
 * (where {@code model.zip} sits directly under the key dir, written before
 * versioning) still load transparently.
 */
public class ModelStore {

    private static final Logger logger = LoggerFactory.getLogger(ModelStore.class);

    private static final String MODEL_FILE = "model.zip";
    private static final String NORMALIZER_FILE = "normalizer.bin";
    private static final String METADATA_FILE = "metadata.json";

    /**
     * Version-directory name format: compact ISO-8601 with millisecond
     * precision, UTC. Sortable lexicographically (latest is largest) and
     * filesystem-safe on Windows + Linux + macOS (no colons or slashes).
     */
    private static final DateTimeFormatter VERSION_FORMAT =
            DateTimeFormatter.ofPattern("yyyyMMdd'T'HHmmss.SSS'Z'").withZone(ZoneOffset.UTC);

    /** Matches the version-directory format so we ignore stray subdirs. */
    private static final Pattern VERSION_PATTERN =
            Pattern.compile("\\d{8}T\\d{6}\\.\\d{3}Z");

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
     * Returns the key directory (parent of every version subdir for a given
     * strategy+key). May or may not exist on disk.
     */
    public Path entryDir(String strategyName, String cacheKey) {
        return baseDir.resolve(strategyName).resolve(cacheKey);
    }

    /** Returns the directory for a specific version of an entry. */
    public Path versionDir(String strategyName, String cacheKey, String versionId) {
        return entryDir(strategyName, cacheKey).resolve(versionId);
    }

    /**
     * Returns the path the next {@link #save} will write to, in case callers
     * want to record it. Generated lazily &mdash; two calls return different
     * paths because the timestamp moves forward.
     */
    public String newVersionId() {
        return VERSION_FORMAT.format(Instant.now());
    }

    /**
     * Attempts to load the latest version of a previously saved model.
     * Returns empty when nothing is cached for this key.
     * <p>
     * Resolution order:
     * <ol>
     *   <li>The lexicographically-latest {@code <keyDir>/<versionId>/} that
     *       holds a complete entry (model.zip + normalizer.bin + metadata.json).</li>
     *   <li>If none, the legacy flat layout where the files sit directly under
     *       {@code <keyDir>/} (written by pre-versioning {@code save}).</li>
     * </ol>
     * Throws an {@link UncheckedIOException} only on a real I/O failure
     * (corrupt file, permission denied) &mdash; missing files are a normal
     * cache miss, not an error.
     */
    public Optional<LoadedModel> load(String strategyName, String cacheKey) {
        Path keyDir = entryDir(strategyName, cacheKey);
        if (!Files.exists(keyDir)) {
            return Optional.empty();
        }

        Optional<Path> latest = findLatestVersionDir(keyDir);
        if (latest.isPresent()) {
            return loadFromDir(latest.get());
        }

        // Legacy: model.zip directly under the key dir, written before versioning.
        if (Files.exists(keyDir.resolve(MODEL_FILE))) {
            return loadFromDir(keyDir);
        }

        return Optional.empty();
    }

    /** Returns the latest version subdir under the given key dir, if one exists. */
    private Optional<Path> findLatestVersionDir(Path keyDir) {
        try (Stream<Path> stream = Files.list(keyDir)) {
            return stream
                    .filter(Files::isDirectory)
                    .filter(p -> VERSION_PATTERN.matcher(p.getFileName().toString()).matches())
                    .max(Comparator.comparing(p -> p.getFileName().toString()));
        } catch (IOException e) {
            throw new UncheckedIOException("Failed to list versions under " + keyDir, e);
        }
    }

    private Optional<LoadedModel> loadFromDir(Path dir) {
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
     * Saves the network, normalizer, and metadata as a new version under
     * {@code <baseDir>/<strategyName>/<cacheKey>/<versionId>/}. Each call
     * produces a fresh version directory; prior versions for the same key
     * are preserved. Returns the version id that was written so callers
     * can record it.
     */
    public String save(String strategyName,
                       String cacheKey,
                       MultiLayerNetwork network,
                       NormalizerMinMaxScaler normalizer,
                       ModelMetadata metadata) {
        String versionId = newVersionId();
        Path dir = versionDir(strategyName, cacheKey, versionId);
        try {
            Files.createDirectories(dir);
            ModelSerializer.writeModel(network, dir.resolve(MODEL_FILE).toFile(), true);
            NormalizerSerializer.getDefault()
                    .write(normalizer, dir.resolve(NORMALIZER_FILE).toFile());
            String json = gson.toJson(metadata);
            Files.writeString(dir.resolve(METADATA_FILE), json, StandardCharsets.UTF_8);
            logger.info("Saved model to {}", dir);
            return versionId;
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
