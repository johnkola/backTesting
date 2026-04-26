package com.bazarbozorg.backtest.model;

import java.time.ZonedDateTime;

/**
 * Represents a completed round-trip trade (entry + exit).
 * Created from a closed {@link Position} to capture final trade metrics.
 */
public class Trade {

    private final String id;
    private final long instrumentId;
    private final OrderSide side;
    private final double entryPrice;
    private final double exitPrice;
    private final ZonedDateTime entryTime;
    private final ZonedDateTime exitTime;
    private final double quantity;
    private final double pnl;
    private final double commission;
    private final int holdingBars;

    /**
     * Creates a Trade from a closed Position.
     *
     * @param position    the closed position
     * @param holdingBars the number of bars the position was held
     * @throws IllegalArgumentException if the position is still open
     */
    public Trade(Position position, int holdingBars) {
        if (position.isOpen()) {
            throw new IllegalArgumentException("Cannot create Trade from an open Position: " + position.getId());
        }
        this.id = position.getId();
        this.instrumentId = position.getInstrumentId();
        this.side = position.getSide();
        this.entryPrice = position.getEntryPrice();
        this.exitPrice = position.getExitPrice();
        this.entryTime = position.getEntryTime();
        this.exitTime = position.getExitTime();
        this.quantity = position.getQuantity();
        this.pnl = position.getRealizedPnl();
        this.commission = position.getCommission();
        this.holdingBars = holdingBars;
    }

    /**
     * Returns true if this trade was profitable (P&L > 0).
     */
    public boolean isWin() {
        return pnl > 0;
    }

    /**
     * Calculates the return percentage of this trade.
     * For long trades: (exitPrice - entryPrice) / entryPrice
     * For short trades: (entryPrice - exitPrice) / entryPrice
     *
     * @return the return as a decimal (e.g., 0.05 for 5%)
     */
    public double getReturnPct() {
        if (entryPrice == 0) {
            return 0.0;
        }
        if (side == OrderSide.BUY) {
            return (exitPrice - entryPrice) / entryPrice;
        } else {
            return (entryPrice - exitPrice) / entryPrice;
        }
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

    public double getExitPrice() {
        return exitPrice;
    }

    public ZonedDateTime getEntryTime() {
        return entryTime;
    }

    public ZonedDateTime getExitTime() {
        return exitTime;
    }

    public double getQuantity() {
        return quantity;
    }

    public double getPnl() {
        return pnl;
    }

    public double getCommission() {
        return commission;
    }

    public int getHoldingBars() {
        return holdingBars;
    }

    @Override
    public String toString() {
        return "Trade{" +
                "id='" + id + '\'' +
                ", instrumentId=" + instrumentId +
                ", side=" + side +
                ", entryPrice=" + entryPrice +
                ", exitPrice=" + exitPrice +
                ", entryTime=" + entryTime +
                ", exitTime=" + exitTime +
                ", quantity=" + quantity +
                ", pnl=" + pnl +
                ", commission=" + commission +
                ", holdingBars=" + holdingBars +
                '}';
    }
}
