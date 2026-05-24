"""Redaction helpers for sensitive identifiers in generated excerpts."""
from __future__ import annotations

import re


SSN_RE = re.compile(r"(?<!\d)\d{3}[-\s]\d{2}[-\s]\d{4}-?(?!\d)")
REDACTION_TEXT = "[REDACTED SSN]"


def scrub_security_numbers(text: str) -> str:
    """Scrub Social Security-style identifiers while preserving surrounding text."""

    return SSN_RE.sub(REDACTION_TEXT, text)
