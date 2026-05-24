"""Document metadata extraction for the local JFK markdown corpus."""
from __future__ import annotations

import argparse
import json
import re
from dataclasses import asdict, dataclass
from datetime import date
from pathlib import Path
from typing import Any, Iterable


DEFAULT_CORPUS_ROOT = Path("../jfk")
DEFAULT_OUTPUT_PATH = Path("data/metadata.parquet")
DEFAULT_REVIEW_QUEUE_PATH = Path("data/review_queue.jsonl")
PROBE_CHARS = 120_000
RIF_PREFIX_AGENCIES: dict[str, str] = {
    "104": "CIA",
    "124": "FBI",
    "157": "SSCIA",
    "180": "HSCA",
    "194": "FBI",
    "198": "JFK Task Force",
    "202": "Secret Service",
}


@dataclass(frozen=True)
class DocumentMetadata:
    """Normalized metadata for one source markdown file."""

    filename: str
    source_path: str
    rif_number: str | None
    doc_date: str
    doc_date_raw: str | None
    originating_agency: str
    agency: str | None
    document_type: str | None
    classification: str | None
    metadata_source: str
    needs_review: bool


_RECORD_NUMBER_RE = re.compile(
    r"(?im)^\s*(?:#+\s*)?RECORD\s+NUMBER\s*:\s*(?P<value>.+?)\s*$"
)
_FILENAME_RIF_RE = re.compile(r"\b\d{3}-\d{5}-\d{5}\b")
_SECTION_BREAK_RE = re.compile(
    r"(?im)^\s*(?:AGENCY\s+INFORMATION|DOCUMENT\s+INFORMATION|"
    r"OPENING\s+CRITERIA\s*:|COMMENTS\s*:|[-]{10,})\s*$"
)
_DOCUMENT_INFO_RE = re.compile(r"(?im)^\s*(?:#+\s*)?DOCUMENT\s+INFORMATION\s*$")
_AGENCY_INFO_RE = re.compile(r"(?im)^\s*(?:#+\s*)?AGENCY\s+INFORMATION\s*$")
_LABEL_RE = re.compile(
    r"^\s*(?:#+\s*)?(?P<label>[A-Z][A-Z /.'()-]{1,50}?)\s*:\s*(?P<value>.*)\s*$",
    re.IGNORECASE,
)
_GENERIC_DATE_LINE_RE = re.compile(
    r"^\s*(?:#+\s*)?DATE\s*:?\s+(?P<value>.+?)\s*$",
    re.IGNORECASE,
)
_BAD_DATE_LABEL_RE = re.compile(
    r"\b(?:birth|last\s+review|next\s+review|review|time\s+group|received|"
    r"forwarded|typed|signed|prepared|stamp|request|classification)\b",
    re.IGNORECASE,
)
_MONTHS: dict[str, int] = {
    "jan": 1,
    "january": 1,
    "feb": 2,
    "february": 2,
    "mar": 3,
    "march": 3,
    "apr": 4,
    "april": 4,
    "may": 5,
    "jun": 6,
    "june": 6,
    "jul": 7,
    "july": 7,
    "aug": 8,
    "august": 8,
    "sep": 9,
    "sept": 9,
    "september": 9,
    "oct": 10,
    "october": 10,
    "nov": 11,
    "november": 11,
    "dec": 12,
    "december": 12,
}
_MONTH_NAME_RE = "|".join(sorted(_MONTHS, key=len, reverse=True))
_MDY_RE = re.compile(
    rf"\b(?P<month>{_MONTH_NAME_RE})\.?\s+(?P<day>\d{{1,2}}),?\s+"
    r"(?P<year>\d{2,4})\b",
    re.IGNORECASE,
)
_DMY_RE = re.compile(
    rf"\b(?P<day>\d{{1,2}})\s+(?P<month>{_MONTH_NAME_RE})\.?\s+"
    r"(?P<year>\d{2,4})\b",
    re.IGNORECASE,
)
_NUMERIC_RE = re.compile(
    r"\b(?P<a>\d{1,2})\s*[/.-]\s*(?P<b>\d{1,2})\s*[/.-]\s*"
    r"(?P<year>\d{2,4})\b"
)
_COMPACT_ISO_RE = re.compile(
    r"\b(?P<year>\d{4})-(?P<month>\d{1,2})-(?P<day>\d{1,2})\b"
)


def discover_markdown_files(corpus_root: Path) -> list[Path]:
    """Return source markdown files, preferring the doctly corpus directory."""

    source_root = corpus_root / "jfk_files_md"
    if source_root.exists():
        files = source_root.rglob("*.md")
    else:
        files = corpus_root.rglob("*.md")
    return sorted(path for path in files if path.is_file())


def extract_document_metadata(path: Path, corpus_root: Path) -> DocumentMetadata:
    """Extract document-level metadata from a single markdown file."""

    text = path.read_text(encoding="utf-8", errors="replace")[:PROBE_CHARS]
    source_path = path.relative_to(corpus_root).as_posix()
    rif_number = _extract_record_number(text) or _extract_rif_from_filename(path)

    document_block = _extract_section(text, _DOCUMENT_INFO_RE)
    agency_block = _extract_section(text, _AGENCY_INFO_RE)

    agency = _clean_value(_extract_key(agency_block, "AGENCY")) if agency_block else None
    originator = (
        _clean_value(_extract_key(document_block, "ORIGINATOR"))
        if document_block
        else None
    )
    doc_date_raw = (
        _clean_value(_extract_key(document_block, "DATE")) if document_block else None
    )
    document_type = (
        _clean_value(_extract_key(document_block, "DOCUMENT TYPE"))
        if document_block
        else None
    )
    classification = (
        _clean_value(_extract_key(document_block, "CLASSIFICATION"))
        if document_block
        else None
    )
    metadata_source = "rif_header" if document_block else "fallback"

    if not doc_date_raw:
        doc_date_raw = _find_generic_document_date(text)

    doc_date = _parse_document_date(doc_date_raw) if doc_date_raw else None
    prefix_agency = _agency_from_rif(rif_number)
    agency = prefix_agency or agency
    originating_agency = (
        prefix_agency
        or _known_agency(originator)
        or _known_agency(agency)
        or "unknown"
    )
    needs_review = doc_date is None

    return DocumentMetadata(
        filename=path.name,
        source_path=source_path,
        rif_number=rif_number,
        doc_date=doc_date or "unknown",
        doc_date_raw=doc_date_raw,
        originating_agency=originating_agency,
        agency=agency,
        document_type=document_type,
        classification=classification,
        metadata_source=metadata_source,
        needs_review=needs_review,
    )


def build_metadata(corpus_root: Path) -> list[DocumentMetadata]:
    """Walk the corpus and extract metadata for every markdown document."""

    return [
        extract_document_metadata(path, corpus_root)
        for path in discover_markdown_files(corpus_root)
    ]


def write_metadata_parquet(rows: Iterable[DocumentMetadata], output_path: Path) -> None:
    """Write metadata rows to parquet using pyarrow."""

    pyarrow, parquet = _load_pyarrow()
    output_path.parent.mkdir(parents=True, exist_ok=True)
    table = pyarrow.Table.from_pylist([asdict(row) for row in rows], schema=_schema(pyarrow))
    parquet.write_table(table, output_path)


def write_review_queue(rows: Iterable[DocumentMetadata], output_path: Path) -> None:
    """Write documents with unknown dates to JSONL for manual triage."""

    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", encoding="utf-8") as handle:
        for row in rows:
            if row.needs_review:
                handle.write(json.dumps(asdict(row), sort_keys=True) + "\n")


def _extract_record_number(text: str) -> str | None:
    match = _RECORD_NUMBER_RE.search(text)
    if not match:
        return None
    return _clean_value(match.group("value"))


def _extract_rif_from_filename(path: Path) -> str | None:
    match = _FILENAME_RIF_RE.search(path.name)
    return match.group(0) if match else None


def _agency_from_rif(rif_number: str | None) -> str | None:
    if not rif_number:
        return None
    prefix = rif_number.split("-", maxsplit=1)[0]
    return RIF_PREFIX_AGENCIES.get(prefix)


def _known_agency(raw: str | None) -> str | None:
    value = _clean_value(raw)
    if not value or value.lower() == "unknown":
        return None
    return value


def _extract_section(text: str, heading_re: re.Pattern[str]) -> str | None:
    heading = heading_re.search(text)
    if not heading:
        return None
    start = heading.end()
    next_heading = _SECTION_BREAK_RE.search(text, start + 1)
    end = next_heading.start() if next_heading else min(len(text), start + 6_000)
    return text[start:end]


def _extract_key(block: str | None, label: str) -> str | None:
    if not block:
        return None
    wanted = _normalize_label(label)
    for line in block.splitlines():
        match = _LABEL_RE.match(line)
        if not match:
            continue
        if _normalize_label(match.group("label")) == wanted:
            value = match.group("value").strip()
            return value or None
    return None


def _find_generic_document_date(text: str) -> str | None:
    for line in text.splitlines()[:160]:
        if _BAD_DATE_LABEL_RE.search(line):
            continue
        match = _GENERIC_DATE_LINE_RE.match(line)
        if not match:
            continue
        value = _clean_value(match.group("value"))
        if value and _parse_document_date(value):
            return value
    return None


def _parse_document_date(raw: str | None) -> str | None:
    if not raw:
        return None
    cleaned = _clean_date_text(raw)
    parsers = (
        _parse_compact_iso,
        _parse_numeric_date,
        _parse_month_day_year,
        _parse_day_month_year,
    )
    for parser in parsers:
        parsed = parser(cleaned)
        if parsed:
            return parsed.isoformat()
    return None


def _parse_compact_iso(raw: str) -> date | None:
    match = _COMPACT_ISO_RE.search(raw)
    if not match:
        return None
    return _safe_date(
        _normalize_year(match.group("year")),
        int(match.group("month")),
        int(match.group("day")),
    )


def _parse_numeric_date(raw: str) -> date | None:
    match = _NUMERIC_RE.search(raw)
    if not match:
        return None
    a = int(match.group("a"))
    b = int(match.group("b"))
    year = _normalize_year(match.group("year"))
    if year is None or a == 0 or b == 0:
        return None

    if a > 12:
        day, month = a, b
    elif b > 12:
        month, day = a, b
    else:
        month, day = a, b
    return _safe_date(year, month, day)


def _parse_month_day_year(raw: str) -> date | None:
    match = _MDY_RE.search(raw)
    if not match:
        return None
    month = _parse_month(match.group("month"))
    day = int(match.group("day"))
    year = _normalize_year(match.group("year"))
    return _safe_date(year, month, day)


def _parse_day_month_year(raw: str) -> date | None:
    match = _DMY_RE.search(raw)
    if not match:
        return None
    month = _parse_month(match.group("month"))
    day = int(match.group("day"))
    year = _normalize_year(match.group("year"))
    return _safe_date(year, month, day)


def _normalize_year(raw: str) -> int | None:
    year = int(raw)
    if year < 100:
        year += 1900 if year >= 30 else 2000
    return year


def _parse_month(raw: str) -> int | None:
    return _MONTHS.get(raw.lower().rstrip("."))


def _safe_date(year: int | None, month: int | None, day: int | None) -> date | None:
    if year is None or month is None or day is None:
        return None
    try:
        return date(year, month, day)
    except ValueError:
        return None


def _clean_value(raw: str | None) -> str | None:
    if raw is None:
        return None
    value = re.sub(r"\s+", " ", raw).strip(" #|*-:\t")
    return value or None


def _clean_date_text(raw: str) -> str:
    cleaned = raw.replace("O", "0")
    cleaned = re.sub(r"\s+", " ", cleaned)
    return cleaned.strip(" .,:;")


def _normalize_label(raw: str) -> str:
    return re.sub(r"\s+", " ", raw.strip().upper())


def _schema(pyarrow: Any) -> Any:
    return pyarrow.schema(
        [
            ("filename", pyarrow.string()),
            ("source_path", pyarrow.string()),
            ("rif_number", pyarrow.string()),
            ("doc_date", pyarrow.string()),
            ("doc_date_raw", pyarrow.string()),
            ("originating_agency", pyarrow.string()),
            ("agency", pyarrow.string()),
            ("document_type", pyarrow.string()),
            ("classification", pyarrow.string()),
            ("metadata_source", pyarrow.string()),
            ("needs_review", pyarrow.bool_()),
        ]
    )


def _load_pyarrow() -> tuple[Any, Any]:
    try:
        import pyarrow  # type: ignore[import-not-found]
        import pyarrow.parquet as parquet  # type: ignore[import-not-found]
    except ImportError as exc:
        msg = (
            "pyarrow is required to write parquet files. Install the project "
            "dependencies, then rerun this command."
        )
        raise SystemExit(msg) from exc
    return pyarrow, parquet


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--corpus", type=Path, default=DEFAULT_CORPUS_ROOT)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT_PATH)
    parser.add_argument(
        "--review-queue",
        type=Path,
        default=DEFAULT_REVIEW_QUEUE_PATH,
    )
    return parser.parse_args()


def main() -> None:
    args = _parse_args()
    corpus_root = args.corpus.resolve()
    rows = build_metadata(corpus_root)
    write_metadata_parquet(rows, args.output)
    write_review_queue(rows, args.review_queue)
    reviewed = sum(row.needs_review for row in rows)
    print(
        f"wrote {len(rows)} metadata rows to {args.output} "
        f"({reviewed} queued for date review)"
    )


if __name__ == "__main__":
    main()
