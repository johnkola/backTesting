package com.bazarbozorg.backtest.engine;

import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.io.TempDir;

import java.io.IOException;
import java.nio.ByteBuffer;
import java.nio.ByteOrder;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.stream.Stream;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertNotNull;
import static org.junit.jupiter.api.Assertions.assertNull;
import static org.junit.jupiter.api.Assertions.assertTrue;

/**
 * The prediction check on a small network exported by the Python side (R62,
 * G77).
 *
 * <p>{@code src/test/resources/crosscheck/onnx-fold} is a fold's directory as
 * {@code python/nn/onnx_export.py} writes one: a 12-input network with a
 * constant input column, its scaler and softmax head inside the graph, 20 rows
 * of inputs and PyTorch's output on them. {@code tests/test_fold_models.py}
 * holds the Python side to the same layout.
 */
class OnnxCheckTest {

    private static final Path FIXTURE = Path.of("src/test/resources/crosscheck/onnx-fold");

    private static Path copyOf(Path into) throws IOException {
        try (Stream<Path> files = Files.list(FIXTURE)) {
            for (Path f : files.toList()) {
                Files.copy(f, into.resolve(f.getFileName()));
            }
        }
        return into;
    }

    @Test
    @DisplayName("ONNX Runtime gives what PyTorch gave on the same inputs")
    void agreesWithPyTorch() {
        OnnxCheck.FoldCheck c = OnnxCheck.checkFold(FIXTURE, 3);
        assertNull(c.problem(), c.problem());
        assertEquals(3, c.fold());
        assertEquals(20, c.bars());
        assertEquals(1e-5, c.tolerance());
        assertNotNull(c.maxDiff());
        assertTrue(c.maxDiff() <= 1e-5, "largest difference " + c.maxDiff());
        assertTrue(c.agreed());
        assertFalse(OnnxCheck.runtimeVersion().isBlank());
    }

    @Test
    @DisplayName("a fold's files hash by the rule the Python side records them with")
    void filesHashIsPythons(@TempDir Path tmp) throws IOException {
        Files.write(tmp.resolve("model.onnx"), "weights".getBytes(StandardCharsets.UTF_8));
        Files.write(tmp.resolve("check.json"), "{\"rows\": 0}".getBytes(StandardCharsets.UTF_8));
        Files.write(tmp.resolve("b-\u00e9.bin"), new byte[] {0, 1, 2});
        Files.createDirectory(tmp.resolve("not-a-file"));
        // nn.file_hash.files_hash on the same three files.
        assertEquals("e5094180a177526388f4a5ac343752abcdc09c60752d9cf7d33bf2a9a91d3cad",
                OnnxCheck.filesHash(tmp));
    }

    @Test
    @DisplayName("a fold whose files no longer hash to its record is not checked")
    void aChangedFoldIsNotChecked(@TempDir Path tmp) throws IOException {
        Path dir = copyOf(tmp);
        String recorded = OnnxCheck.filesHash(dir);
        OnnxCheck.FoldCheck as = OnnxCheck.checkRecordedFold(dir, 1, recorded);
        assertNull(as.problem(), as.problem());
        assertTrue(as.agreed());

        Path outputs = dir.resolve(OnnxCheck.OUTPUTS_FILE);
        byte[] raw = Files.readAllBytes(outputs);
        raw[0] ^= 1;
        Files.write(outputs, raw);
        OnnxCheck.FoldCheck changed = OnnxCheck.checkRecordedFold(dir, 1, recorded);
        assertEquals(0, changed.bars());
        assertFalse(changed.agreed());
        assertTrue(changed.problem().startsWith("the fold's files hash to "), changed.problem());
        assertTrue(changed.problem().endsWith("not the recorded " + recorded.substring(0, 12)
                + ": they changed after the run kept them"), changed.problem());
    }

    @Test
    @DisplayName("a different stored output is seen and reported, not thrown")
    void aDifferenceIsSeen(@TempDir Path tmp) throws IOException {
        Path dir = copyOf(tmp);
        Path outputs = dir.resolve(OnnxCheck.OUTPUTS_FILE);
        byte[] raw = Files.readAllBytes(outputs);
        ByteBuffer b = ByteBuffer.wrap(raw).order(ByteOrder.LITTLE_ENDIAN);
        b.putDouble(5 * Double.BYTES, b.getDouble(5 * Double.BYTES) + 0.01);   // one probability
        Files.write(outputs, raw);

        OnnxCheck.FoldCheck c = OnnxCheck.checkFold(dir, 1);
        assertNull(c.problem());
        assertFalse(c.agreed());
        assertEquals(0.01, c.maxDiff(), 1e-4);
    }

    @Test
    @DisplayName("a fold that was not exported, or whose batch is cut short, is not checked")
    void notCheckedSaysWhy(@TempDir Path tmp) throws IOException {
        OnnxCheck.FoldCheck none = OnnxCheck.checkFold(tmp, 2);
        assertFalse(none.agreed());
        assertNull(none.maxDiff());
        assertEquals("the fold was not exported to ONNX", none.problem());

        Path dir = copyOf(tmp);
        Path inputs = dir.resolve(OnnxCheck.INPUTS_FILE);
        byte[] raw = Files.readAllBytes(inputs);
        Files.write(inputs, java.util.Arrays.copyOf(raw, raw.length - 4));
        OnnxCheck.FoldCheck cut = OnnxCheck.checkFold(dir, 2);
        assertFalse(cut.agreed());
        assertEquals("the check batch is not the size check.json says", cut.problem());
    }
}
