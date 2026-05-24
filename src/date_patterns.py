"""Date pattern extraction for JFK corpus.

Extracts date references from markdown text and classifies them as:
- DAY:     resolves to a single YYYY-MM-DD
- RANGE:   expands to multiple consecutive YYYY-MM-DD values
- MONTH:   buckets to YYYY-MM
- QUARTER: buckets to a quarter/season group
- REVIEW:  ambiguous; needs manual review
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date, timedelta
from enum import Enum
from typing import Sequence


class HitType(Enum):
    DAY = "DAY"
    RANGE = "RANGE"
    MONTH = "MONTH"
    QUARTER = "QUARTER"
    REVIEW = "REVIEW"


@dataclass(frozen=True)
class DateHit:
    hit_type: HitType
    matched_text: str
    span: tuple[int, int]
    dates: tuple[str, ...] = ()        # YYYY-MM-DD for DAY/RANGE
    month: str | None = None            # YYYY-MM for MONTH
    quarter_label: str | None = None    # e.g. "Q2 1961", "spring 1961"


# ---------------------------------------------------------------------------
# Month name helpers (including common OCR corruptions)
# ---------------------------------------------------------------------------
_MONTH_MAP: dict[str, int] = {
    "january": 1, "jan": 1, "janaury": 1, "janurary": 1,
    "february": 2, "feb": 2, "febuary": 2, "feburary": 2,
    "march": 3, "mar": 3,
    "april": 4, "apr": 4, "apri1": 4,
    "may": 5,
    "june": 6, "jun": 6,
    "july": 7, "jul": 7,
    "august": 8, "aug": 8,
    "september": 9, "sep": 9, "sept": 9,
    "october": 10, "oct": 10, "octcber": 10, "ocotber": 10,
    "november": 11, "nov": 11,
    "december": 12, "dec": 12,
}
_MONTH_PAT = "|".join(sorted(_MONTH_MAP, key=len, reverse=True))


def _parse_month(s: str) -> int | None:
    return _MONTH_MAP.get(s.lower().rstrip("."))


# ---------------------------------------------------------------------------
# Year normalization (handles OCR l/1 and G/6 swaps)
# ---------------------------------------------------------------------------
_YEAR_PAT = r"[\dlOG]{2,4}"


def _normalize_year(raw: str) -> int | None:
    """Attempt to normalize a 2- or 4-char year string to a 4-digit int."""
    cleaned = raw.replace("l", "1").replace("O", "0").replace("G", "6")
    try:
        y = int(cleaned)
    except ValueError:
        return None
    if y < 100:
        y += 1900 if y >= 60 else 2000
    if 1960 <= y <= 1980:
        return y
    return None


def _normalize_day(raw: str) -> int | None:
    cleaned = raw.replace("l", "1").replace("O", "0")
    try:
        d = int(cleaned)
    except ValueError:
        return None
    return d if 1 <= d <= 31 else None


def _safe_date(y: int, m: int, d: int) -> str | None:
    try:
        return date(y, m, d).isoformat()
    except ValueError:
        return None


# ---------------------------------------------------------------------------
# Core regexes
# ---------------------------------------------------------------------------
# 1) Month DD, YYYY  /  Month DD YYYY  (American cable style)
_RE_MDY = re.compile(
    rf"\b({_MONTH_PAT})\.?\s+(\d{{1,2}}),?\s+({_YEAR_PAT})\b",
    re.IGNORECASE,
)

# 2) DD Month YYYY  (British / military style)
_RE_DMY = re.compile(
    rf"\b(\d{{1,2}})\s+({_MONTH_PAT})\.?\s+({_YEAR_PAT})\b",
    re.IGNORECASE,
)

# 3) ISO: YYYY-MM-DD
_RE_ISO = re.compile(
    r"\b(\d{4})-(\d{2})-(\d{2})\b"
)

# 4) Numeric US: M/D/YY or M/D/YYYY or M-D-YY
_RE_NUMERIC = re.compile(
    r"(?<!\$)\b(\d{1,2})[/\-](\d{1,2})[/\-](\d{2,4})\b"
)

# 5) Range: Month DD-DD, YYYY  (with en-dash or hyphen)
_RE_RANGE = re.compile(
    rf"\b({_MONTH_PAT})\.?\s+(\d{{1,2}})\s*[\-\u2013]\s*(\d{{1,2}}),?\s+({_YEAR_PAT})\b",
    re.IGNORECASE,
)

# 6) Month-level: "early/mid/late March 1961", "in March 1961", "March of 1961"
_RE_MONTH_LEVEL = re.compile(
    rf"\b(?:(?:early|mid|late|in|the\s+month\s+of)[-\s]+)?({_MONTH_PAT})\.?\s+(?:of\s+)?({_YEAR_PAT})\b",
    re.IGNORECASE,
)

# 7) Quarter/season: "spring 1961", "Q2 1961", "early 1961"
_SEASON_PAT = r"(?:spring|summer|fall|autumn|winter)"
_QUARTER_PAT = r"(?:Q[1-4]|(?:first|second|third|fourth)\s+quarter(?:\s+of)?)"
_BROAD_PAT = r"(?:early|late|mid(?:dle(?:\s+of)?)?)"
_RE_QUARTER = re.compile(
    rf"\b({_SEASON_PAT}|{_QUARTER_PAT}|{_BROAD_PAT})\s+(?:of\s+)?(\d{{4}}(?:-\d{{2}})?)\b",
    re.IGNORECASE,
)

# 8) False-positive guards
_RE_DOLLAR = re.compile(r"\$[\d,]+\.\d{2}")
_RE_DOCNUM = re.compile(r"\b\d{7,}\b")
_RE_ROOM = re.compile(r"\broom\s+\d{3,4}\b", re.IGNORECASE)
_RE_PAGE = re.compile(r"\bpage\s+\d{3,5}\b", re.IGNORECASE)


def _overlaps(span: tuple[int, int], taken: list[tuple[int, int]]) -> bool:
    for s, e in taken:
        if span[0] < e and span[1] > s:
            return True
    return False


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------
def extract_dates(text: str) -> list[DateHit]:
    """Extract all date references from *text*, returning a list of DateHit."""
    hits: list[DateHit] = []
    taken: list[tuple[int, int]] = []

    # --- pass 1: ranges (must come first to avoid sub-match conflicts) ---
    for m in _RE_RANGE.finditer(text):
        month = _parse_month(m.group(1))
        d_start = _normalize_day(m.group(2))
        d_end = _normalize_day(m.group(3))
        year = _normalize_year(m.group(4))
        if month and d_start and d_end and year and d_start <= d_end:
            dates = []
            for d in range(d_start, d_end + 1):
                iso = _safe_date(year, month, d)
                if iso:
                    dates.append(iso)
            if dates:
                span = (m.start(), m.end())
                hits.append(DateHit(HitType.RANGE, m.group(), span, tuple(dates)))
                taken.append(span)

    # --- pass 2: exact days (MDY, DMY, ISO, numeric) ---
    for pat, groups in [
        (_RE_MDY, "mdy"),
        (_RE_DMY, "dmy"),
        (_RE_ISO, "iso"),
        (_RE_NUMERIC, "num"),
    ]:
        for m in pat.finditer(text):
            span = (m.start(), m.end())
            if _overlaps(span, taken):
                continue
            # false-positive guard
            ctx = text[max(0, span[0] - 10): span[1] + 5]
            if _RE_DOLLAR.search(ctx) or _RE_DOCNUM.search(ctx):
                continue
            if _RE_ROOM.search(ctx) or _RE_PAGE.search(ctx):
                continue

            if groups == "mdy":
                mo = _parse_month(m.group(1))
                dy = _normalize_day(m.group(2))
                yr = _normalize_year(m.group(3))
            elif groups == "dmy":
                dy = _normalize_day(m.group(1))
                mo = _parse_month(m.group(2))
                yr = _normalize_year(m.group(3))
            elif groups == "iso":
                yr = _normalize_year(m.group(1))
                mo = int(m.group(2))
                dy = int(m.group(3))
            else:  # numeric
                mo = int(m.group(1))
                dy = int(m.group(2))
                yr = _normalize_year(m.group(3))

            if mo and dy and yr:
                iso = _safe_date(yr, mo, dy)
                if iso:
                    hits.append(DateHit(HitType.DAY, m.group(), span, (iso,)))
                    taken.append(span)

    # --- pass 3: month-level ---
    for m in _RE_MONTH_LEVEL.finditer(text):
        span = (m.start(), m.end())
        if _overlaps(span, taken):
            continue
        mo = _parse_month(m.group(1))
        yr = _normalize_year(m.group(2))
        if mo and yr:
            month_str = f"{yr}-{mo:02d}"
            hits.append(DateHit(HitType.MONTH, m.group(), span, month=month_str))
            taken.append(span)

    # --- pass 4: quarter / season ---
    for m in _RE_QUARTER.finditer(text):
        span = (m.start(), m.end())
        if _overlaps(span, taken):
            continue
        label = m.group().strip()
        hits.append(DateHit(HitType.QUARTER, m.group(), span, quarter_label=label))
        taken.append(span)

    # sort by position in text
    hits.sort(key=lambda h: h.span[0])
    return hits
