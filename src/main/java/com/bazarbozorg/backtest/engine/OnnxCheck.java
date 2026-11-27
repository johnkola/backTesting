package com.bazarbozorg.backtest.engine;

import ai.onnxruntime.OnnxTensor;
import ai.onnxruntime.OrtEnvironment;
import ai.onnxruntime.OrtException;
import ai.onnxruntime.OrtSession;
import com.bazarbozorg.backtest.data.DatabaseManager;
import com.google.gson.JsonObject;
import com.google.gson.JsonParser;

import java.io.IOException;
import java.nio.ByteBuffer;
import java.nio.ByteOrder;
import java.nio.FloatBuffer;
import java.nio.file.Files;
import java.nio.file.Path;
import java.sql.Connection;
import java.sql.PreparedStatement;
import java.sql.ResultSet;
import java.sql.SQLException;
import java.util.ArrayList;
import java.util.List;
import java.util.Map;

/**
 * The prediction check (R62, G77): a walk-forward fold's network, exported to
 * ONNX by the Python side, is run here in ONNX Runtime and its output compared
 * with what PyTorch produced on the same inputs.
 *
 * <p>The point is a second runtime. PyTorch trained the network and made the
 * signals the run stored; if a different runtime, given the same weights and
 * the same inputs, does not give the same numbers, the stored signals depend
 * on something other than the model. A fold that differs past the tolerance
 * is a warning on the run and never stops anything.
 *
 * <p>What a fold's directory holds is written by {@code python/nn/onnx_export.py}
 * and read here: {@code model.onnx} (the scaler and the output head are inside
 * it, so it takes the fold's raw inputs and returns what the fold stores),
 * {@code check_inputs.f32} and {@code check_outputs.f64} (flat little-endian
 * rows) and {@code check.json} (their shape and the tolerance). This class
 * shares no code with the Python side beyond that layout and the hash below.
 *
 * <p>The files are checked against their record before any is read (R49): the
 * fold's row in {@code walkforward_fold_models} holds a SHA-256 over them, by
 * the rule {@code python/nn/file_hash.py} states, and a fold whose files no
 * longer hash to it is reported as not checked. Run on files that changed
 * after the run kept them, the check would vouch for a model the run never
 * trained.
 *
 * <p>The design runs this in a separate Java compute worker fed by the jobs
 * table. That is the stack move (G16); until then the engine is the Java side,
 * and the run asks it over HTTP when it finishes.
 */
public final class OnnxCheck {

    static final String ONNX_FILE = "model.onnx";
    static final String INPUTS_FILE = "check_inputs.f32";
    static final String OUTPUTS_FILE = "check_outputs.f64";
    static final String CHECK_FILE = "check.json";

    private OnnxCheck() {
    }

    /**
     * One fold's outcome.
     *
     * @param bars      rows compared; 0 when it could not be checked
     * @param maxDiff   the largest absolute difference from PyTorch's output,
     *                  or null when it could not be checked
     * @param agreed    true when checked and {@code maxDiff <= tolerance}
     * @param problem   why it could not be checked, or null
     */
    public record FoldCheck(int fold, int bars, Double maxDiff, double tolerance, boolean agreed,
                            String problem) {

        static FoldCheck notChecked(int fold, String why) {
            return new FoldCheck(fold, 0, null, 0.0, false, why);
        }
    }

    /** Where the folds' directories are, as the Python side resolves it. */
    public static Path baseDir() {
        Map<String, String> env = System.getenv();
        String explicit = env.get("FOLD_MODELS_DIR");
        if (explicit != null && !explicit.isBlank()) {
            return Path.of(explicit);
        }
        String models = env.get("MODELS_DIR");
        if (models != null && !models.isBlank()) {
            Path parent = Path.of(models).toAbsolutePath().getParent();
            return (parent == null ? Path.of(".") : parent).resolve("fold-models");
        }
        return Path.of("data", "fold-models");
    }

    /** The ONNX Runtime this check ran on, recorded with each row. */
    public static String runtimeVersion() {
        return OrtEnvironment.getEnvironment().getVersion();
    }

    /**
     * SHA-256 over every file of the directory in name order, each as its name
     * in UTF-8, a zero byte, its length in decimal, a zero byte and its bytes:
     * the hash a kept model is recorded with ({@code python/nn/file_hash.py}).
     */
    static String filesHash(Path dir) throws IOException {
        java.security.MessageDigest sha;
        try {
            sha = java.security.MessageDigest.getInstance("SHA-256");
        } catch (java.security.NoSuchAlgorithmException e) {
            throw new IllegalStateException(e);
        }
        List<Path> files;
        try (var listing = Files.list(dir)) {
            files = listing.filter(Files::isRegularFile)
                    .sorted(java.util.Comparator.comparing(f -> f.getFileName().toString()))
                    .toList();
        }
        for (Path f : files) {
            byte[] raw = Files.readAllBytes(f);
            sha.update(f.getFileName().toString().getBytes(java.nio.charset.StandardCharsets.UTF_8));
            sha.update((byte) 0);
            sha.update(Integer.toString(raw.length).getBytes(java.nio.charset.StandardCharsets.US_ASCII));
            sha.update((byte) 0);
            sha.update(raw);
        }
        return java.util.HexFormat.of().formatHex(sha.digest());
    }

    /** One fold, after its files are checked against the hash its run recorded. */
    public static FoldCheck checkRecordedFold(Path dir, int fold, String recordedHash) {
        if (Files.isDirectory(dir)) {
            try {
                String now = filesHash(dir);
                String recorded = recordedHash == null ? "" : recordedHash.trim();
                if (!now.equals(recorded)) {
                    return FoldCheck.notChecked(fold, "the fold's files hash to "
                            + now.substring(0, 12) + ", not the recorded "
                            + recorded.substring(0, Math.min(12, recorded.length()))
                            + ": they changed after the run kept them");
                }
            } catch (IOException e) {
                return FoldCheck.notChecked(fold, e.getClass().getSimpleName() + ": " + e.getMessage());
            }
        }
        return checkFold(dir, fold);
    }

    /** Runs one fold's ONNX model on its check batch. Never throws for a bad fold. */
    public static FoldCheck checkFold(Path dir, int fold) {
        if (!Files.isRegularFile(dir.resolve(ONNX_FILE))) {
            return FoldCheck.notChecked(fold, "the fold was not exported to ONNX");
        }
        try {
            JsonObject meta = JsonParser.parseString(Files.readString(dir.resolve(CHECK_FILE)))
                    .getAsJsonObject();
            int rows = meta.get("rows").getAsInt();
            int width = meta.get("width").getAsInt();
            int outputs = meta.get("outputs").getAsInt();
            double tolerance = meta.get("tolerance").getAsDouble();

            byte[] rawIn = Files.readAllBytes(dir.resolve(INPUTS_FILE));
            byte[] rawOut = Files.readAllBytes(dir.resolve(OUTPUTS_FILE));
            if (rawIn.length != (long) rows * width * Float.BYTES
                    || rawOut.length != (long) rows * outputs * Double.BYTES) {
                return FoldCheck.notChecked(fold, "the check batch is not the size check.json says");
            }
            FloatBuffer inputs = ByteBuffer.wrap(rawIn).order(ByteOrder.LITTLE_ENDIAN).asFloatBuffer();
            ByteBuffer expected = ByteBuffer.wrap(rawOut).order(ByteOrder.LITTLE_ENDIAN);

            OrtEnvironment env = OrtEnvironment.getEnvironment();
            try (OrtSession session = env.createSession(dir.resolve(ONNX_FILE).toString(),
                         new OrtSession.SessionOptions());
                 OnnxTensor x = OnnxTensor.createTensor(env, inputs, new long[] {rows, width});
                 OrtSession.Result result = session.run(Map.of("x", x))) {
                Object value = result.get(0).getValue();
                if (!(value instanceof float[][] got) || got.length != rows
                        || (rows > 0 && got[0].length != outputs)) {
                    return FoldCheck.notChecked(fold, "the ONNX model's output is not "
                            + rows + " x " + outputs + " float values");
                }
                double worst = 0.0;
                for (int r = 0; r < rows; r++) {
                    for (int c = 0; c < outputs; c++) {
                        double want = expected.getDouble((r * outputs + c) * Double.BYTES);
                        worst = Math.max(worst, Math.abs(got[r][c] - want));
                    }
                }
                return new FoldCheck(fold, rows, worst, tolerance, worst <= tolerance, null);
            }
        } catch (IOException | OrtException | RuntimeException e) {
            return FoldCheck.notChecked(fold, e.getClass().getSimpleName() + ": " + e.getMessage());
        }
    }

    /**
     * Checks every network fold a run kept, stores a row per fold, and returns
     * them in fold order. A run that kept no network has no rows.
     */
    public static List<FoldCheck> checkRun(DatabaseManager db, long runId, Path base)
            throws SQLException {
        List<FoldCheck> out = new ArrayList<>();
        try (Connection conn = db.getConnection()) {
            try (PreparedStatement ps = conn.prepareStatement(
                    "SELECT fold, path, file_hash FROM walkforward_fold_models "
                            + "WHERE run_id = ? AND engine = 'mlp' ORDER BY fold")) {
                ps.setLong(1, runId);
                try (ResultSet rs = ps.executeQuery()) {
                    while (rs.next()) {
                        out.add(checkRecordedFold(base.resolve(rs.getString("path")),
                                rs.getInt("fold"), rs.getString("file_hash")));
                    }
                }
            }
            String version = out.isEmpty() ? null : runtimeVersion();
            try (PreparedStatement ps = conn.prepareStatement(
                    "INSERT INTO walkforward_onnx_checks "
                            + "(run_id, fold, bars, max_diff, tolerance, agreed, problem, runtime_version) "
                            + "VALUES (?, ?, ?, ?, ?, ?, ?, ?) "
                            + "ON CONFLICT (run_id, fold) DO UPDATE SET bars = EXCLUDED.bars, "
                            + "max_diff = EXCLUDED.max_diff, tolerance = EXCLUDED.tolerance, "
                            + "agreed = EXCLUDED.agreed, problem = EXCLUDED.problem, "
                            + "runtime_version = EXCLUDED.runtime_version, checked_at = now()")) {
                for (FoldCheck c : out) {
                    ps.setLong(1, runId);
                    ps.setInt(2, c.fold());
                    ps.setInt(3, c.bars());
                    if (c.maxDiff() == null) {
                        ps.setNull(4, java.sql.Types.DOUBLE);
                    } else {
                        ps.setDouble(4, c.maxDiff());
                    }
                    ps.setDouble(5, c.tolerance());
                    ps.setBoolean(6, c.agreed());
                    ps.setString(7, c.problem());
                    ps.setString(8, version);
                    ps.addBatch();
                }
                ps.executeBatch();
            }
            if (!conn.getAutoCommit()) {
                conn.commit();
            }
        }
        return out;
    }

    /** A run's stored check rows, in fold order; empty when it was never checked. */
    public static List<Map<String, Object>> stored(DatabaseManager db, long runId)
            throws SQLException {
        List<Map<String, Object>> out = new ArrayList<>();
        try (Connection conn = db.getConnection();
             PreparedStatement ps = conn.prepareStatement(
                     "SELECT fold, bars, max_diff, tolerance, agreed, problem, runtime_version, "
                             + "checked_at FROM walkforward_onnx_checks WHERE run_id = ? ORDER BY fold")) {
            ps.setLong(1, runId);
            try (ResultSet rs = ps.executeQuery()) {
                while (rs.next()) {
                    Map<String, Object> row = new java.util.LinkedHashMap<>();
                    row.put("fold", rs.getInt("fold"));
                    row.put("bars", rs.getInt("bars"));
                    double diff = rs.getDouble("max_diff");
                    row.put("maxDiff", rs.wasNull() ? null : diff);
                    row.put("tolerance", rs.getDouble("tolerance"));
                    row.put("agreed", rs.getBoolean("agreed"));
                    row.put("problem", rs.getString("problem"));
                    row.put("runtimeVersion", rs.getString("runtime_version"));
                    row.put("checkedAt", rs.getTimestamp("checked_at").toInstant().toString());
                    out.add(row);
                }
            }
        }
        return out;
    }
}
