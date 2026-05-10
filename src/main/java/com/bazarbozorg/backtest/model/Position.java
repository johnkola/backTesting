package com.bazarbozorg.backtest.model;

import com.bazarbozorg.backtest.model.enums.OrderSide;

import java.time.ZonedDateTime;
import java.util.List;
import java.util.UUID;

/**
 * An open or closed trading position, immutable. Lifecycle is open
 * ({@link #open}) → closed ({@link #closed}). The compact constructor copies
 * {@code orderIds} so callers cannot mutate the record's list.
 */
public record Position(String id,
                       long instrumentId,
                       OrderSide side,
                       double entryPrice,
                       double quantity,
                       ZonedDateTime entryTime,
                       ZonedDateTime exitTime,
                       double exitPrice,
                       boolean open,
                       double realizedPnl,
                       double commission,
                       List<String> orderIds) {

    public Position {
        orderIds = List.copyOf(orderIds);
    }

    /** Creates a fresh, open position. */
    public static Position open(long instrumentId, OrderSide side, double entryPrice,
                                 double quantity, ZonedDateTime entryTime,
                                 double commission, String entryOrderId) {
        return new Position(UUID.randomUUID().toString(), instrumentId, side,
                entryPrice, quantity, entryTime,
                null, 0.0, true, 0.0, commission,
                entryOrderId == null ? List.of() : List.of(entryOrderId));
    }

    /**
     * Returns a closed copy of this position. Computes realized P&amp;L from
     * the entry/exit prices and side.
     */
    public Position closed(double exitPrice, ZonedDateTime exitTime) {
        if (!open) {
            throw new IllegalStateException("Position is already closed: " + id);
        }
        double pnl = isLong()
                ? (exitPrice - entryPrice) * quantity
                : (entryPrice - exitPrice) * quantity;
        return new Position(id, instrumentId, side, entryPrice, quantity, entryTime,
                exitTime, exitPrice, false, pnl, commission, orderIds);
    }

    /** Returns a copy with the given commission. */
    public Position withCommission(double commission) {
        return new Position(id, instrumentId, side, entryPrice, quantity, entryTime,
                exitTime, exitPrice, open, realizedPnl, commission, orderIds);
    }

    /** Returns a copy with {@code orderId} appended to the order-id history. */
    public Position withOrderId(String orderId) {
        if (orderId == null) {
            return this;
        }
        java.util.List<String> next = new java.util.ArrayList<>(orderIds.size() + 1);
        next.addAll(orderIds);
        next.add(orderId);
        return new Position(id, instrumentId, side, entryPrice, quantity, entryTime,
                exitTime, exitPrice, open, realizedPnl, commission, next);
    }

    /** Mark-to-market unrealized P&amp;L at {@code currentPrice}. Zero if closed. */
    public double unrealizedPnl(double currentPrice) {
        if (!open) {
            return 0.0;
        }
        return isLong()
                ? (currentPrice - entryPrice) * quantity
                : (entryPrice - currentPrice) * quantity;
    }

    public boolean isLong() {
        return side == OrderSide.BUY;
    }

    public boolean isShort() {
        return side == OrderSide.SELL;
    }
}
