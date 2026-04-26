package com.bazarbozorg.backtest.model;

import java.time.ZonedDateTime;
import java.util.UUID;

public class Order {

    private final String id;
    private final long instrumentId;
    private final OrderType type;
    private final OrderSide side;
    private final double quantity;
    private final double requestedPrice;
    private final Double limitPrice;
    private final Double stopPrice;

    private OrderStatus status;
    private final ZonedDateTime createdAt;
    private ZonedDateTime filledAt;
    private double filledPrice;
    private double commission;
    private double slippage;

    /**
     * Constructor for market orders.
     */
    public Order(long instrumentId, OrderSide side, double quantity, double requestedPrice) {
        this.id = UUID.randomUUID().toString();
        this.instrumentId = instrumentId;
        this.type = OrderType.MARKET;
        this.side = side;
        this.quantity = quantity;
        this.requestedPrice = requestedPrice;
        this.limitPrice = null;
        this.stopPrice = null;
        this.status = OrderStatus.PENDING;
        this.createdAt = ZonedDateTime.now();
        this.filledAt = null;
        this.filledPrice = 0.0;
        this.commission = 0.0;
        this.slippage = 0.0;
    }

    /**
     * Constructor for limit or stop orders.
     */
    public Order(long instrumentId, OrderType type, OrderSide side, double quantity,
                 double requestedPrice, Double limitPrice, Double stopPrice) {
        if (type == OrderType.MARKET) {
            throw new IllegalArgumentException(
                    "Use the market order constructor for MARKET orders");
        }
        this.id = UUID.randomUUID().toString();
        this.instrumentId = instrumentId;
        this.type = type;
        this.side = side;
        this.quantity = quantity;
        this.requestedPrice = requestedPrice;
        this.limitPrice = limitPrice;
        this.stopPrice = stopPrice;
        this.status = OrderStatus.PENDING;
        this.createdAt = ZonedDateTime.now();
        this.filledAt = null;
        this.filledPrice = 0.0;
        this.commission = 0.0;
        this.slippage = 0.0;
    }

    // --- Getters (immutable fields) ---

    public String getId() {
        return id;
    }

    public long getInstrumentId() {
        return instrumentId;
    }

    public OrderType getType() {
        return type;
    }

    public OrderSide getSide() {
        return side;
    }

    public double getQuantity() {
        return quantity;
    }

    public double getRequestedPrice() {
        return requestedPrice;
    }

    public Double getLimitPrice() {
        return limitPrice;
    }

    public Double getStopPrice() {
        return stopPrice;
    }

    public ZonedDateTime getCreatedAt() {
        return createdAt;
    }

    // --- Getters and Setters (mutable fields) ---

    public OrderStatus getStatus() {
        return status;
    }

    public void setStatus(OrderStatus status) {
        this.status = status;
    }

    public ZonedDateTime getFilledAt() {
        return filledAt;
    }

    public void setFilledAt(ZonedDateTime filledAt) {
        this.filledAt = filledAt;
    }

    public double getFilledPrice() {
        return filledPrice;
    }

    public void setFilledPrice(double filledPrice) {
        this.filledPrice = filledPrice;
    }

    public double getCommission() {
        return commission;
    }

    public void setCommission(double commission) {
        this.commission = commission;
    }

    public double getSlippage() {
        return slippage;
    }

    public void setSlippage(double slippage) {
        this.slippage = slippage;
    }

    @Override
    public String toString() {
        return "Order{" +
                "id='" + id + '\'' +
                ", instrumentId=" + instrumentId +
                ", type=" + type +
                ", side=" + side +
                ", quantity=" + quantity +
                ", requestedPrice=" + requestedPrice +
                ", limitPrice=" + limitPrice +
                ", stopPrice=" + stopPrice +
                ", status=" + status +
                ", createdAt=" + createdAt +
                ", filledAt=" + filledAt +
                ", filledPrice=" + filledPrice +
                ", commission=" + commission +
                ", slippage=" + slippage +
                '}';
    }
}
