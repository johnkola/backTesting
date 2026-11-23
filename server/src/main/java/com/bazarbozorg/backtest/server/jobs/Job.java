package com.bazarbozorg.backtest.server.jobs;

import com.fasterxml.jackson.annotation.JsonProperty;
import com.fasterxml.jackson.annotation.JsonRawValue;
import java.time.OffsetDateTime;

/**
 * A row of the jobs table as the API shows it. {@code inputs} and
 * {@code result} are the stored JSON, passed through as written: this service
 * has no opinion about what a job type's inputs or result look like.
 */
public record Job(
        long id,
        String type,
        String engine,
        String status,
        String requestedBy,
        String workerId,
        int attempts,
        boolean cancelRequested,
        @JsonProperty("transient") boolean isTransient,
        @JsonRawValue String inputs,
        @JsonRawValue String result,
        String error,
        OffsetDateTime createdAt,
        OffsetDateTime claimedAt,
        OffsetDateTime heartbeatAt,
        OffsetDateTime finishedAt) {

    /** Over: done, failed or cancelled. */
    public boolean ended() {
        return status.equals("done") || status.equals("failed") || status.equals("cancelled");
    }
}
