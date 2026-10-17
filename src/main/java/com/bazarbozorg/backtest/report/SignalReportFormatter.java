package com.bazarbozorg.backtest.report;

import com.bazarbozorg.backtest.engine.SignalQualityReport;
import com.bazarbozorg.backtest.engine.SignalStance;
import com.bazarbozorg.backtest.util.DateTimeUtils;

import java.util.List;

/**
 * Formats a {@link SignalQualityReport} for the console.
 *
 * <p>The layout puts the baseline on its own line above the per-stance table,
 * because every number in that table is only meaningful relative to it. A
 * BULLISH mean of +0.9% reads like a result until you see the baseline is
 * +1.1%, at which point it is a signal that loses money relative to doing
 * nothing. The verdict line at the bottom states that comparison outright
 * rather than leaving the reader to do the subtraction.
 */
public class SignalReportFormatter {

    private static final String SECTION_SEPARATOR = "=".repeat(64);
    private static final String SUBSECTION_SEPARATOR = "-".repeat(64);
    private static final int RECENT_CALLS_LIMIT = 10;

    public String formatReport(SignalQualityReport r) {
        StringBuilder sb = new StringBuilder();

        sb.append('\n').append(SECTION_SEPARATOR).append('\n');
        sb.append("              STRATEGY 1 -- SIGNALS ONLY\n");
        sb.append(SECTION_SEPARATOR).append('\n');
        sb.append(String.format("Strategy      : %s%n", r.strategyName()));
        sb.append(String.format("Instrument    : %s (%s, %s)%n",
                r.instrumentSymbol(), r.timeframe(), r.dataSource()));
        sb.append(String.format("Range         : %s .. %s%n",
                fmtDate(r.startDate()), fmtDate(r.endDate())));
        sb.append(String.format("Horizon       : %d bars%n", r.horizonBars()));
        sb.append(String.format("Calls         : %d emitted, %d scored "
                        + "(last %d have no future yet)%n",
                r.barsEmitted(), r.barsScored(), r.barsEmitted() - r.barsScored()));
        sb.append("Nothing was bought or sold: no costs, no tax, no position.\n");

        sb.append('\n').append(SUBSECTION_SEPARATOR).append('\n');
        sb.append(String.format("Baseline (hold through every bar): %+.3f%% per %d bars%n",
                r.baselineMeanForwardReturnPct(), r.horizonBars()));
        sb.append(String.format("Buy-and-hold over the whole range : %+.2f%%%n",
                r.buyAndHoldReturnPct()));
        sb.append(SUBSECTION_SEPARATOR).append('\n');

        sb.append(String.format("%-9s %7s %7s %9s %9s %8s %9s%n",
                "STANCE", "COUNT", "SHARE", "MEAN", "MEDIAN", "HIT", "vs BASE"));
        for (SignalStance stance : SignalStance.values()) {
            SignalQualityReport.StanceStats s = r.stats(stance);
            if (s == null) {
                continue;
            }
            sb.append(String.format("%-9s %7d %6.1f%% %+8.3f%% %+8.3f%% %7s %+8.3f%%%n",
                    stance, s.count(), s.sharePct(),
                    s.meanForwardReturnPct(), s.medianForwardReturnPct(),
                    s.hitRatePct() == null ? "-" : String.format("%.1f%%", s.hitRatePct()),
                    s.edgeVsBaselinePct()));
        }

        sb.append('\n');
        appendVerdict(sb, r);
        appendRecentCalls(sb, r);
        sb.append(SECTION_SEPARATOR).append('\n');

        return sb.toString();
    }

    /**
     * States the only conclusion this report supports on its own.
     *
     * <p>Deliberately refuses to call a model good. Beating the baseline in
     * both directions is the minimum bar, not evidence of an edge worth
     * trading: the margin can be a rounding error on a handful of calls, and
     * none of this has been charged a cost yet. The wording says so, because a
     * verdict line is exactly where an unearned claim does the most damage.
     */
    private void appendVerdict(StringBuilder sb, SignalQualityReport r) {
        SignalQualityReport.StanceStats bull = r.stats(SignalStance.BULLISH);
        SignalQualityReport.StanceStats bear = r.stats(SignalStance.BEARISH);

        if (r.barsScored() == 0) {
            sb.append("VERDICT: nothing scored -- the range is shorter than the horizon.\n");
            return;
        }
        if ((bull == null || bull.count() == 0) && (bear == null || bear.count() == 0)) {
            sb.append("VERDICT: no directional calls at all -- the strategy held every bar.\n");
            return;
        }
        if (r.directionallyCorrect()) {
            sb.append("VERDICT: directionally correct -- bullish calls beat the baseline "
                    + "and bearish calls trail it.\n");
            sb.append("         This is the minimum bar, not an edge. No cost has been "
                    + "charged, and a\n");
            sb.append("         thin margin over few calls is noise. Read the counts.\n");
        } else {
            sb.append("VERDICT: NO EDGE -- the calls do not land on the correct side of "
                    + "the baseline.\n");
            if (bull != null && bull.count() > 0 && bull.edgeVsBaselinePct() <= 0) {
                sb.append(String.format(
                        "         Bullish calls averaged %+.3f%% against a %+.3f%% baseline: "
                                + "listening to them%n         was worse than holding.%n",
                        bull.meanForwardReturnPct(), r.baselineMeanForwardReturnPct()));
            }
            if (bear != null && bear.count() > 0 && bear.edgeVsBaselinePct() >= 0) {
                sb.append(String.format(
                        "         Bearish calls averaged %+.3f%%, at or above the %+.3f%% "
                                + "baseline: they did not%n         mark weakness.%n",
                        bear.meanForwardReturnPct(), r.baselineMeanForwardReturnPct()));
            }
        }
    }

    /**
     * The most recent calls, which are the ones a listener would act on and the
     * ones with no forward return yet. Shown last so the report ends on what the
     * model is saying now rather than on how it did historically.
     */
    private void appendRecentCalls(StringBuilder sb, SignalQualityReport r) {
        List<SignalQualityReport.SignalPoint> all = r.signals();
        if (all.isEmpty()) {
            return;
        }
        int from = Math.max(0, all.size() - RECENT_CALLS_LIMIT);
        List<SignalQualityReport.SignalPoint> recent = all.subList(from, all.size());

        sb.append('\n').append(SUBSECTION_SEPARATOR).append('\n');
        sb.append(String.format("LATEST %d CALLS%n", recent.size()));
        sb.append(String.format("%-12s %-9s %12s %12s%n",
                "DATE", "STANCE", "CLOSE", "FWD " + r.horizonBars() + "B"));
        for (SignalQualityReport.SignalPoint p : recent) {
            sb.append(String.format("%-12s %-9s %12.2f %12s%n",
                    fmtDate(p.date()), p.stance(), p.closePrice(),
                    p.forwardReturnPct() == null
                            ? "pending"
                            : String.format("%+.2f%%", p.forwardReturnPct())));
        }
    }

    private String fmtDate(java.time.ZonedDateTime d) {
        return d == null ? "-" : DateTimeUtils.formatDate(d);
    }
}
