package com.bazarbozorg.backtest.server.api;

import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;

/**
 * Paging, sorting and filters for a list route, the same on every list
 * (Architecture §6: "every list endpoint supports server paging, sorting and
 * filters for the shared table").
 *
 * <pre>
 *   ?limit=50&amp;offset=0          a page; limit 1 to 200, default 50
 *   ?sort=-createdAt,type        newest first, then by type; "-" is descending
 *   ?status=queued,running       a filter: any of these values
 * </pre>
 *
 * <p>What a change must keep:
 * <ul>
 *   <li><b>Nothing from the request reaches the SQL as text.</b> A sort field
 *       or a filter name is looked up in the route's {@link Spec} and only the
 *       column the spec holds is written; every value is a bind parameter.</li>
 *   <li><b>A bad page is corrected, a bad sort or filter is refused.</b>
 *       {@code limit=9999} or {@code offset=x} fall back to the bounds, as the
 *       Node API does, because a pager that overshoots should still show
 *       something. An unknown sort field, or a value of the wrong type, is a
 *       400 that lists what is allowed: silently ignoring it would show the
 *       wrong rows and look right.</li>
 *   <li><b>The order is total.</b> The spec's tiebreak column is always the
 *       last sort key, so two pages never share a row.</li>
 * </ul>
 */
public final class ListQuery {
    public static final int DEFAULT_LIMIT = 50;
    public static final int MAX_LIMIT = 200;

    /** How a filter's value is read. */
    public enum Kind { TEXT, NUMBER, BOOLEAN }

    private record Filter(String column, Kind kind) {
    }

    /** What one list allows: its sortable fields, its filters and its default order. */
    public static final class Spec {
        private final Map<String, String> sorts = new LinkedHashMap<>();
        private final Map<String, Filter> filters = new LinkedHashMap<>();
        private final String defaultSort;
        private final String tiebreak;
        private boolean nullsLast;

        /**
         * @param defaultSort the order when none is asked for, e.g. {@code "-id"}
         * @param tiebreak    the API field that makes the order total, e.g. {@code "id"},
         *                    or {@code "-id"} for the newest of equals first
         */
        public Spec(String defaultSort, String tiebreak) {
            this.defaultSort = defaultSort;
            this.tiebreak = tiebreak;
        }

        /**
         * Rows with no value sort last in either direction: on a list of
         * results "unknown" is not "best", so it must not top a descending sort.
         */
        public Spec nullsLast() {
            this.nullsLast = true;
            return this;
        }

        String tiebreakField() {
            return tiebreak.startsWith("-") ? tiebreak.substring(1) : tiebreak;
        }

        public Spec sort(String field, String column) {
            sorts.put(field, column);
            return this;
        }

        public Spec filter(String field, String column, Kind kind) {
            filters.put(field, new Filter(column, kind));
            return this;
        }
    }

    private final String where;
    private final String orderBy;
    private final String sort;
    private final int limit;
    private final int offset;
    private final List<Object> params;

    private ListQuery(String where, String orderBy, String sort, int limit, int offset,
            List<Object> params) {
        this.where = where;
        this.orderBy = orderBy;
        this.sort = sort;
        this.limit = limit;
        this.offset = offset;
        this.params = params;
    }

    public static ListQuery parse(Spec spec, Map<String, String> query) {
        int limit = clamp(number(query.get("limit"), DEFAULT_LIMIT), 1, MAX_LIMIT);
        int offset = Math.max(number(query.get("offset"), 0), 0);

        List<String> clauses = new ArrayList<>();
        List<Object> params = new ArrayList<>();
        for (Map.Entry<String, Filter> f : spec.filters.entrySet()) {
            String raw = query.get(f.getKey());
            if (raw == null || raw.isBlank()) {
                continue;
            }
            List<String> marks = new ArrayList<>();
            for (String one : raw.split(",")) {
                params.add(value(f.getKey(), f.getValue().kind(), one.trim()));
                marks.add("?");
            }
            clauses.add(f.getValue().column() + " IN (" + String.join(", ", marks) + ")");
        }

        String asked = query.get("sort");
        String sort = asked == null || asked.isBlank() ? spec.defaultSort : asked.trim();
        List<String> order = new ArrayList<>();
        boolean tied = false;
        for (String key : sort.split(",")) {
            String k = key.trim();
            boolean desc = k.startsWith("-");
            String field = desc ? k.substring(1) : k;
            String column = spec.sorts.get(field);
            if (column == null) {
                throw ApiException.badRequest("cannot sort by '" + field + "'; sort by one of "
                        + String.join(", ", spec.sorts.keySet()) + " (a leading - is descending)");
            }
            order.add(column + (desc ? " DESC" : " ASC") + (spec.nullsLast ? " NULLS LAST" : ""));
            tied |= field.equals(spec.tiebreakField());
        }
        if (!tied) {
            order.add(spec.sorts.get(spec.tiebreakField())
                    + (spec.tiebreak.startsWith("-") ? " DESC" : " ASC"));
        }
        return new ListQuery(clauses.isEmpty() ? "" : " WHERE " + String.join(" AND ", clauses),
                " ORDER BY " + String.join(", ", order), sort, limit, offset, params);
    }

    private static Object value(String field, Kind kind, String raw) {
        switch (kind) {
            case NUMBER:
                try {
                    return Long.parseLong(raw);
                } catch (NumberFormatException e) {
                    throw ApiException.badRequest("'" + raw + "' is not a number, for " + field);
                }
            case BOOLEAN:
                if (raw.equalsIgnoreCase("true") || raw.equalsIgnoreCase("false")) {
                    return Boolean.parseBoolean(raw);
                }
                throw ApiException.badRequest("'" + raw + "' is not true or false, for " + field);
            default:
                return raw;
        }
    }

    private static int number(String raw, int fallback) {
        if (raw == null) {
            return fallback;
        }
        try {
            return Integer.parseInt(raw.trim());
        } catch (NumberFormatException e) {
            return fallback;
        }
    }

    private static int clamp(int n, int lo, int hi) {
        return Math.min(Math.max(n, lo), hi);
    }

    /** {@code " WHERE ..."} or the empty string; its values are {@link #filterParams()}. */
    public String where() {
        return where;
    }

    /** {@code " ORDER BY ..."}, always total. */
    public String orderBy() {
        return orderBy;
    }

    /** {@code " LIMIT ? OFFSET ?"}; its values are the last two of {@link #pageParams()}. */
    public String paging() {
        return " LIMIT ? OFFSET ?";
    }

    public List<Object> filterParams() {
        return List.copyOf(params);
    }

    /** The filter values followed by limit and offset, for the page query. */
    public List<Object> pageParams() {
        List<Object> all = new ArrayList<>(params);
        all.add(limit);
        all.add(offset);
        return all;
    }

    public <T> Page<T> page(List<T> items, long total) {
        return new Page<>(items, total, limit, offset, sort);
    }
}
