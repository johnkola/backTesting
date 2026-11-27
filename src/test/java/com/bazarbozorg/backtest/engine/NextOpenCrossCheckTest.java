package com.bazarbozorg.backtest.engine;

import com.bazarbozorg.backtest.data.BarSeriesConverter;
import com.bazarbozorg.backtest.model.Candle;
import com.bazarbozorg.backtest.model.StrategyContext;
import com.bazarbozorg.backtest.model.Trade;
import com.bazarbozorg.backtest.model.commission.FixedCommission;
import com.bazarbozorg.backtest.model.enums.StrategySignal;
import com.bazarbozorg.backtest.model.enums.Timeframe;
import com.bazarbozorg.backtest.model.slippage.PercentageSlippage;
import com.bazarbozorg.backtest.strategy.TradingStrategy;
import com.google.gson.JsonArray;
import com.google.gson.JsonElement;
import com.google.gson.JsonObject;
import com.google.gson.JsonParser;
import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Test;
import org.ta4j.core.BarSeries;

import java.io.IOException;
import java.nio.file.Files;
import java.nio.file.Path;
import java.time.LocalDate;
import java.time.ZoneOffset;
import java.util.ArrayList;
import java.util.List;
import java.util.Map;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertTrue;

/**
 * B1 (R76): a signal computed from a bar's close fills at the next bar's open.
 *
 * <p>{@code src/test/resources/crosscheck/next-open.json} holds bars, one
 * long-or-flat call per bar, and the trades and return they must give with
 * costs off. The walk-forward scorer is held to the same file by
 * {@code python/tests/test_next_open_crosscheck.py}, so a backtest result and a
 * walk-forward fold trade the same days at the same prices.
 */
class NextOpenCrossCheckTest {

    private static final double CAPITAL = 10_000.0;

    private static JsonObject fixture() throws IOException {
        return JsonParser.parseString(Files.readString(
                Path.of("src/test/resources/crosscheck/next-open.json"))).getAsJsonObject();
    }

    private static BarSeries series(JsonArray bars) {
        List<Candle> candles = new ArrayList<>();
        for (JsonElement e : bars) {
            JsonObject b = e.getAsJsonObject();
            double open = b.get("open").getAsDouble();
            double close = b.get("close").getAsDouble();
            candles.add(new Candle(0, 1, 1, Timeframe.D1,
                    LocalDate.parse(b.get("date").getAsString()).atStartOfDay(ZoneOffset.UTC),
                    open, Math.max(open, close), Math.min(open, close), close, 0));
        }
        return BarSeriesConverter.convert("XCHK", candles);
    }

    /** Replays the calls: enter on a long call after a flat one, exit on the reverse. */
    private static TradingStrategy replay(boolean[] longs) {
        return new TradingStrategy() {
            public String getName() { return "replay"; }
            public String getDescription() { return "replays fixed calls"; }
            public Map<String, String> getDefaultParameters() { return Map.of(); }
            public void initialize(BarSeries s, Map<String, String> p) { }
            public int getWarmupBars() { return 0; }
            public StrategySignal evaluate(StrategyContext ctx) {
                int i = ctx.currentBarIndex();
                boolean was = i > 0 && longs[i - 1];
                if (longs[i] && !was) return StrategySignal.ENTRY_LONG;
                if (!longs[i] && was) return StrategySignal.EXIT_LONG;
                return StrategySignal.HOLD;
            }
        };
    }

    private static boolean[] longs(JsonArray a) {
        boolean[] out = new boolean[a.size()];
        for (int i = 0; i < out.length; i++) out[i] = a.get(i).getAsBoolean();
        return out;
    }

    private static PortfolioManager run(BarSeries s, boolean[] calls) {
        return run(s, calls, 0.0);
    }

    private static PortfolioManager run(BarSeries s, boolean[] calls, double taxRate) {
        PortfolioManager pm = new PortfolioManager(CAPITAL, taxRate);
        BacktestEngine.trade(s, replay(calls), 0, 1L, pm,
                new ExecutionSimulator(new FixedCommission(0.0), new PercentageSlippage(0.0)));
        return pm;
    }

    @Test
    @DisplayName("the engine makes the fixture's trades, at the next open, and its return")
    void engineMatchesTheFixture() throws IOException {
        JsonObject f = fixture();
        BarSeries s = series(f.getAsJsonArray("bars"));
        PortfolioManager pm = run(s, longs(f.getAsJsonArray("long")));

        JsonArray expected = f.getAsJsonObject("expected").getAsJsonArray("trades");
        List<Trade> trades = pm.getCompletedTrades();
        assertEquals(expected.size(), trades.size());
        for (int i = 0; i < trades.size(); i++) {
            JsonObject want = expected.get(i).getAsJsonObject();
            Trade got = trades.get(i);
            // Stamped with the bar's own date, the one its prices are stored under.
            assertEquals(want.get("entry").getAsString(),
                    got.entryTime().toLocalDate().toString(), "entry date of trade " + i);
            assertEquals(want.get("exit").getAsString(),
                    got.exitTime().toLocalDate().toString(), "exit date of trade " + i);
            assertEquals(want.get("entryPrice").getAsDouble(), got.entryPrice(), 1e-12);
            assertEquals(want.get("exitPrice").getAsDouble(), got.exitPrice(), 1e-12);
        }
        double returnPct = (pm.getCash() / CAPITAL - 1.0) * 100.0;
        assertEquals(f.getAsJsonObject("expected").get("returnPct").getAsDouble(), returnPct, 1e-9);
    }

    @Test
    @DisplayName("no fill at the close a signal was computed from, and none for the last bar's")
    void neverAtTheSignalsOwnClose() throws IOException {
        BarSeries s = series(fixture().getAsJsonArray("bars"));
        int n = s.getBarCount();

        boolean[] onlyFirst = new boolean[n];
        onlyFirst[0] = true;
        Trade first = run(s, onlyFirst).getCompletedTrades().get(0);
        assertEquals(s.getBar(1).getOpenPrice().doubleValue(), first.entryPrice(), 1e-12,
                "bar 0's call fills at bar 1's open, not bar 0's close");

        boolean[] onlyLast = new boolean[n];
        onlyLast[n - 1] = true;
        PortfolioManager last = run(s, onlyLast);
        assertTrue(last.getCompletedTrades().isEmpty(), "the last bar's call has no next open");
        assertEquals(CAPITAL, last.getCash(), 1e-9);
    }

    @Test
    @DisplayName("G20: after tax, the engine makes the walk-forward scorer's return and tax")
    void taxedMatchesTheScorer() throws IOException {
        JsonObject f = fixture();
        JsonObject taxed = f.getAsJsonObject("taxed");
        BarSeries s = series(f.getAsJsonArray("bars"));
        boolean[] calls = longs(taxed.getAsJsonArray("long"));
        double rate = taxed.get("taxRatePct").getAsDouble() / 100.0;

        PortfolioManager before = run(s, calls);
        assertEquals(taxed.get("returnPctBeforeTax").getAsDouble(),
                (before.getCash() / CAPITAL - 1.0) * 100.0, 1e-9);

        PortfolioManager after = run(s, calls, rate);
        assertEquals(taxed.get("fills").getAsInt(), 2 * after.getCompletedTrades().size());
        assertEquals(taxed.get("returnPct").getAsDouble(), (after.getCash() / CAPITAL - 1.0) * 100.0, 1e-9);
        assertEquals(taxed.get("taxPaidPct").getAsDouble(), after.getTaxPaid() / CAPITAL * 100.0, 1e-9);
        // Two gains taxed at their sale, a loss carried untaxed, the last sale
        // taxed net of it: four trades, three paying.
        assertEquals(3, after.getCompletedTrades().stream().filter(t -> t.tax() > 0).count());
    }
}
