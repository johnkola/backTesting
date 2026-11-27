package com.bazarbozorg.backtest.server;

import org.springframework.boot.SpringApplication;
import org.springframework.boot.autoconfigure.SpringBootApplication;

/**
 * The one backend the client calls (G16; Architecture §2, §6; R48).
 *
 * <p>What a change must keep:
 * <ul>
 *   <li><b>It never computes.</b> It reads the store and answers; anything that
 *       is work (an import, an audit, a training run, a backtest) is a row it
 *       inserts in the jobs table for a compute worker ({@code jobs} package).
 *       It calls no other service over HTTP.</li>
 *   <li><b>Every answer is JSON</b>, and every failure has one shape,
 *       {@code {"error": "..."}} ({@code api.ErrorAdvice}): the client already
 *       reads that from the Node API this service replaces.</li>
 *   <li><b>Every list pages, sorts and filters on the server</b>
 *       ({@code api.ListQuery}), for the shared table (§7.3).</li>
 *   <li><b>Every route has a JUnit test and a Postman request</b> (§6);
 *       {@code PostmanCoverageTest} fails the build for one that has none.</li>
 * </ul>
 *
 * <p>While the move is under way it runs beside the Node API on its own port,
 * and routes arrive here area by area.
 */
@SpringBootApplication
public class ServerApplication {
    public static void main(String[] args) {
        SpringApplication.run(ServerApplication.class, args);
    }
}
