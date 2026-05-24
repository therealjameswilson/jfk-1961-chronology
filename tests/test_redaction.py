"""Tests for sensitive identifier redaction."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from redaction import scrub_security_numbers


def test_scrub_security_numbers_redacts_hyphenated_and_spaced_ssns() -> None:
    hyphenated = "123-45" + "-6789"
    spaced = "123 45" + " 6789"
    text = f"SSN {hyphenated} and Social Security #{spaced}."

    assert scrub_security_numbers(text) == (
        "SSN [REDACTED SSN] and Social Security #[REDACTED SSN]."
    )


def test_scrub_security_numbers_leaves_record_ids_and_dates() -> None:
    text = "RIF 104-10219-10143 and dates 10-13-44 8-28-66 remain."

    assert scrub_security_numbers(text) == text
