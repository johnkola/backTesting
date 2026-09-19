#!/usr/bin/env python3
"""Tests for the intraday test-data generator.

Standard library only, like the tool itself -- no pytest, no install:

    python test_generate_intraday.py          # or: python -m unittest discover

The contract under test is the round-trip: whatever path the generator
invents inside a day, aggregating that day's bars back up has to reproduce
the daily bar exactly. If that breaks, generated intraday data stops being a
stand-in for the daily data it came from.
"""

from __future__ import annotations

import io
import math
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from datetime import datetime
from pathlib import Path

from generate_intraday import (
    TF_MINUTES,
    default_output,
    distribute,
    main,
    parse_hhmm,
    read_daily,
    read_written,
    resolve_session,
    rng_for,
    session_slots,
    synthesize,
    verify,
)

NYSE = resolve_session("nyse", None, None)  # 09:30 .. 16:00
DAY = datetime(2024, 3, 4).date()

# A week of daily bars with varied shapes: an up day, a down day, a doji with
# zero volume, a violently wide range, and a quiet day.
DAILY = [
    (datetime(2024, 3, 4), 100.0, 104.0, 99.0, 103.0, 1_000_000.0),
    (datetime(2024, 3, 5), 103.0, 103.5, 97.25, 98.0, 2_500_001.0),
    (datetime(2024, 3, 6), 98.0, 98.0, 98.0, 98.0, 0.0),
    (datetime(2024, 3, 7), 98.5, 130.0, 60.0, 61.0, 999.0),
    (datetime(2024, 3, 8), 61.0, 61.5, 60.5, 61.25, 7.0),
]


def generate(timeframe: str = "M30", **kwargs) -> list:
    return synthesize(
        DAILY, timeframe=timeframe, session_start=NYSE[0], session_end=NYSE[1], **kwargs
    )


def bars_on(day_index: int, timeframe: str = "M30", **kwargs) -> list:
    target = DAILY[day_index][0].date()
    return [b for b in generate(timeframe, **kwargs) if b[0].date() == target]


def write_daily_csv(path: Path) -> None:
    lines = ["Date,Open,High,Low,Close,Volume"]
    for ts, o, h, low, c, v in DAILY:
        lines.append(f"{ts:%Y-%m-%d},{o},{h},{low},{c},{int(v)}")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


class Sessions(unittest.TestCase):
    def test_parse_hhmm_accepts_end_of_day_sentinel(self):
        self.assertEqual(parse_hhmm("09:30"), 570)
        self.assertEqual(parse_hhmm("24:00"), 1440)

    def test_parse_hhmm_rejects_junk(self):
        for bad in ("9-30", "09:60", "25:00", "", "09:30:00"):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                parse_hhmm(bad)

    def test_resolve_session_overrides_the_preset(self):
        self.assertEqual(resolve_session("24h", None, None), (0, 1440))
        self.assertEqual(resolve_session("nyse", "10:00", None), (600, 960))

    def test_resolve_session_rejects_an_empty_window(self):
        with self.assertRaises(ValueError):
            resolve_session("nyse", "16:00", "09:30")
        with self.assertRaises(ValueError):
            resolve_session("tokyo", None, None)

    def test_m30_fills_the_nyse_session_exactly(self):
        slots = session_slots(DAY, "M30", *NYSE)
        self.assertEqual(len(slots), 13)  # 6.5h / 30m
        self.assertEqual(slots[0].strftime("%H:%M"), "09:30")
        self.assertEqual(slots[-1].strftime("%H:%M"), "15:30")

    def test_h1_gets_a_short_final_bar_rather_than_running_past_the_close(self):
        # 6.5 hours is not a whole number of H1 bars. The last one covers
        # 15:30-16:00 rather than spilling into 16:30, which would land
        # outside the session and break the uniform spacing the app's gap
        # check expects.
        slots = session_slots(DAY, "H1", *NYSE)
        self.assertEqual(len(slots), 7)
        self.assertEqual(slots[-1].strftime("%H:%M"), "15:30")
        deltas = {(b - a).total_seconds() for a, b in zip(slots, slots[1:])}
        self.assertEqual(deltas, {3600.0})

    def test_24h_session_stays_inside_the_day(self):
        slots = session_slots(DAY, "H1", *resolve_session("24h", None, None))
        self.assertEqual(len(slots), 24)
        self.assertTrue(all(s.date() == DAY for s in slots))


class RoundTrip(unittest.TestCase):
    def test_every_timeframe_rolls_back_up_to_the_source_day(self):
        for timeframe in sorted(TF_MINUTES):
            with self.subTest(timeframe=timeframe):
                self.assertEqual(verify(DAILY, generate(timeframe)), [])

    def test_24h_session_also_rolls_back_up(self):
        start, end = resolve_session("24h", None, None)
        bars = synthesize(DAILY, timeframe="M30", session_start=start, session_end=end)
        self.assertEqual(verify(DAILY, bars), [])

    def test_round_trip_holds_across_seeds(self):
        # The extremes are forced rather than hoped for, so no seed should be
        # able to produce a day whose high or low misses.
        for seed in range(25):
            with self.subTest(seed=seed):
                self.assertEqual(verify(DAILY, generate(seed=seed)), [])

    def test_anchors_land_on_the_right_bars(self):
        day_one = bars_on(0)
        self.assertEqual(len(day_one), 13)
        self.assertEqual(day_one[0][1], DAILY[0][1])  # first open == daily open
        self.assertEqual(day_one[-1][4], DAILY[0][4])  # last close == daily close
        self.assertEqual(max(b[2] for b in day_one), DAILY[0][2])
        self.assertEqual(min(b[3] for b in day_one), DAILY[0][3])
        self.assertEqual(math.fsum(b[5] for b in day_one), DAILY[0][5])

    def test_bars_chain_open_to_close(self):
        day_one = bars_on(0)
        for prev, cur in zip(day_one, day_one[1:]):
            self.assertEqual(cur[1], prev[4])

    def test_every_generated_bar_is_well_formed(self):
        for ts, o, h, low, c, v in generate("M15"):
            self.assertGreaterEqual(h, max(o, c), ts)
            self.assertLessEqual(low, min(o, c), ts)
            self.assertGreaterEqual(h, low, ts)
            self.assertGreaterEqual(v, 0.0, ts)

    def test_flat_day_generates_flat_bars(self):
        doji = bars_on(2)
        self.assertTrue(doji, "the flat day should still produce bars")
        self.assertTrue(all(o == h == low == c == 98.0 for _, o, h, low, c, _ in doji))

    def test_zero_volume_day_stays_at_zero(self):
        self.assertTrue(all(b[5] == 0.0 for b in bars_on(2)))

    def test_single_slot_session_reproduces_the_daily_bar(self):
        # A session shorter than one bar collapses to a copy of the input.
        bars = synthesize(DAILY, timeframe="H4", session_start=570, session_end=600)
        self.assertEqual(len(bars), len(DAILY))
        self.assertEqual(bars[0][1:], DAILY[0][1:])

    def test_verify_catches_a_broken_bar(self):
        bars = generate("M30")
        poisoned = [(bars[0][0], bars[0][1], 999.0, *bars[0][3:])] + bars[1:13]
        self.assertTrue(any("high" in line for line in verify(DAILY[:1], poisoned)))

    def test_verify_reports_a_missing_day(self):
        self.assertNotEqual(verify(DAILY, []), [])


class Determinism(unittest.TestCase):
    def test_output_is_reproducible(self):
        self.assertEqual(generate("M30"), generate("M30"))

    def test_a_days_shape_does_not_depend_on_the_range_around_it(self):
        # Seeding per-day rather than per-index means trimming the input does
        # not reshuffle the days that remain.
        full = {b[0]: b for b in generate("M30")}
        trimmed = synthesize(
            DAILY[2:], timeframe="M30", session_start=NYSE[0], session_end=NYSE[1]
        )
        self.assertTrue(all(full[b[0]] == b for b in trimmed))

    def test_different_seeds_give_different_paths(self):
        self.assertNotEqual(generate("M30", seed=1), generate("M30", seed=2))

    def test_rng_is_stable_across_processes(self):
        # Seeded from a digest, not hash() -- which is salted per process and
        # would make runs irreproducible.
        first = rng_for(42, "M30", DAY).random()
        self.assertEqual(first, rng_for(42, "M30", DAY).random())
        self.assertNotEqual(first, rng_for(43, "M30", DAY).random())


class VolumeSplit(unittest.TestCase):
    def test_exact_for_integral_totals(self):
        parts = distribute(1_000_001.0, [3.0, 1.0, 1.0, 5.0])
        self.assertEqual(sum(parts), 1_000_001.0)
        self.assertTrue(all(float(p).is_integer() for p in parts))

    def test_exact_for_fractional_totals(self):
        parts = distribute(10.5, [1.0, 2.0, 3.0])
        self.assertAlmostEqual(math.fsum(parts), 10.5, places=9)

    def test_handles_zero_and_empty(self):
        self.assertEqual(distribute(0.0, [1.0, 2.0]), [0.0, 0.0])
        self.assertEqual(distribute(100.0, []), [])

    def test_volume_is_heaviest_at_the_open_and_close(self):
        volumes = [b[5] for b in bars_on(0)]
        midday = volumes[len(volumes) // 2]
        self.assertGreater(volumes[0], midday)
        self.assertGreater(volumes[-1], midday)

    def test_flat_profile_spreads_evenly(self):
        volumes = [b[5] for b in bars_on(0, volume_profile=0.0)]
        mean = math.fsum(volumes) / len(volumes)
        # Only the +/-15% jitter remains, so no bar sits far from the mean.
        self.assertTrue(all(abs(v - mean) < mean * 0.35 for v in volumes))


class CommandLine(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)
        self.src = self.tmp / "X_daily.csv"
        write_daily_csv(self.src)
        self.addCleanup(self._tmp.cleanup)

    def run_main(self, argv):
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            code = main(argv)
        return code, out.getvalue(), err.getvalue()

    def test_default_output_name_drops_the_daily_suffix(self):
        for name, tf, want in (
            ("AAPL_daily.csv", "M30", "AAPL_M30.csv"),
            ("AAPL_D1.csv", "H1", "AAPL_H1.csv"),
            ("AAPL.csv", "M30", "AAPL_M30.csv"),
        ):
            with self.subTest(name=name):
                self.assertEqual(default_output(Path(name), tf).name, want)

    def test_default_output_goes_to_the_tools_own_folder(self):
        # Never beside the input: generated candles stay out of the app's
        # data directories unless someone passes -o explicitly.
        target = default_output(Path("/somewhere/else/AAPL_daily.csv"), "M30")
        self.assertEqual(target.parent.name, "output")
        self.assertEqual(target.parent.parent.name, "datagen")

    def test_read_daily_skips_the_header_and_honours_bounds(self):
        self.assertEqual(len(read_daily(self.src)), len(DAILY))
        self.assertEqual(len(read_daily(self.src, since="2024-03-06", until="2024-03-07")), 2)

    def test_writes_a_file_whose_round_trip_survives_rounding(self):
        dest = self.tmp / "X_M30.csv"
        code, out, _ = self.run_main([str(self.src), "-t", "M30", "-o", str(dest)])
        self.assertEqual(code, 0)
        self.assertIn("Round-trip to D1: OK", out)

        written = read_written(dest)
        self.assertEqual(len(written), len(DAILY) * 13)
        # The check that matters: verification against the parsed file, not
        # the in-memory bars, so rounding on the way out is covered.
        self.assertEqual(verify(DAILY, written), [])

        lines = dest.read_text(encoding="utf-8").splitlines()
        self.assertEqual(lines[0], "Date,Open,High,Low,Close,Volume")
        self.assertTrue(lines[1].startswith("2024-03-04 09:30:00,"))

    def test_respects_session_and_date_bounds(self):
        dest = self.tmp / "custom.csv"
        code, _, _ = self.run_main(
            [str(self.src), "-t", "H1", "--session", "24h", "--since", "2024-03-07",
             "-o", str(dest)]
        )
        self.assertEqual(code, 0)
        self.assertEqual(len(read_written(dest)), 2 * 24)  # two days, hourly, round the clock

    def test_reports_an_empty_input(self):
        empty = self.tmp / "empty_daily.csv"
        empty.write_text("Date,Open,High,Low,Close,Volume\n", encoding="utf-8")
        code, _, err = self.run_main([str(empty), "-o", str(self.tmp / "out.csv")])
        self.assertEqual(code, 1)
        self.assertIn("No daily rows", err)

    def test_rejects_a_missing_input_file(self):
        with self.assertRaises(SystemExit):
            self.run_main([str(self.tmp / "nope.csv")])


if __name__ == "__main__":
    unittest.main(verbosity=2)
