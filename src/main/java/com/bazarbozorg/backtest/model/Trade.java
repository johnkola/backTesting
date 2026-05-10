package com.bazarbozorg.backtest.model;

import com.bazarbozorg.backtest.model.enums.OrderSide;

import java.time.ZonedDateTime;

/**
 * A completed round-trip trade (entry + exit), immutable. Build from a
 * closed {@link Position} via {@link #from}.
 */
public record Trade(String id,
                    long instrumentId,
                    OrderSide side,
                    double entryPrice,
                    double exitPrice,
                    ZonedDateTime entryTime,
                    ZonedDateTime exitTime,
                    double quantity,
                    double pnl,
                    double commission,
                    int holdingBars) {

    /** Builds a Trade from a closed Position. */
    public static Trade from(Position position, int holdingBars) {
        if (position.open()) {
            throw new IllegalArgumentException(
                    "Cannot create Trade from an open Position: " + position.id());
        }
        return new Trade(
                position.id(),
                position.instrumentId(),
                position.side(),
                position.entryPrice(),
                position.exitPrice(),
                position.entryTime(),
                position.exitTime(),
                position.quantity(),
                position.realizedPnl(),
                position.commission(),
                holdingBars);
    }

    public boolean isWin() {
        return pnl > 0;
    }

    /** Return percentage as a decimal (0.05 == 5%). */
    public double returnPct() {
        if (entryPrice == 0) {
            return 0.0;
        }
        return side == OrderSide.BUY
                ? (exitPrice - entryPrice) / entryPrice
                : (entryPrice - exitPrice) / entryPrice;
    }
}
