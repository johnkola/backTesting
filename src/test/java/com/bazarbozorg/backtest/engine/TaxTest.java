package com.bazarbozorg.backtest.engine;

import com.bazarbozorg.backtest.data.BarSeriesConverter;
import com.bazarbozorg.backtest.model.Candle;
import com.bazarbozorg.backtest.model.Trade;
import com.bazarbozorg.backtest.model.commission.FixedCommission;
import com.bazarbozorg.backtest.model.enums.OrderSide;
import com.bazarbozorg.backtest.model.enums.Timeframe;
import com.bazarbozorg.backtest.model.slippage.PercentageSlippage;
import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Test;
import org.ta4j.core.BarSeries;

import java.time.LocalDate;
import java.time.ZoneOffset;
import java.time.ZonedDateTime;
import java.util.ArrayList;
import java.util.List;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertThrows;

/**
 * Tax on realized gains in the engine's accounting (G20): the walk-forward
 * scorer's rule in money terms, paid by the strategy and its benchmarks alike.
 */
class TaxTest {

    private static final ZonedDateTime T = LocalDate.parse("2024-01-02").atStartOfDay(ZoneOffset.UTC);

    private static PortfolioManager pm(double rate) {
        return new PortfolioManager(10_000.0, rate);
    }

    @Test
    @DisplayName("a gain is taxed at its sale, net of both commissions, and paid from cash")
    void gainTaxedNetOfCommissions() {
        PortfolioManager pm = pm(0.25);
        var p = pm.openPosition(1, OrderSide.BUY, 100.0, 90.0, T, 5.0, null);
        Trade t = pm.closePosition(p, 110.0, T.plusDays(1), 5.0);
        double gain = 10.0 * 90.0 - 10.0;   // price gain less both commissions
        assertEquals(0.25 * gain, t.tax(), 1e-9);
        assertEquals(10_000.0 - 9_000.0 - 5.0 + 9_900.0 - 5.0 - 0.25 * gain, pm.getCash(), 1e-9);
        assertEquals(0.25 * gain, pm.getTaxPaid(), 1e-9);
    }

    @Test
    @DisplayName("a loss carries to offset the next gain; a short's gain is taxed the same way")
    void lossCarriesAndShortsAreTaxed() {
        PortfolioManager pm = pm(0.25);
        var a = pm.openPosition(1, OrderSide.BUY, 100.0, 10.0, T, 0.0, null);
        Trade loss = pm.closePosition(a, 90.0, T.plusDays(1), 0.0);           // -100
        assertEquals(0.0, loss.tax());
        var b = pm.openPosition(1, OrderSide.SELL, 100.0, 10.0, T.plusDays(2), 0.0, null);
        Trade shortGain = pm.closePosition(b, 70.0, T.plusDays(3), 0.0);      // +300 on the short
        assertEquals(0.25 * (300.0 - 100.0), shortGain.tax(), 1e-9);
        assertEquals(50.0, pm.getTaxPaid(), 1e-9);
    }

    @Test
    @DisplayName("at no rate nothing changes; a rate outside [0, 1) is refused")
    void noRateIsBeforeTax() {
        PortfolioManager taxed = pm(0.0);
        PortfolioManager plain = new PortfolioManager(10_000.0);
        for (PortfolioManager pm : List.of(taxed, plain)) {
            var p = pm.openPosition(1, OrderSide.BUY, 100.0, 50.0, T, 1.0, null);
            pm.closePosition(p, 120.0, T.plusDays(1), 1.0);
        }
        assertEquals(plain.getCash(), taxed.getCash());
        assertEquals(0.0, taxed.getTaxPaid());
        assertThrows(IllegalArgumentException.class, () -> pm(1.0));
        assertThrows(IllegalArgumentException.class, () -> pm(-0.1));
        assertThrows(IllegalArgumentException.class,
                () -> new BacktestEngine(null, new FixedCommission(0), new PercentageSlippage(0), 1).withTaxRate(0.0));
    }

    @Test
    @DisplayName("buy-and-hold pays the same tax on its one sale at the end (R36, D8)")
    void buyAndHoldIsTaxedAtTheEnd() {
        List<Candle> candles = new ArrayList<>();
        double[] closes = {100, 104, 108, 112, 120};
        for (int i = 0; i < closes.length; i++) {
            candles.add(new Candle(0, 1, 1, Timeframe.D1, T.plusDays(i), closes[i], closes[i],
                    closes[i], closes[i], 0));
        }
        BarSeries s = BarSeriesConverter.convert("TAX", candles);
        BacktestEngine engine = new BacktestEngine(null, new FixedCommission(0.0),
                new PercentageSlippage(0.0), 10_000.0);
        BuyAndHold before = engine.buyAndHold(s, Timeframe.D1, 0, 1L, 120.0);
        BuyAndHold after = engine.withTaxRate(25.0).buyAndHold(s, Timeframe.D1, 0, 1L, 120.0);
        double gain = before.finalEquity() - 10_000.0;
        assertEquals(before.finalEquity() - 0.25 * gain, after.finalEquity(), 1e-9);
        assertEquals(0.25 * gain, after.metrics().getTotalTax(), 1e-9);
        assertEquals(0.0, before.metrics().getTotalTax());
    }
}
