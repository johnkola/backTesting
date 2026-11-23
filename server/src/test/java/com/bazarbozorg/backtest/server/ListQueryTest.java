package com.bazarbozorg.backtest.server;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertThrows;
import static org.junit.jupiter.api.Assertions.assertTrue;

import com.bazarbozorg.backtest.server.api.ApiException;
import com.bazarbozorg.backtest.server.api.ListQuery;
import java.util.List;
import java.util.Map;
import org.junit.jupiter.api.Test;

/** The list conventions every list route shares, with no service running. */
class ListQueryTest {
    private static final ListQuery.Spec SPEC = new ListQuery.Spec("-id", "id")
            .sort("id", "j.id")
            .sort("createdAt", "j.created_at")
            .filter("status", "j.status", ListQuery.Kind.TEXT)
            .filter("attempts", "j.attempts", ListQuery.Kind.NUMBER)
            .filter("transient", "j.transient", ListQuery.Kind.BOOLEAN);

    @Test
    void withNothingAskedItIsTheFirstFiftyInTheDefaultOrder() {
        ListQuery q = ListQuery.parse(SPEC, Map.of());
        assertEquals("", q.where());
        assertEquals(" ORDER BY j.id DESC", q.orderBy());
        assertEquals(List.of(50, 0), q.pageParams());
        assertEquals("-id", q.page(List.of(), 0).sort());
    }

    @Test
    void aPageOutOfBoundsIsCorrectedNotRefused() {
        assertEquals(List.of(200, 0),
                ListQuery.parse(SPEC, Map.of("limit", "9999", "offset", "-5")).pageParams());
        assertEquals(List.of(1, 0), ListQuery.parse(SPEC, Map.of("limit", "0")).pageParams());
        assertEquals(List.of(50, 0),
                ListQuery.parse(SPEC, Map.of("limit", "many", "offset", "x")).pageParams());
    }

    @Test
    void theOrderIsAlwaysTotal() {
        assertEquals(" ORDER BY j.created_at ASC, j.id ASC",
                ListQuery.parse(SPEC, Map.of("sort", "createdAt")).orderBy());
        assertEquals(" ORDER BY j.created_at DESC, j.id DESC",
                ListQuery.parse(SPEC, Map.of("sort", "-createdAt,-id")).orderBy());
    }

    @Test
    void anUnknownSortFieldIsRefusedAndNeverWritten() {
        ApiException e = assertThrows(ApiException.class, () -> ListQuery.parse(SPEC,
                Map.of("sort", "id; DROP TABLE jobs")));
        assertEquals(400, e.status().value());
        assertTrue(e.getMessage().contains("sort by one of id, createdAt"), e.getMessage());
    }

    @Test
    void filtersAreBoundValuesOnTheSpecsColumns() {
        ListQuery q = ListQuery.parse(SPEC, Map.of("status", "queued, running",
                "attempts", "2", "transient", "false", "nonsense", "ignored"));
        assertEquals(" WHERE j.status IN (?, ?) AND j.attempts IN (?) AND j.transient IN (?)",
                q.where());
        assertEquals(List.of("queued", "running", 2L, false), q.filterParams());
        assertEquals(List.of("queued", "running", 2L, false, 50, 0), q.pageParams());
    }

    @Test
    void aFilterValueOfTheWrongTypeIsRefused() {
        assertEquals("'two' is not a number, for attempts", assertThrows(ApiException.class,
                () -> ListQuery.parse(SPEC, Map.of("attempts", "two"))).getMessage());
        assertEquals("'maybe' is not true or false, for transient", assertThrows(
                ApiException.class,
                () -> ListQuery.parse(SPEC, Map.of("transient", "maybe"))).getMessage());
    }
}
