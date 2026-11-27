package com.bazarbozorg.backtest.engine;

import com.bazarbozorg.backtest.model.EquityPoint;
import com.bazarbozorg.backtest.model.Order;
import com.bazarbozorg.backtest.model.Position;
import com.bazarbozorg.backtest.model.Trade;
import com.bazarbozorg.backtest.model.enums.OrderSide;

import java.time.ZonedDateTime;
import java.util.ArrayList;
import java.util.Collections;
import java.util.HashMap;
import java.util.List;
import java.util.Map;

/**
 * Manages the portfolio during a backtest, including positions, cash balance,
 * completed trades, and equity history. Tracks peak equity for drawdown
 * calculations.
 *
 * Positions are immutable {@link Position} records; this manager keeps a list
 * of "currently-open" position references and replaces them whenever a copy
 * with new state (commission update, order-id append, close) is produced.
 *
 * <p><b>The cash model.</b> Opening either side debits {@code price x qty +
 * commission}. Closing a long credits {@code price x qty - commission}; closing
 * a short credits back its notional plus its realized P&L, less the commission.
 * So a short's notional is held as collateral rather than its sale proceeds
 * being credited, and its round trip nets {@code (entry - exit) x qty} less both
 * commissions. There is no margin and no borrowing cost: the engine trades cash
 * only (Strategy 2's margin lives in the walk-forward scorer, not here).
 *
 * <p><b>Tax (G20).</b> With a rate above zero, each close taxes the trade's gain
 * net of commissions plus any loss carried from earlier trades, and pays it from
 * cash at the close; a net loss carries forward instead. Every close goes
 * through {@link #closePosition}, the end-of-run {@link #closeAllPositions}
 * included, so a position still open at the end is sold and taxed like any
 * other (D8).
 */
public class PortfolioManager {

    private final double initialCapital;
    /**
     * Tax on realized gains, a fraction (0.25 for 25%), or 0 for a run scored
     * before tax (G20). The walk-forward scorer's rule (`profit.simulate_taxed`)
     * in money terms: a gain is taxed when a position closes, net of both
     * commissions; a loss carries to offset later gains within the run; the
     * position the end of the window closes is taxed like any other.
     */
    private final double taxRate;
    /** Realized losses not yet offset by a gain: zero or negative. */
    private double lossCarry;
    private double taxPaid;
    private double cash;
    private final List<Position> openPositions;
    private final List<Trade> completedTrades;
    private final List<EquityPoint> equityHistory;
    /**
     * Bars each open position has been held through, by position id: one per
     * equity point recorded while it is open, so a position bought at bar i's
     * open and sold at bar j's open was held j - i bars, and one closed at the
     * last close counts that bar too. A trade carries it as {@code holdingBars}
     * (step 9: it was always 0).
     */
    private final Map<String, Integer> barsHeld = new HashMap<>();
    private double peakEquity;

    public PortfolioManager(double initialCapital) {
        this(initialCapital, 0.0);
    }

    /** @param taxRate tax on realized gains, a fraction; 0 scores before tax (G20) */
    public PortfolioManager(double initialCapital, double taxRate) {
        if (!(taxRate >= 0.0 && taxRate < 1.0)) {
            throw new IllegalArgumentException("tax rate must be a fraction in [0, 1), not " + taxRate);
        }
        this.taxRate = taxRate;
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

        Integer held = barsHeld.remove(position.id());
        Trade trade = Trade.from(closed, held == null ? 0 : held);
        if (taxRate > 0.0) {
            double net = trade.taxableGain() + lossCarry;
            if (net > 0.0) {
                double tax = taxRate * net;
                cash -= tax;
                taxPaid += tax;
                lossCarry = 0.0;
                trade = trade.withTax(tax);
            } else {
                lossCarry = net;
            }
        }
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
        for (Position position : openPositions) {
            barsHeld.merge(position.id(), 1, Integer::sum);
        }
    }

    public double getInitialCapital() {
        return initialCapital;
    }

    /** Tax paid on realized gains so far (G20). */
    public double getTaxPaid() {
        return taxPaid;
    }

    public double getTaxRate() {
        return taxRate;
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
