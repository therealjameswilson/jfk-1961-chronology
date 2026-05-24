"""Tests for src/date_patterns.py against tests/fixtures/date_samples.txt."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

# Ensure src/ is importable
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from date_patterns import HitType, extract_dates

FIXTURE = Path(__file__).resolve().parent / "fixtures" / "date_samples.txt"


def _load_fixture() -> list[tuple[str, str]]:
    """Return list of (expected_type, text) from date_samples.txt."""
    entries: list[tuple[str, str]] = []
    for line in FIXTURE.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        parts = line.split("\t", maxsplit=1)
        if len(parts) == 2:
            entries.append((parts[0].strip(), parts[1].strip()))
    return entries


# ---------------------------------------------------------------------------
# Parametrized: every DAY line should produce at least one DAY hit
# ---------------------------------------------------------------------------
_DAY_CASES = [(t, text) for t, text in _load_fixture() if t == "DAY"]

@pytest.mark.parametrize("expected_type,text", _DAY_CASES, ids=[c[1] for c in _DAY_CASES])
def test_day_cases(expected_type: str, text: str) -> None:
    hits = extract_dates(text)
    day_hits = [h for h in hits if h.hit_type == HitType.DAY]
    assert day_hits, f"Expected DAY hit for: {text!r}, got {hits}"


# ---------------------------------------------------------------------------
# Parametrized: every RANGE line should produce at least one RANGE hit
# ---------------------------------------------------------------------------
_RANGE_CASES = [(t, text) for t, text in _load_fixture() if t == "RANGE"]

@pytest.mark.parametrize("expected_type,text", _RANGE_CASES, ids=[c[1] for c in _RANGE_CASES])
def test_range_cases(expected_type: str, text: str) -> None:
    hits = extract_dates(text)
    range_hits = [h for h in hits if h.hit_type == HitType.RANGE]
    assert range_hits, f"Expected RANGE hit for: {text!r}, got {hits}"


# ---------------------------------------------------------------------------
# Parametrized: every MONTH line should produce at least one MONTH hit
# ---------------------------------------------------------------------------
_MONTH_CASES = [(t, text) for t, text in _load_fixture() if t == "MONTH"]

@pytest.mark.parametrize("expected_type,text", _MONTH_CASES, ids=[c[1] for c in _MONTH_CASES])
def test_month_cases(expected_type: str, text: str) -> None:
    hits = extract_dates(text)
    month_hits = [h for h in hits if h.hit_type == HitType.MONTH]
    assert month_hits, f"Expected MONTH hit for: {text!r}, got {hits}"


# ---------------------------------------------------------------------------
# Parametrized: every QUARTER line should produce at least one QUARTER hit
# ---------------------------------------------------------------------------
_QUARTER_CASES = [(t, text) for t, text in _load_fixture() if t == "QUARTER"]

@pytest.mark.parametrize("expected_type,text", _QUARTER_CASES, ids=[c[1] for c in _QUARTER_CASES])
def test_quarter_cases(expected_type: str, text: str) -> None:
    hits = extract_dates(text)
    quarter_hits = [h for h in hits if h.hit_type == HitType.QUARTER]
    assert quarter_hits, f"Expected QUARTER hit for: {text!r}, got {hits}"


# ---------------------------------------------------------------------------
# Parametrized: every NONE line should produce zero hits
# ---------------------------------------------------------------------------
_NONE_CASES = [(t, text) for t, text in _load_fixture() if t == "NONE"]

@pytest.mark.parametrize("expected_type,text", _NONE_CASES, ids=[c[1] for c in _NONE_CASES])
def test_none_cases(expected_type: str, text: str) -> None:
    hits = extract_dates(text)
    assert not hits, f"Expected no hits for: {text!r}, got {hits}"


# ---------------------------------------------------------------------------
# Specific value checks
# ---------------------------------------------------------------------------
def test_inauguration_resolves_correctly() -> None:
    hits = extract_dates("January 20, 1961")
    assert hits[0].dates == ("1961-01-20",)


def test_bay_of_pigs_range_expands() -> None:
    hits = extract_dates("April 17-19, 1961")
    assert hits[0].hit_type == HitType.RANGE
    assert hits[0].dates == ("1961-04-17", "1961-04-18", "1961-04-19")


def test_missile_crisis_range() -> None:
    hits = extract_dates("October 16-28, 1962")
    assert hits[0].hit_type == HitType.RANGE
    assert len(hits[0].dates) == 13


def test_ocr_l_for_1() -> None:
    hits = extract_dates("January 20, l961")
    assert hits and hits[0].dates == ("1961-01-20",)


def test_ocr_corrupted_month() -> None:
    hits = extract_dates("Janaury 20, 1961")
    assert hits and hits[0].dates == ("1961-01-20",)


def test_dollar_false_positive() -> None:
    hits = extract_dates("$1,961.00 was authorized")
    assert not hits


def test_room_false_positive() -> None:
    hits = extract_dates("Room 1961 of the State Department")
    assert not hits


def test_assassination_date() -> None:
    hits = extract_dates("November 22, 1963")
    assert hits[0].dates == ("1963-11-22",)


def test_month_level_early() -> None:
    hits = extract_dates("early March 1961")
    month_hits = [h for h in hits if h.hit_type == HitType.MONTH]
    assert month_hits and month_hits[0].month == "1961-03"


def test_quarter_spring() -> None:
    hits = extract_dates("spring 1961")
    quarter_hits = [h for h in hits if h.hit_type == HitType.QUARTER]
    assert quarter_hits
