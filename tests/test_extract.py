"""Tests for corpus hit extraction helpers."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from extract import _context_window


def test_context_window_uses_character_window_around_match() -> None:
    text = f"{'a' * 400}17 April 1961{'b' * 400}"
    start = 400
    end = start + len("17 April 1961")

    context = _context_window(text, (start, end), 300)

    assert context == f"{'a' * 300}17 April 1961{'b' * 300}"
