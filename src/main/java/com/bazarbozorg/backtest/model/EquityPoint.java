package com.bazarbozorg.backtest.model;

import java.time.ZonedDateTime;

/**
 * Represents a snapshot of portfolio equity at a specific point in time,
 * including drawdown information.
 *
 * @param timestamp   the time of this equity snapshot
 * @param equity      the total portfolio equity (cash + positions market value)
 * @param drawdown    the absolute drawdown from the peak equity
 * @param drawdownPct the drawdown as a percentage of the peak equity
 */
public record EquityPoint(ZonedDateTime timestamp, double equity, double drawdown, double drawdownPct) {
}
