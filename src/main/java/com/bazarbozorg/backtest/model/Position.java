package com.bazarbozorg.backtest.model;

import java.time.ZonedDateTime;
import java.util.ArrayList;
import java.util.Collections;
import java.util.List;
import java.util.UUID;

/**
 * Represents an open or closed trading position.
 * A position is created when an entry order is filled and closed when an exit order is filled.
 * Tracks entry/exit prices, P&L, commissions, and associated order IDs.
 */
public class Position {

    private final String id;
    private final long instrumentId;
    private final OrderSide side;
    private final double entryPrice;
    private final double quantity;
    private final ZonedDateTime entryTime;

    private ZonedDateTime exitTime;
    private double exitPrice;
    private boolean open;
    private double realizedPnl;
    private double commission;
    private final List<String> orderIds;

    /**
     * Creates a new open position.
     *
     * @param instrumentId the instrument identifier
     * @param side         the position side (BUY for long, SELL for short)
     * @param entryPrice   the entry price
     * @param quantity     the position size
     * @param entryTime    the time the position was opened
     */
    public Position(long instrumentId, OrderSide side, double entryPrice,
                    double quantity, ZonedDateTime entryTime) {
        this.id = UUID.randomUUID().toString();
        this.instrumentId = instrumentId;
        this.side = side;
        this.entryPrice = entryPrice;
        this.quantity = quantity;
        this.entryTime = entryTime;
        this.exitTime = null;
        this.exitPrice = 0.0;
        this.open = true;
        this.realizedPnl = 0.0;
        this.commission = 0.0;
        this.orderIds = new ArrayList<>();
    }

    /**
     * Closes this position at the given exit price and time.
     * Calculates the realized P&L based on position side:
     * - LONG (BUY): (exitPrice - entryPrice) * quantity
     * - SHORT (SELL): (entryPrice - exitPrice) * quantity
     *
     * @param exitPrice the exit price
     * @param exitTime  the time the position was closed
     */
    public void close(double exitPrice, ZonedDateTime exitTime) {
        if (!this.open) {
            throw new IllegalStateException("Position is already closed: " + id);
        }
        this.exitPrice = exitPrice;
        this.exitTime = exitTime;
        this.open = false;

        if (isLong()) {
            this.realizedPnl = (exitPrice - entryPrice) * quantity;
        } else {
            this.realizedPnl = (entryPrice - exitPrice) * quantity;
        }
    }

    /**
     * Calculates the unrealized P&L at the given current market price.
     *
     * @param currentPrice the current market price
     * @return the unrealized P&L
     */
    public double getUnrealizedPnl(double currentPrice) {
        if (!open) {
            return 0.0;
        }
        if (isLong()) {
            return (currentPrice - entryPrice) * quantity;
        } else {
            return (entryPrice - currentPrice) * quantity;
        }
    }

    /**
     * Returns true if this is a long position (BUY side).
     */
    public boolean isLong() {
        return side == OrderSide.BUY;
    }

    /**
     * Returns true if this is a short position (SELL side).
     */
    public boolean isShort() {
        return side == OrderSide.SELL;
    }

    /**
     * Adds an order ID to the list of orders associated with this position.
     *
     * @param orderId the order ID to add
     */
    public void addOrderId(String orderId) {
        this.orderIds.add(orderId);
    }

    // --- Getters ---

    public String getId() {
        return id;
    }

    public long getInstrumentId() {
        return instrumentId;
    }

    public OrderSide getSide() {
        return side;
    }

    public double getEntryPrice() {
        return entryPrice;
    }

    public double getQuantity() {
        return quantity;
    }

    public ZonedDateTime getEntryTime() {
        return entryTime;
    }

    public ZonedDateTime getExitTime() {
        return exitTime;
    }

    public double getExitPrice() {
        return exitPrice;
    }

    public boolean isOpen() {
        return open;
    }

    public double getRealizedPnl() {
        return realizedPnl;
    }

    public double getCommission() {
        return commission;
    }

    public void setCommission(double commission) {
        this.commission = commission;
    }

    public List<String> getOrderIds() {
        return Collections.unmodifiableList(orderIds);
    }

    @Override
    public String toString() {
        return "Position{" +
                "id='" + id + '\'' +
                ", instrumentId=" + instrumentId +
                ", side=" + side +
                ", entryPrice=" + entryPrice +
                ", quantity=" + quantity +
                ", entryTime=" + entryTime +
                ", exitTime=" + exitTime +
                ", exitPrice=" + exitPrice +
                ", open=" + open +
                ", realizedPnl=" + realizedPnl +
                ", commission=" + commission +
                '}';
    }
}
