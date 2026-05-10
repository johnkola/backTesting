package com.bazarbozorg.backtest.data.entity;

import java.time.ZonedDateTime;

/**
 * Database-row mirror of the {@code data_imports} audit table. Has no
 * domain counterpart &mdash; the audit log is itself the public shape, so
 * the row is consumed directly by {@code report}/web layers.
 */
public record DataImportRow(long id,
                            long sourceId,
                            long instrumentId,
                            String timeframe,
                            String filePath,
                            String fileName,
                            int rowCount,
                            ZonedDateTime importedAt) {

    public static Builder builder() {
        return new Builder();
    }

    public static final class Builder {
        private long id;
        private long sourceId;
        private long instrumentId;
        private String timeframe;
        private String filePath;
        private String fileName;
        private int rowCount;
        private ZonedDateTime importedAt;

        private Builder() {}

        public Builder id(long id) { this.id = id; return this; }
        public Builder sourceId(long sourceId) { this.sourceId = sourceId; return this; }
        public Builder instrumentId(long instrumentId) { this.instrumentId = instrumentId; return this; }
        public Builder timeframe(String timeframe) { this.timeframe = timeframe; return this; }
        public Builder filePath(String filePath) { this.filePath = filePath; return this; }
        public Builder fileName(String fileName) { this.fileName = fileName; return this; }
        public Builder rowCount(int rowCount) { this.rowCount = rowCount; return this; }
        public Builder importedAt(ZonedDateTime importedAt) { this.importedAt = importedAt; return this; }

        public DataImportRow build() {
            return new DataImportRow(id, sourceId, instrumentId, timeframe,
                    filePath, fileName, rowCount, importedAt);
        }
    }
}
