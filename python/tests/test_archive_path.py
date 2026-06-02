"""Parity tests for the archive-path convention. Mirrors ArchivePathTest.java
so the Python loader produces identical paths to whatever the Java/Node
sides used to write."""

from __future__ import annotations

from loader.archive_path import DEFAULT_ARCHIVE_DIR, relative_path, sanitize_segment


def test_relative_path_follows_convention():
    assert relative_path("yahoo", "QQQ", 2008, "H1") == "yahoo/QQQ/2008/H1.csv"
    assert relative_path("default", "AAPL", 2024, "D1") == "default/AAPL/2024/D1.csv"
    assert relative_path("rbc", "EURUSD", 2020, "M30") == "rbc/EURUSD/2020/M30.csv"


def test_sanitize_strips_traversal_and_odd_chars():
    # Slashes get folded — no directory traversal possible from a single segment.
    assert sanitize_segment("../etc/passwd") == "___etc_passwd"
    # Whitespace, dots, colons → underscore.
    assert sanitize_segment("my source") == "my_source"
    assert sanitize_segment("AAPL.2024") == "AAPL_2024"
    # Allowed characters survive.
    assert sanitize_segment("alpha-vantage_v2") == "alpha-vantage_v2"
    # None falls back to a safe placeholder.
    assert sanitize_segment(None) == "_"


def test_sanitize_applied_to_each_segment_by_relative_path():
    p = relative_path("../yahoo", "AAPL/secret", 2024, "D1")
    assert p == "___yahoo/AAPL_secret/2024/D1.csv"
    assert not p.startswith("/")
    assert "/.." not in p
    assert "../" not in p


def test_default_archive_root():
    # The default path is the shipped contract, not env-only.
    assert str(DEFAULT_ARCHIVE_DIR).replace("\\", "/") == "data/csv-archive"
