package com.bazarbozorg.backtest.cli;

import com.bazarbozorg.backtest.data.DatabaseManager;
import com.bazarbozorg.backtest.engine.OnnxCheck;
import picocli.CommandLine.Command;
import picocli.CommandLine.Option;

import java.nio.file.Path;
import java.util.List;

/**
 * The prediction check from the terminal (R62, G77): runs each fold's exported
 * network in ONNX Runtime and compares it with PyTorch's output. The same
 * check as {@code POST /api/walkforward/onnx-check?run=N}, for a run whose
 * models are on this machine.
 */
@Command(name = "onnx-check",
         description = "Run a walk-forward run's exported networks in ONNX Runtime and compare "
                 + "with PyTorch's output")
public class OnnxCheckCommand implements Runnable {

    @Option(names = {"--run"}, required = true, description = "The walk-forward run id")
    private long runId;

    @Option(names = {"--models-dir"},
            description = "Where the fold models are (default: FOLD_MODELS_DIR, else fold-models "
                    + "beside MODELS_DIR, else data/fold-models)")
    private Path modelsDir;

    @Override
    public void run() {
        DatabaseManager db = DatabaseManager.getInstance();
        try {
            db.initialize();
            List<OnnxCheck.FoldCheck> folds = OnnxCheck.checkRun(db, runId,
                    modelsDir != null ? modelsDir : OnnxCheck.baseDir());
            if (folds.isEmpty()) {
                System.out.println("run " + runId + " kept no network to check");
                return;
            }
            for (OnnxCheck.FoldCheck f : folds) {
                System.out.printf("fold %2d  %5d bars  %s%n", f.fold(), f.bars(),
                        f.problem() != null ? "NOT CHECKED: " + f.problem()
                                : String.format("largest difference %.3g  %s", f.maxDiff(),
                                        f.agreed() ? "agrees" : "DIFFERS (tolerance "
                                                + f.tolerance() + ")"));
            }
            long agreed = folds.stream().filter(OnnxCheck.FoldCheck::agreed).count();
            System.out.println(agreed + " of " + folds.size() + " folds agree with PyTorch on "
                    + "onnxruntime " + OnnxCheck.runtimeVersion());
        } catch (Exception e) {
            System.err.println("Error: " + e.getMessage());
        } finally {
            db.shutdown();
        }
    }
}
