package com.bazarbozorg.backtest.engine;

import com.bazarbozorg.backtest.model.EquityPoint;
import com.bazarbozorg.backtest.model.Order;
import com.bazarbozorg.backtest.model.Position;
import com.bazarbozorg.backtest.model.Trade;
import com.bazarbozorg.backtest.model.enums.OrderSide;

import java.time.ZonedDateTime;
import java.util.ArrayList;
import java.util.Collections;
import java.util.List;

/**
 * Manages the portfolio during a backtest, including positions, cash balance,
 * completed trades, and equity history. Tracks peak equity for drawdown
 * calculations.
 *
 * Positions are immutable {@link Position} records; this manager keeps a list
 * of "currently-open" position references and replaces them whenever a copy
 * with new state (commission update, order-id append, close) is produced.
 */
public class PortfolioManager {

    private final double initialCapital;
    private double cash;
    private final List<Position> openPositions;
    private final List<Trade> completedTrades;
    private final List<EquityPoint> equityHistory;
    private double peakEquity;

    public PortfolioManager(double initialCapital) {
        this.initialCapital = initialCapital;
        this.cash = initialCapital;
        this.openPositions = new ArrayList<>();
        this.completedTrades = new ArrayList<>();
        this.equityHistory = new ArrayList<>();
        this.peakEquity = initialCapital;
    }

    /**
     * Opens a new position and deducts its cost from cash.
     */
    public Position openPosition(long instrumentId, OrderSide side, double price,
                                  double quantity, ZonedDateTime time,
                                  double commission, String entryOrderId) {
        cash -= (price * quantity + commission);
        Position position = Position.open(instrumentId, side, price, quantity, time,
                commission, entryOrderId);
        openPositions.add(position);
        return position;
    }

    /**
     * Closes an existing position and adds the proceeds (or net change for
     * shorts) to cash. Records the resulting {@link Trade}.
     *
     * @param exitOrderId optional id of the exit order; appended to the
     *                    closed position's order-id history if non-null
     */
    public Trade closePosition(Position position, double price, ZonedDateTime time,
                                double commission, String exitOrderId) {
        if (!position.open()) {
            throw new IllegalStateException("Position is already closed: " + position.id());
        }
        Position closed = position
                .closed(price, time)
                .withCommission(position.commission() + commission)
                .withOrderId(exitOrderId);

        double proceeds = price * closed.quantity() - commission;
        if (closed.isLong()) {
            cash += proceeds;
        } else {
            // Short: original collateral + realized P&L - commission
            cash += (closed.entryPrice() * closed.quantity()) + closed.realizedPnl() - commission;
        }

        openPositions.remove(position);

        Trade trade = Trade.from(closed, 0);
        completedTrades.add(trade);
        return trade;
    }

    /** Convenience overload used when no exit order is associated (force-close). */
    public Trade closePosition(Position position, double price, ZonedDateTime time,
                                double commission) {
        return closePosition(position, price, time, commission, null);
    }

    /**
     * Closes all open positions at the given price (force-close at the end of
     * a backtest). Filled through the {@code simulator}, like any other exit,
     * so its slippage is counted in the run's total (G29); the price and
     * commission are the same as applying its models directly.
     */
    public void closeAllPositions(double price, ZonedDateTime time, ExecutionSimulator simulator) {
        List<Position> positionsToClose = new ArrayList<>(openPositions);
        for (Position position : positionsToClose) {
            OrderSide exitSide = position.isLong() ? OrderSide.SELL : OrderSide.BUY;
            Order exit = Order.market(position.instrumentId(), exitSide, position.quantity(), price);
            ExecutionSimulator.FillResult fill = simulator.fillMarketOrder(exit, price);
            closePosition(position, fill.filledPrice(), time, fill.commission());
        }
    }

    /**
     * Interest on cash for one bar, at {@code rate} (a fraction of the cash).
     * Only dual momentum's cash earns any, T-bills as its rule says (B7); the
     * strategy's idle money earns nothing.
     */
    public void accrueInterest(double rate) {
        cash *= 1.0 + rate;
    }

    /** Total equity = cash + market value of any open positions. */
    public double getEquity(double currentPrice) {
        double positionValue = 0.0;
        for (Position position : openPositions) {
            if (position.isLong()) {
                positionValue += currentPrice * position.quantity();
            } else {
                // Short value: entry collateral + unrealized P&L
                positionValue += (position.entryPrice() * position.quantity())
                        + position.unrealizedPnl(currentPrice);
            }
        }
        return cash + positionValue;
    }

    /** Records an equity point and updates peak/drawdown tracking. */
    public void recordEquityPoint(ZonedDateTime time, double currentPrice) {
        double equity = getEquity(currentPrice);
        if (equity > peakEquity) {
            peakEquity = equity;
        }
        double drawdown = peakEquity - equity;
        double drawdownPct = peakEquity > 0 ? drawdown / peakEquity : 0.0;
        equityHistory.add(new EquityPoint(time, equity, drawdown, drawdownPct));
    }

    public double getInitialCapital() {
        return initialCapital;
    }

    public double getCash() {
        return cash;
    }

    public List<Position> getOpenPositions() {
        return Collections.unmodifiableList(openPositions);
    }

    public List<Trade> getCompletedTrades() {
        return Collections.unmodifiableList(completedTrades);
    }

    public List<EquityPoint> getEquityHistory() {
        return Collections.unmodifiableList(equityHistory);
    }

    public double getPeakEquity() {
        return peakEquity;
    }

    public boolean hasOpenPositions() {
        return !openPositions.isEmpty();
    }

    /** First open position matching {@code (instrumentId, side)}, or null. */
    public Position findOpenPosition(long instrumentId, OrderSide side) {
        for (Position position : openPositions) {
            if (position.instrumentId() == instrumentId && position.side() == side) {
                return position;
            }
        }
        return null;
    }
}
