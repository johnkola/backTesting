package com.bazarbozorg.backtest.cli;

import com.bazarbozorg.backtest.api.EngineApi;
import com.bazarbozorg.backtest.data.DatabaseManager;
import com.bazarbozorg.backtest.jobs.JobQueue;
import com.bazarbozorg.backtest.jobs.JobWorker;
import picocli.CommandLine.Command;

/**
 * Runs the engine as the Java compute worker, in the foreground until
 * interrupted: the process shape a container wants.
 *
 * <p>It opens no port. The Spring Boot service puts a {@code java-compute}
 * job in the jobs table for each thing only the engine can do (the strategy
 * list, a backtest, deleting a result, the walk-forward record, the ONNX
 * check), and this worker takes it and stores the answer (Architecture §2,
 * §6). {@code serve} is the command's old name, from when it served HTTP.
 */
@Command(name = "worker", aliases = {"serve"},
         description = "Run the Java compute worker: take java-compute jobs and answer them")
public class ServeCommand implements Runnable {

    @Override
    public void run() {
        DatabaseManager dbManager = DatabaseManager.getInstance();
        try {
            dbManager.initialize();
            JobWorker worker = new JobWorker(dbManager, new JobQueue(dbManager),
                    new EngineApi(dbManager));
            worker.start();
            System.out.printf("Job worker %s taking %s jobs%n", worker.id(), JobQueue.ENGINE);
            System.out.println("Press Ctrl+C to stop.");

            // Shut the pool down on SIGTERM so `docker stop` doesn't leave
            // connections hanging until the server times them out.
            Runtime.getRuntime().addShutdownHook(new Thread(() -> {
                worker.stop();
                dbManager.shutdown();
            }));

            Thread.currentThread().join();

        } catch (InterruptedException e) {
            Thread.currentThread().interrupt();
        } catch (Exception e) {
            System.err.println("Failed to start the job worker: " + e.getMessage());
            dbManager.shutdown();
        }
    }
}
