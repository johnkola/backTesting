package com.bazarbozorg.backtest.server.api;

import java.util.List;

/**
 * One page of a list, with what the shared table needs to draw its pager and
 * its sort marker: the rows, how many match in all, and the paging and sort
 * that were actually applied (after defaults and clamping).
 */
public record Page<T>(List<T> items, long total, int limit, int offset, String sort) {
}
