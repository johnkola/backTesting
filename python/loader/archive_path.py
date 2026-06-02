"""Convention for the on-disk CSV archive layout, shared with the Java/Node
sides during the migration. Mirrors src/main/java/.../data/ArchivePath.java
and web/server/imports.js's path helper:

  <source>/<symbol>/<year>/<TIMEFRAME>.csv

Segments are sanitised to alphanumerics + underscore + dash so a hostile
source or symbol name can't escape its bucket via path traversal."""

from __future__ import annotations

import os
import re
from pathlib import Path

DEFAULT_ARCHIVE_DIR = Path("data") / "csv-archive"

# Anything outside this set collapses to '_'. Slashes included on purpose:
# a single sanitised segment can never reintroduce a directory boundary.
_ALLOWED = re.compile(r"[^A-Za-z0-9_\-]")


def resolve_archive_root() -> Path:
    """Resolve the archive root from $CSV_ARCHIVE_DIR or fall back to the
    repo-relative default."""
    env = os.environ.get("CSV_ARCHIVE_DIR")
    if env and env.strip():
        return Path(env)
    return DEFAULT_ARCHIVE_DIR


def sanitize_segment(segment: str | None) -> str:
    if segment is None:
        return "_"
    return _ALLOWED.sub("_", segment)


def relative_path(source: str, symbol: str, year: int, timeframe: str) -> str:
    """Build the relative archive path. `timeframe` is the enum *name*
    (e.g. 'H1', 'D1') so callers pass strings directly without importing
    a Python enum we don't have yet."""
    return "/".join(
        [
            sanitize_segment(source),
            sanitize_segment(symbol),
            str(year),
            f"{timeframe}.csv",
        ]
    )
