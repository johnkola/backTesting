package com.bazarbozorg.backtest.server.jobs;

import java.time.Duration;
import org.springframework.stereotype.Service;

/**
 * Work this service asks a compute worker for and waits on: insert the job,
 * wait until it has ended, hand back the row (Architecture §6: "Python is
 * reached only through the jobs table; Java reads job status from it").
 *
 * <p>The Java counterpart of {@code python/jobs/calls.py}, and it keeps the
 * same three outcomes apart, because a route answers each differently:
 * <ul>
 *   <li>the job is <b>done</b>: its result is the answer, whatever it says. A
 *       route job's result is {@code {"status": 400, "body": ...}} as readily
 *       as a 200; a refusal is a finished job.</li>
 *   <li>the job <b>failed</b> or was <b>cancelled</b>: the work raised. The
 *       caller gets the row and reports the reason.</li>
 *   <li><b>no worker finished it in time</b>: {@link TimedOut}, after asking
 *       for the job to be cancelled so it does not run later, when nobody is
 *       waiting for it any more.</li>
 * </ul>
 *
 * <p>The wait polls the row, quickly at first. It holds no connection and no
 * lock between polls.
 */
@Service
public class JobCalls {
    private final JobsRepository jobs;

    public JobCalls(JobsRepository jobs) {
        this.jobs = jobs;
    }

    /** No worker finished the job in time; the message says whether one ever took it. */
    public static class TimedOut extends RuntimeException {
        public TimedOut(String message) {
            super(message);
        }
    }

    public Job call(String type, String engine, String inputsJson, Duration timeout,
            boolean isTransient) {
        Job job = jobs.insert(type, engine, inputsJson, isTransient);
        long deadline = System.nanoTime() + timeout.toNanos();
        long delayMs = 20;
        while (true) {
            Job now = jobs.find(job.id()).orElseThrow(() -> new IllegalStateException(
                    "job " + job.id() + " (" + type + ") is gone from the jobs table"));
            if (now.ended()) {
                return now;
            }
            if (System.nanoTime() >= deadline) {
                jobs.requestCancel(job.id());
                String where = now.status().equals("queued")
                        ? "no worker took it: is the " + engine + " worker running?"
                        : "worker " + now.workerId() + " was still on it";
                throw new TimedOut("job " + job.id() + " (" + type + ") did not finish in "
                        + timeout.toMillis() / 1000.0 + " s; " + where);
            }
            try {
                Thread.sleep(delayMs);
            } catch (InterruptedException e) {
                Thread.currentThread().interrupt();
                throw new IllegalStateException("interrupted waiting for job " + job.id(), e);
            }
            delayMs = Math.min(delayMs * 3 / 2, 250);
        }
    }
}
