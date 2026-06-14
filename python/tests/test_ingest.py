"""Unit tests for the drop-folder importer's pure-logic parts: the
filename-convention parser and the empty-inbox CLI path. The DB-touching
import path reuses `loader.csv_import` (covered by test_csv_import.py and
the integration test), so it isn't re-exercised here."""

from __future__ import annotations

import pytest

from loader.ingest import main, parse_filename


def test_parse_filename_minimal_symbol_and_timeframe():
    assert parse_filename("AAPL__D1.csv") == ("default", "AAPL", "STOCK", "D1")


def test_parse_filename_with_source():
    assert parse_filename("yahoo__AAPL__D1.csv") == ("yahoo", "AAPL", "STOCK", "D1")


def test_parse_filename_full():
    assert parse_filename("yahoo__EURUSD__FOREX__H1.csv") == (
        "yahoo",
        "EURUSD",
        "FOREX",
        "H1",
    )


def test_parse_filename_type_without_source():
    # Type is recognised by membership wherever it sits, so source defaults.
    assert parse_filename("AAPL__STOCK__D1.csv") == ("default", "AAPL", "STOCK", "D1")


def test_parse_filename_normalises_case():
    # Timeframe and type are upper-cased; symbol/source are preserved as-is.
    assert parse_filename("vendor__spy__d1.csv") == ("vendor", "spy", "STOCK", "D1")


def test_parse_filename_rejects_missing_timeframe():
    with pytest.raises(ValueError, match="at least SYMBOL__TIMEFRAME"):
        parse_filename("AAPL.csv")


def test_parse_filename_rejects_unknown_timeframe():
    with pytest.raises(ValueError, match="not a timeframe"):
        parse_filename("foo__BADTF.csv")


def test_parse_filename_rejects_too_many_segments():
    with pytest.raises(ValueError, match="leftover segment"):
        parse_filename("a__b__c__d__D1.csv")


def test_main_empty_inbox_creates_dir_and_exits_clean(tmp_path, capsys):
    inbox = tmp_path / "drop"
    assert main(["--inbox", str(inbox)]) == 0
    assert inbox.is_dir()
    assert "Nothing to do" in capsys.readouterr().out
