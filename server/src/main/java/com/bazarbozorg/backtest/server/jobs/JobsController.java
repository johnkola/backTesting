package com.bazarbozorg.backtest.server.jobs;

import com.bazarbozorg.backtest.server.api.ApiException;
import com.bazarbozorg.backtest.server.api.ListQuery;
import com.bazarbozorg.backtest.server.api.Page;
import java.util.Map;
import org.springframework.http.HttpStatus;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PathVariable;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestParam;
import org.springframework.web.bind.annotation.ResponseStatus;
import org.springframework.web.bind.annotation.RestController;

/**
 * The jobs table, for the UI: what is waiting, running and over, and the one
 * thing a person may do to a job, which is ask it to stop (R70).
 *
 * <pre>
 *   GET  /api/jobs             a page of jobs, newest first; sort and filter per ListQuery
 *   GET  /api/jobs/{id}        one job, with its inputs and result
 *   POST /api/jobs/{id}/cancel 202 the job, cancelRequested true | 404 | 409 already over
 * </pre>
 *
 * <p>Cancel is a request, not an act: the worker hears it at its next
 * heartbeat, stops, and marks the job cancelled with what it had written
 * kept. So the answer to a cancel still shows the job as running.
 */
@RestController
public class JobsController {
    private final JobsRepository jobs;

    public JobsController(JobsRepository jobs) {
        this.jobs = jobs;
    }

    @GetMapping("/api/jobs")
    public Page<Job> list(@RequestParam Map<String, String> query) {
        return jobs.list(ListQuery.parse(JobsRepository.LIST, query));
    }

    @GetMapping("/api/jobs/{id}")
    public Job one(@PathVariable long id) {
        return jobs.find(id).orElseThrow(() -> ApiException.notFound("no job " + id));
    }

    @PostMapping("/api/jobs/{id}/cancel")
    @ResponseStatus(HttpStatus.ACCEPTED)
    public Job cancel(@PathVariable long id) {
        Job job = one(id);
        if (!jobs.requestCancel(id)) {
            throw ApiException.conflict("job " + id + " is already " + job.status());
        }
        return one(id);
    }
}
