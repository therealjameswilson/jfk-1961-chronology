"""Render daily and monthly chronology markdown from extracted hit parquet."""
from __future__ import annotations

import argparse
import calendar
import re
from collections import defaultdict
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path
from typing import Any, Iterable

try:
    from .redaction import scrub_security_numbers
except ImportError:
    from redaction import scrub_security_numbers


DEFAULT_HITS_PATH = Path("data/hits.parquet")
DEFAULT_EVENTS_PATH = Path("key_events.yaml")
DEFAULT_OUTPUT_ROOT = Path("output")
DEFAULT_CORPUS_ROOT = Path("../jfk")
DEFAULT_START_DATE = date(1961, 1, 20)
DEFAULT_END_DATE = date(1961, 12, 31)
DEFAULT_CONTEXT_CHARS = 300
NARA_RELEASE_URL = "https://www.archives.gov/research/jfk/release-2025"
MONTH_NAMES = (
    "",
    "January",
    "February",
    "March",
    "April",
    "May",
    "June",
    "July",
    "August",
    "September",
    "October",
    "November",
    "December",
)


@dataclass(frozen=True)
class EventInfo:
    label: str = ""
    note: str = ""


@dataclass(frozen=True)
class RenderHit:
    source_path: str
    filename: str
    rif_number: str | None
    doc_date: str
    originating_agency: str
    matched_text: str
    span_start: int
    span_end: int
    context: str


def render_chronology(
    *,
    hits_path: Path = DEFAULT_HITS_PATH,
    events_path: Path = DEFAULT_EVENTS_PATH,
    output_root: Path = DEFAULT_OUTPUT_ROOT,
    corpus_root: Path = DEFAULT_CORPUS_ROOT,
    start_date: date = DEFAULT_START_DATE,
    end_date: date = DEFAULT_END_DATE,
    context_chars: int = DEFAULT_CONTEXT_CHARS,
) -> None:
    """Render all day files and month rollups."""

    events = load_key_events(events_path)
    rows = read_hits(hits_path)
    source_cache: dict[str, str] = {}
    day_hits = group_hits_by_day(
        rows,
        corpus_root=corpus_root.resolve(),
        context_chars=context_chars,
        source_cache=source_cache,
    )
    output_root.mkdir(parents=True, exist_ok=True)
    by_day = output_root / "by-day"
    by_month = output_root / "by-month"
    by_day.mkdir(parents=True, exist_ok=True)
    by_month.mkdir(parents=True, exist_ok=True)

    summaries: dict[str, dict[str, int]] = {}
    for current in iter_days(start_date, end_date):
        hits = day_hits.get(current.isoformat(), [])
        summaries[current.isoformat()] = _category_counts(hits)
        (by_day / f"{current.isoformat()}.md").write_text(
            render_day(current, hits, events["days"].get(current.isoformat())),
            encoding="utf-8",
        )

    for month in iter_months(start_date, end_date):
        (by_month / f"{month}.md").write_text(
            render_month(month, summaries, events),
            encoding="utf-8",
        )


def load_key_events(path: Path) -> dict[str, dict[str, EventInfo]]:
    """Load the small key event YAML file without requiring PyYAML."""

    events: dict[str, dict[str, EventInfo]] = {"days": {}, "months": {}}
    section: str | None = None
    current_key: str | None = None
    current_values: dict[str, str] = {}

    def flush() -> None:
        nonlocal current_key, current_values
        if section in events and current_key is not None:
            events[section][current_key] = EventInfo(
                label=current_values.get("label", ""),
                note=current_values.get("note", ""),
            )
        current_key = None
        current_values = {}

    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.split("#", maxsplit=1)[0].rstrip()
        if not line.strip():
            continue
        stripped = line.strip()
        if stripped in {"days:", "months:"}:
            flush()
            section = stripped[:-1]
            continue
        if section not in events:
            continue

        key_match = re.match(r"^(?P<key>\d{4}-\d{2}(?:-\d{2})?):\s*$", stripped)
        if key_match:
            flush()
            current_key = key_match.group("key")
            continue

        value_match = re.match(r"^(?P<field>label|note):\s*(?P<value>.*)$", stripped)
        if current_key is not None and value_match:
            current_values[value_match.group("field")] = _strip_yaml_scalar(
                value_match.group("value")
            )

    flush()
    return events


def read_hits(path: Path) -> list[dict[str, Any]]:
    """Read extracted hits from parquet."""

    _, parquet = _load_pyarrow()
    return parquet.read_table(path).to_pylist()


def group_hits_by_day(
    rows: Iterable[dict[str, Any]],
    *,
    corpus_root: Path,
    context_chars: int,
    source_cache: dict[str, str],
) -> dict[str, list[RenderHit]]:
    grouped: dict[str, list[RenderHit]] = defaultdict(list)
    for row in rows:
        if row.get("bucket_type") != "day" or not row.get("referenced_date"):
            continue
        source_path = str(row.get("source_path", ""))
        matched_text = str(row.get("matched_text") or "")
        stored_context = _stored_context(row)
        grouped[str(row["referenced_date"])].append(
            RenderHit(
                source_path=source_path,
                filename=str(row.get("filename") or Path(source_path).name),
                rif_number=row.get("rif_number"),
                doc_date=str(row.get("doc_date") or "unknown"),
                originating_agency=str(row.get("originating_agency") or "unknown"),
                matched_text=matched_text,
                span_start=int(row.get("span_start") or 0),
                span_end=int(row.get("span_end") or 0),
                context=(
                    _source_context(
                        corpus_root=corpus_root,
                        source_path=source_path,
                        span_start=int(row.get("span_start") or 0),
                        span_end=int(row.get("span_end") or 0),
                        context_chars=context_chars,
                        matched_text=matched_text,
                        source_cache=source_cache,
                    )
                    or stored_context
                ),
            )
        )

    for hits in grouped.values():
        hits.sort(key=_hit_sort_key)
    return dict(grouped)


def render_day(current: date, hits: list[RenderHit], event: EventInfo | None) -> str:
    label = f" — {event.label}" if event and event.label else ""
    lines = [f"# {_human_date(current)}{label}", ""]
    if event and event.note:
        lines.extend([f"> {event.note}", ""])
    lines.extend(["---", ""])

    if not hits:
        lines.extend(
            [
                "No references in 2025 release.",
                "",
                "---",
                "",
                "## Contemporaneous (1961)",
                "",
                "No contemporaneous hits.",
                "",
                "---",
                "",
                "## Retrospective",
                "",
                "No retrospective hits.",
                "",
                "---",
                "",
                _reference_summary(hits),
                "",
            ]
        )
        return "\n".join(lines)

    categories = _categorize_hits(hits)
    lines.extend(
        _render_section(
            "Contemporaneous (1961)",
            categories["contemporaneous"],
            "No contemporaneous hits.",
            heading_level=3,
        )
    )
    lines.extend(["---", ""])
    lines.extend(
        _render_grouped_section(
            "Retrospective",
            categories["retrospective"],
            "No retrospective hits.",
        )
    )
    if categories["unknown"]:
        lines.extend(["---", ""])
        lines.extend(
            _render_section(
                "Document Date Unknown",
                categories["unknown"],
                "No unknown-date hits.",
                heading_level=4,
            )
        )
    lines.extend(["---", "", _reference_summary(hits), ""])
    return "\n".join(lines)


def render_month(
    month: str,
    summaries: dict[str, dict[str, int]],
    events: dict[str, dict[str, EventInfo]],
) -> str:
    event = events["months"].get(month)
    label = f" - {event.label}" if event and event.label else ""
    lines = [f"# {month}{label}", ""]
    if event and event.note:
        lines.extend([f"> {event.note}", ""])

    days = [day for day in sorted(summaries) if day.startswith(month)]
    lines.extend(
        [
            "## Hit Counts",
            "",
            "| Date | Label | Contemporaneous | Retrospective | Unknown Date | Total |",
            "| --- | --- | ---: | ---: | ---: | ---: |",
        ]
    )
    for day in days:
        counts = summaries[day]
        day_event = events["days"].get(day)
        lines.append(
            "| {day} | {label} | {contemporaneous} | {retrospective} | "
            "{unknown} | {total} |".format(
                day=day,
                label=_table_cell(day_event.label if day_event else ""),
                contemporaneous=counts["contemporaneous"],
                retrospective=counts["retrospective"],
                unknown=counts["unknown"],
                total=counts["total"],
            )
        )

    top_days = sorted(
        ((day, summaries[day]["total"]) for day in days),
        key=lambda item: (-item[1], item[0]),
    )[:5]
    lines.extend(
        [
            "",
            "## Top 5 Referenced Days",
            "",
            "| Rank | Date | Label | Hits |",
            "| ---: | --- | --- | ---: |",
        ]
    )
    for rank, (day, total) in enumerate(top_days, start=1):
        day_event = events["days"].get(day)
        lines.append(
            f"| {rank} | {day} | {_table_cell(day_event.label if day_event else '')} | "
            f"{total} |"
        )
    lines.append("")
    return "\n".join(lines)


def iter_days(start_date: date, end_date: date) -> Iterable[date]:
    current = start_date
    while current <= end_date:
        yield current
        current += timedelta(days=1)


def iter_months(start_date: date, end_date: date) -> Iterable[str]:
    current = date(start_date.year, start_date.month, 1)
    end_month = date(end_date.year, end_date.month, 1)
    while current <= end_month:
        yield f"{current.year:04d}-{current.month:02d}"
        _, days_in_month = calendar.monthrange(current.year, current.month)
        current += timedelta(days=days_in_month)


def _render_section(
    title: str,
    hits: list[RenderHit],
    empty_text: str,
    *,
    heading_level: int,
) -> list[str]:
    lines = [f"## {title}", ""]
    if not hits:
        lines.extend([empty_text, ""])
        return lines
    for hit in hits:
        lines.extend(_render_hit(hit, heading_level=heading_level))
    return lines


def _render_grouped_section(
    title: str,
    hits: list[RenderHit],
    empty_text: str,
) -> list[str]:
    lines = [f"## {title}", ""]
    if not hits:
        lines.extend([empty_text, ""])
        return lines

    grouped: dict[str, list[RenderHit]] = defaultdict(list)
    for hit in hits:
        grouped[hit.originating_agency or "unknown"].append(hit)
    for agency in sorted(grouped):
        lines.extend([f"### {_escape_heading(_agency_display(agency))}", ""])
        for hit in sorted(grouped[agency], key=_hit_sort_key):
            lines.extend(_render_hit(hit, heading_level=4))
    return lines


def _render_hit(hit: RenderHit, *, heading_level: int) -> list[str]:
    heading = "#" * heading_level
    return [
        f"{heading} {_escape_heading(_source_label(hit))}",
        f"**{_agency_display(hit.originating_agency)}** · "
        f"{_document_date_display(hit.doc_date)} · "
        f"Source: {_source_markdown_link(hit.source_path, hit.filename)} · "
        f"NARA release: [2025 JFK Assassination Records Release](<{NARA_RELEASE_URL}>)",
        "",
        "Excerpt (+/-300 characters around match):",
        "",
        *_blockquote(hit.context),
        "",
    ]


def _categorize_hits(hits: list[RenderHit]) -> dict[str, list[RenderHit]]:
    categories = {
        "contemporaneous": [],
        "retrospective": [],
        "unknown": [],
    }
    for hit in hits:
        year = _doc_year(hit.doc_date)
        if year == 1961:
            categories["contemporaneous"].append(hit)
        elif year and year > 1961:
            categories["retrospective"].append(hit)
        else:
            categories["unknown"].append(hit)
    return categories


def _category_counts(hits: list[RenderHit]) -> dict[str, int]:
    categories = _categorize_hits(hits)
    counts = {name: len(values) for name, values in categories.items()}
    counts["total"] = sum(counts.values())
    return counts


def _source_context(
    *,
    corpus_root: Path,
    source_path: str,
    span_start: int,
    span_end: int,
    context_chars: int,
    matched_text: str,
    source_cache: dict[str, str],
) -> str:
    if not source_path:
        return ""
    if source_path not in source_cache:
        path = corpus_root / source_path
        if not path.exists():
            return ""
        source_cache[source_path] = path.read_text(encoding="utf-8", errors="replace")

    text = source_cache[source_path]
    match_span = _resolve_match_span(text, span_start, span_end, matched_text)
    if match_span is None:
        return ""
    start = max(0, match_span[0] - context_chars)
    end = min(len(text), match_span[1] + context_chars)
    return scrub_security_numbers(text[start:end].strip())


def _resolve_match_span(
    text: str,
    span_start: int,
    span_end: int,
    matched_text: str,
) -> tuple[int, int] | None:
    if 0 <= span_start <= span_end <= len(text):
        if not matched_text or text[span_start:span_end] == matched_text:
            return (span_start, span_end)

    if not matched_text:
        return None

    local_start = max(0, span_start - 2_000)
    local_end = min(len(text), span_end + 2_000)
    local_at = text.find(matched_text, local_start, local_end)
    if local_at >= 0:
        return (local_at, local_at + len(matched_text))

    global_at = text.find(matched_text)
    if global_at >= 0:
        return (global_at, global_at + len(matched_text))
    return None


def _stored_context(row: dict[str, Any]) -> str:
    return scrub_security_numbers(str(row.get("context_window") or row.get("context") or ""))


def _hit_sort_key(hit: RenderHit) -> tuple[int, str, str, int]:
    year = _doc_year(hit.doc_date)
    sortable_year = year if year is not None else 9999
    return (sortable_year, hit.doc_date, hit.source_path, hit.span_start)


def _doc_year(raw: str) -> int | None:
    match = re.match(r"^(?P<year>\d{4})-\d{2}-\d{2}$", raw)
    return int(match.group("year")) if match else None


def _strip_yaml_scalar(raw: str) -> str:
    value = raw.strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in {'"', "'"}:
        return value[1:-1]
    return value


def _escape_heading(raw: str) -> str:
    return raw.replace("\n", " ").strip() or "Unknown Source"


def _blockquote(raw: str) -> list[str]:
    if not raw:
        return [">"]
    return [f"> {line}" if line else ">" for line in raw.splitlines()]


def _table_cell(raw: str) -> str:
    return raw.replace("|", "\\|")


def _source_markdown_link(source_path: str, filename: str) -> str:
    if not source_path:
        return filename or "`unknown`"
    target = f"https://github.com/doctly/jfk/blob/main/{source_path}"
    label = filename or source_path
    return f"[{label}](<{target}>)"


def _source_label(hit: RenderHit) -> str:
    return hit.rif_number or Path(hit.filename).stem or hit.filename


def _agency_display(raw: str) -> str:
    value = raw.strip()
    if not value or value.lower() == "unknown":
        return "Agency unknown"
    return value


def _document_date_display(raw: str) -> str:
    parsed = _parse_iso_date(raw)
    if parsed:
        return f"Document dated {_human_date(parsed)}"
    return "Document date not identified"


def _human_date(value: date) -> str:
    return f"{MONTH_NAMES[value.month]} {value.day}, {value.year}"


def _parse_iso_date(raw: str) -> date | None:
    try:
        return date.fromisoformat(raw)
    except ValueError:
        return None


def _reference_summary(hits: list[RenderHit]) -> str:
    references = len(hits)
    documents = len({hit.source_path for hit in hits})
    reference_word = "reference" if references == 1 else "references"
    document_word = "document" if documents == 1 else "documents"
    return (
        f"*{references} {reference_word} from {documents} {document_word} "
        "in the 2025 NARA JFK release.*"
    )


def _load_pyarrow() -> tuple[Any, Any]:
    try:
        import pyarrow  # type: ignore[import-not-found]
        import pyarrow.parquet as parquet  # type: ignore[import-not-found]
    except ImportError as exc:
        msg = "pyarrow is required to render chronology files from parquet."
        raise SystemExit(msg) from exc
    return pyarrow, parquet


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--hits", type=Path, default=DEFAULT_HITS_PATH)
    parser.add_argument("--events", type=Path, default=DEFAULT_EVENTS_PATH)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT_ROOT)
    parser.add_argument("--corpus", type=Path, default=DEFAULT_CORPUS_ROOT)
    parser.add_argument("--context-chars", type=int, default=DEFAULT_CONTEXT_CHARS)
    parser.add_argument("--start-date", type=date.fromisoformat, default=DEFAULT_START_DATE)
    parser.add_argument("--end-date", type=date.fromisoformat, default=DEFAULT_END_DATE)
    return parser.parse_args()


def main() -> None:
    args = _parse_args()
    render_chronology(
        hits_path=args.hits,
        events_path=args.events,
        output_root=args.output,
        corpus_root=args.corpus,
        start_date=args.start_date,
        end_date=args.end_date,
        context_chars=args.context_chars,
    )
    day_count = sum(1 for _ in iter_days(args.start_date, args.end_date))
    month_count = sum(1 for _ in iter_months(args.start_date, args.end_date))
    print(f"wrote {day_count} day files and {month_count} month rollups to {args.output}")


if __name__ == "__main__":
    main()
