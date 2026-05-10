package com.bazarbozorg.backtest.model;

import com.bazarbozorg.backtest.model.enums.OrderSide;
import com.bazarbozorg.backtest.model.enums.OrderStatus;
import com.bazarbozorg.backtest.model.enums.OrderType;

import java.time.ZonedDateTime;
import java.util.UUID;

/**
 * An order, immutable. A pending order is created via {@link #market} /
 * {@link #limit}; the engine produces a filled copy via {@link #filled}
 * once {@code ExecutionSimulator} has run. Old mutator-style code paths are
 * gone &mdash; reassign the variable instead.
 */
public record Order(String id,
                    long instrumentId,
                    OrderType type,
                    OrderSide side,
                    double quantity,
                    double requestedPrice,
                    Double limitPrice,
                    Double stopPrice,
                    OrderStatus status,
                    ZonedDateTime createdAt,
                    ZonedDateTime filledAt,
                    double filledPrice,
                    double commission,
                    double slippage) {

    /** Pending market order. */
    public static Order market(long instrumentId, OrderSide side,
                                double quantity, double requestedPrice) {
        return new Order(UUID.randomUUID().toString(), instrumentId, OrderType.MARKET, side,
                quantity, requestedPrice, null, null,
                OrderStatus.PENDING, ZonedDateTime.now(), null, 0.0, 0.0, 0.0);
    }

    /** Pending limit or stop order. */
    public static Order limit(long instrumentId, OrderType type, OrderSide side,
                               double quantity, double requestedPrice,
                               Double limitPrice, Double stopPrice) {
        if (type == OrderType.MARKET) {
            throw new IllegalArgumentException(
                    "Use Order.market(...) for MARKET orders");
        }
        return new Order(UUID.randomUUID().toString(), instrumentId, type, side,
                quantity, requestedPrice, limitPrice, stopPrice,
                OrderStatus.PENDING, ZonedDateTime.now(), null, 0.0, 0.0, 0.0);
    }

    /** Returns a copy with status FILLED and the given fill details populated. */
    public Order filled(ZonedDateTime filledAt, double filledPrice,
                         double commission, double slippage) {
        return new Order(id, instrumentId, type, side, quantity, requestedPrice,
                limitPrice, stopPrice,
                OrderStatus.FILLED, createdAt, filledAt, filledPrice, commission, slippage);
    }
}
