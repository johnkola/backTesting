package com.bazarbozorg.backtest.cli;

import com.bazarbozorg.backtest.api.EngineApi;
import com.bazarbozorg.backtest.data.DatabaseManager;
import picocli.CommandLine.Command;
import picocli.CommandLine.Option;

/**
 * Runs the engine's HTTP API in the foreground until interrupted — the process
 * shape a container wants.
 *
 * <p>Port 8001 is the Python loader's; this defaults to 8002 so both can run
 * side by side. The browser talks to neither directly: Node on :3000 serves the
 * UI and proxies to both, so the client has one origin.
 */
@Command(name = "serve",
         description = "Serve the engine HTTP API (strategies, run, delete result)")
public class ServeCommand implements Runnable {

    @Option(names = {"-p", "--port"},
            description = "Port to bind (default: ${DEFAULT-VALUE}, or $ENGINE_PORT)")
    private Integer port;

    @Override
    public void run() {
        int bindPort = resolvePort();

        DatabaseManager dbManager = DatabaseManager.getInstance();
        EngineApi api = new EngineApi(dbManager);
        try {
            dbManager.initialize();
            api.start(bindPort);

            System.out.printf("Engine API listening on http://0.0.0.0:%d%n", bindPort);
            System.out.println("  GET    /api/strategies");
            System.out.println("  POST   /api/run");
            System.out.println("  DELETE /api/results/{id}");
            System.out.println("  GET    /api/health");
            System.out.println("Press Ctrl+C to stop.");

            // Shut the pool down on SIGTERM so `docker stop` doesn't leave
            // connections hanging until the server times them out.
            Runtime.getRuntime().addShutdownHook(new Thread(() -> {
                api.stop();
                dbManager.shutdown();
            }));

            Thread.currentThread().join();

        } catch (InterruptedException e) {
            Thread.currentThread().interrupt();
        } catch (Exception e) {
            System.err.println("Failed to start the engine API: " + e.getMessage());
            api.stop();
            dbManager.shutdown();
        }
    }

    /** --port wins over $ENGINE_PORT, which wins over the 8002 default. */
    private int resolvePort() {
        if (port != null) {
            return port;
        }
        String env = System.getenv("ENGINE_PORT");
        if (env != null && !env.isBlank()) {
            try {
                return Integer.parseInt(env.trim());
            } catch (NumberFormatException e) {
                System.err.println("Ignoring unparseable ENGINE_PORT: " + env);
            }
        }
        return 8002;
    }
}
