package com.bazarbozorg.backtest.engine;

import com.bazarbozorg.backtest.data.BarSeriesConverter;
import com.bazarbozorg.backtest.model.Candle;
import com.bazarbozorg.backtest.model.Trade;
import com.bazarbozorg.backtest.model.commission.FixedCommission;
import com.bazarbozorg.backtest.model.enums.Timeframe;
import com.bazarbozorg.backtest.model.slippage.PercentageSlippage;
import com.google.gson.JsonParser;
import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Test;
import org.ta4j.core.Bar;
import org.ta4j.core.BarSeries;

import java.time.LocalDate;
import java.time.ZoneOffset;
import java.util.ArrayList;
import java.util.HashMap;
import java.util.List;
import java.util.Map;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertNull;
import static org.junit.jupiter.api.Assertions.assertTrue;

/**
 * Faber and dual momentum beside a result (B7, G33): the loader's decisions,
 * traded by the engine with the run's own costs.
 */
class RuleBenchmarkTest {

    private static final double CAPITAL = 10_000.0;
    private static final String[] DAYS = {"2024-01-29", "2024-01-30", "2024-01-31", "2024-02-01", "2024-02-02"};

    private static BarSeries series(String name, double[] opens, double[] closes) {
        List<Candle> candles = new ArrayList<>();
        for (int i = 0; i < opens.length; i++) {
            candles.add(new Candle(0, 1, 1, Timeframe.D1,
                    LocalDate.parse(DAYS[i]).atStartOfDay(ZoneOffset.UTC),
                    opens[i], Math.max(opens[i], closes[i]), Math.min(opens[i], closes[i]), closes[i], 0));
        }
        return BarSeriesConverter.convert(name, candles);
    }

    private static final BarSeries QQQ = series("QQQ",
            new double[] {100, 101, 103, 102, 104}, new double[] {100.5, 102, 102.5, 103, 106});
    private static final BarSeries SPY_SERIES = series("SPY",
            new double[] {50, 51, 52, 53, 54}, new double[] {50.5, 51.5, 52.5, 53.5, 55});

    private static Map<LocalDate, Bar> spy() {
        Map<LocalDate, Bar> m = new HashMap<>();
        for (int i = 0; i < SPY_SERIES.getBarCount(); i++) {
            m.put(BacktestEngine.barDate(SPY_SERIES.getBar(i)).toLocalDate(), SPY_SERIES.getBar(i));
        }
        return m;
    }

    private static String body(String faberCalls, String momentumCalls, String bills) {
        return "{\"dates\":[\"" + String.join("\",\"", DAYS) + "\"],"
                + "\"faber\":" + faberCalls + ",\"dualMomentum\":" + momentumCalls
                + ",\"bills\":" + bills + "}";
    }

    private static List<RuleBenchmark> score(String json, double commission) {
        BacktestEngine engine = new BacktestEngine(null, new FixedCommission(commission),
                new PercentageSlippage(0.0), CAPITAL);
        return engine.scoreRules(JsonParser.parseString(json).getAsJsonObject(), QQQ,
                Timeframe.D1, 0, 1L, "QQQ", spy());
    }

    @Test
    @DisplayName("Faber is traded like the strategy: long at the next open, closed at the end")
    void faberTradesAtTheNextOpen() {
        String faber = "{\"available\":true,\"calls\":[\"QQQ\",\"QQQ\",null,\"QQQ\",\"QQQ\"]}";
        RuleBenchmark f = score(body(faber, "{\"available\":false,\"reason\":\"x\"}", "null"), 0.0).get(0);
        assertTrue(f.available());
        List<Trade> t = f.trades();
        assertEquals(2, t.size());
        assertEquals(101.0, t.get(0).entryPrice(), 1e-12);   // bar 0's call, bar 1's open
        assertEquals(102.0, t.get(0).exitPrice(), 1e-12);    // bar 2's cash call, bar 3's open
        assertEquals(104.0, t.get(1).entryPrice(), 1e-12);   // bar 3's call, bar 4's open
        assertEquals(106.0, t.get(1).exitPrice(), 1e-12);    // closed at the last close
        assertEquals(CAPITAL * 102 / 101 * 106 / 104, f.finalEquity(), 1e-9);
        assertEquals(QQQ.getBarCount(), f.equity().size());
    }

    @Test
    @DisplayName("dual momentum switches to SPY at the next open, paying commission both ways")
    void dualMomentumSwitches() {
        String momentum = "{\"available\":true,\"calls\":[\"QQQ\",\"SPY\",\"SPY\",\"SPY\",\"SPY\"]}";
        RuleBenchmark d = score(body("{\"available\":false,\"reason\":\"x\"}", momentum, "null"), 1.0).get(1);
        assertTrue(d.available());
        List<Trade> t = d.trades();
        assertEquals(2, t.size());
        assertEquals(101.0, t.get(0).entryPrice(), 1e-12);   // QQQ at bar 1's open
        assertEquals(103.0, t.get(0).exitPrice(), 1e-12);    // sold at bar 2's open
        assertEquals(52.0, t.get(1).entryPrice(), 1e-12);    // SPY at bar 2's open
        assertEquals(55.0, t.get(1).exitPrice(), 1e-12);
        // Each buy is sized with its commission inside the cash (B2), so cash is 0
        // after it, never below.
        double afterQqq = (CAPITAL - 1.0) / 101 * 103 - 1.0;
        assertEquals((afterQqq - 1.0) / 52 * 55 - 1.0, d.finalEquity(), 1e-6);
    }

    @Test
    @DisplayName("dual momentum's cash earns T-bills; Faber's earns nothing")
    void cashEarnsTBillsForDualMomentumOnly() {
        String cash = "{\"available\":true,\"calls\":[null,null,null,null,null]}";
        List<RuleBenchmark> both = score(body(cash, cash, "[0.001,0.001,0.001,0.001,0.001]"), 0.0);
        assertEquals(CAPITAL, both.get(0).finalEquity(), 1e-9);
        assertEquals(CAPITAL * Math.pow(1.001, 5), both.get(1).finalEquity(), 1e-6);
    }

    @Test
    @DisplayName("a rule the loader could not decide is not available, with its reason and no figures")
    void notAvailableCarriesTheReason() {
        String no = "{\"available\":false,\"reason\":\"needs 10 month-end closes before 2024-01-29\"}";
        RuleBenchmark f = score(body(no, no, "null"), 0.0).get(0);
        assertFalse(f.available());
        assertEquals("needs 10 month-end closes before 2024-01-29", f.reason());
        assertNull(f.metrics());
        assertNull(f.finalEquity());
    }

    @Test
    @DisplayName("the five indicator rules are scored beside the two, in order, from the same payload")
    void indicatorRulesAreScoredToo() {
        String no = "{\"available\":false,\"reason\":\"x\"}";
        String held = "{\"available\":true,\"calls\":[\"QQQ\",\"QQQ\",null,\"QQQ\",\"QQQ\"],"
                + "\"settings\":{\"period\":14}}";
        String early = "{\"available\":false,\"reason\":\"needs 201 daily closes of QQQ\"}";
        String json = body(no, no, "[0.001,0.001,0.001,0.001,0.001]");
        json = json.substring(0, json.length() - 1) + ",\"indicatorRules\":{\"rsi\":" + held
                + ",\"sma-crossover\":" + early + "}}";
        List<RuleBenchmark> all = score(json, 0.0);
        assertEquals(List.of("faber", "dual_momentum", "sma-crossover", "ema-triple", "macd",
                "rsi", "bollinger"), all.stream().map(RuleBenchmark::name).toList());
        RuleBenchmark rsi = all.get(5);
        assertTrue(rsi.available());
        // Traded exactly as Faber is on the same calls, and its cash earns nothing.
        assertEquals(CAPITAL * 102 / 101 * 106 / 104, rsi.finalEquity(), 1e-9);
        assertEquals(2, rsi.trades().size());
        assertEquals("needs 201 daily closes of QQQ", all.get(2).reason());
        // A rule the loader's payload does not carry is not available, never a zero.
        assertFalse(all.get(4).available());
        assertTrue(all.get(4).reason().contains("did not serve"));
        assertNull(all.get(4).metrics());
    }

    @Test
    @DisplayName("a payload from before the indicator rules still scores the two")
    void anOlderPayloadStillScoresTheTwo() {
        String faber = "{\"available\":true,\"calls\":[\"QQQ\",\"QQQ\",null,\"QQQ\",\"QQQ\"]}";
        List<RuleBenchmark> all = score(body(faber, "{\"available\":false,\"reason\":\"x\"}", "null"), 0.0);
        assertEquals(7, all.size());
        assertTrue(all.get(0).available());
        assertTrue(all.subList(2, 7).stream().noneMatch(RuleBenchmark::available));
    }

    @Test
    @DisplayName("a result that is not daily has no month ends to decide on")
    void notDailyIsNotAvailable() {
        BacktestEngine engine = new BacktestEngine(null, new FixedCommission(0.0),
                new PercentageSlippage(0.0), CAPITAL);
        List<RuleBenchmark> r = engine.ruleBenchmarks(QQQ, Timeframe.H1, 0, null, null);
        assertEquals(7, r.size());
        assertTrue(r.stream().noneMatch(RuleBenchmark::available));
        assertTrue(r.get(1).reason().contains("daily bars"));
        assertTrue(r.get(6).reason().contains("daily bars"));
    }
}
