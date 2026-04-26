package com.bazarbozorg.backtest.engine;

import com.bazarbozorg.backtest.model.EquityPoint;
import com.bazarbozorg.backtest.model.OrderSide;
import com.bazarbozorg.backtest.model.Position;
import com.bazarbozorg.backtest.model.Trade;

import java.time.ZonedDateTime;
import java.util.ArrayList;
import java.util.Collections;
import java.util.List;

/**
 * Manages the portfolio during a backtest, including positions, cash balance,
 * completed trades, and equity history. Tracks peak equity for drawdown calculations.
 */
public class PortfolioManager {

    private final double initialCapital;
    private double cash;
    private final List<Position> openPositions;
    private final List<Trade> completedTrades;
    private final List<EquityPoint> equityHistory;
    private double peakEquity;

    /**
     * Creates a new portfolio manager with the given initial capital.
     *
     * @param initialCapital the starting cash amount
     */
    public PortfolioManager(double initialCapital) {
        this.initialCapital = initialCapital;
        this.cash = initialCapital;
        this.openPositions = new ArrayList<>();
        this.completedTrades = new ArrayList<>();
        this.equityHistory = new ArrayList<>();
        this.peakEquity = initialCapital;
    }

    /**
     * Opens a new position and deducts the cost from cash.
     *
     * @param instrumentId the instrument identifier
     * @param side         the order side (BUY for long, SELL for short)
     * @param price        the entry price
     * @param quantity     the position size
     * @param time         the entry time
     * @param commission   the commission charged for opening
     * @return the newly created Position
     */
    public Position openPosition(long instrumentId, OrderSide side, double price,
                                  double quantity, ZonedDateTime time, double commission) {
        double cost = price * quantity + commission;
        cash -= cost;

        Position position = new Position(instrumentId, side, price, quantity, time);
        position.setCommission(commission);
        openPositions.add(position);

        return position;
    }

    /**
     * Closes an existing position and adds the proceeds to cash.
     * Creates a {@link Trade} from the closed position.
     *
     * @param position   the position to close
     * @param price      the exit price
     * @param time       the exit time
     * @param commission the commission charged for closing
     * @return the completed Trade
     */
    public Trade closePosition(Position position, double price, ZonedDateTime time, double commission) {
        position.close(price, time);
        position.setCommission(position.getCommission() + commission);

        double proceeds = price * position.getQuantity() - commission;
        if (position.isLong()) {
            cash += proceeds;
        } else {
            // For short positions: profit/loss is added to the original collateral
            cash += (position.getEntryPrice() * position.getQuantity()) + position.getRealizedPnl() - commission;
        }

        openPositions.remove(position);

        Trade trade = new Trade(position, 0);
        completedTrades.add(trade);

        return trade;
    }

    /**
     * Closes all open positions at the given price.
     *
     * @param price     the exit price for all positions
     * @param time      the exit time
     * @param simulator the execution simulator for commission/slippage calculation
     */
    public void closeAllPositions(double price, ZonedDateTime time, ExecutionSimulator simulator) {
        List<Position> positionsToClose = new ArrayList<>(openPositions);
        for (Position position : positionsToClose) {
            OrderSide exitSide = position.isLong() ? OrderSide.SELL : OrderSide.BUY;
            double adjustedPrice = simulator.getSlippageModel().calculate(price, exitSide);
            double commission = simulator.getCommissionModel().calculate(adjustedPrice, position.getQuantity());
            closePosition(position, adjustedPrice, time, commission);
        }
    }

    /**
     * Calculates the total portfolio equity (cash + market value of open positions).
     *
     * @param currentPrice the current market price for valuing open positions
     * @return the total equity
     */
    public double getEquity(double currentPrice) {
        double positionValue = 0.0;
        for (Position position : openPositions) {
            if (position.isLong()) {
                positionValue += currentPrice * position.getQuantity();
            } else {
                // Short position value: entry value + unrealized P&L
                positionValue += (position.getEntryPrice() * position.getQuantity())
                        + position.getUnrealizedPnl(currentPrice);
            }
        }
        return cash + positionValue;
    }

    /**
     * Records an equity point snapshot, tracking drawdown from peak equity.
     *
     * @param time         the timestamp for this snapshot
     * @param currentPrice the current market price for equity calculation
     */
    public void recordEquityPoint(ZonedDateTime time, double currentPrice) {
        double equity = getEquity(currentPrice);

        if (equity > peakEquity) {
            peakEquity = equity;
        }

        double drawdown = peakEquity - equity;
        double drawdownPct = peakEquity > 0 ? drawdown / peakEquity : 0.0;

        equityHistory.add(new EquityPoint(time, equity, drawdown, drawdownPct));
    }

    /**
     * Calculates a position size based on available cash and risk percentage.
     *
     * @param price       the current price per unit
     * @param riskPercent the fraction of cash to risk (e.g., 0.02 for 2%)
     * @return the number of units to trade
     */
    public double calculatePositionSize(double price, double riskPercent) {
        if (price <= 0) {
            return 0.0;
        }
        return (cash * riskPercent) / price;
    }

    // --- Getters ---

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

    /**
     * Returns true if there are any open positions.
     */
    public boolean hasOpenPositions() {
        return !openPositions.isEmpty();
    }

    /**
     * Finds the first open position for the given instrument and side, if any.
     *
     * @param instrumentId the instrument identifier
     * @param side         the position side to match
     * @return the matching position, or null if not found
     */
    public Position findOpenPosition(long instrumentId, OrderSide side) {
        for (Position position : openPositions) {
            if (position.getInstrumentId() == instrumentId && position.getSide() == side) {
                return position;
            }
        }
        return null;
    }
}
