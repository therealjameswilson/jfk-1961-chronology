"""Scan the JFK markdown corpus for 1961 date references."""
from __future__ import annotations

import argparse
import hashlib
import os
import re
from concurrent.futures import ProcessPoolExecutor
from datetime import date
from pathlib import Path
from typing import Any, Iterable

try:
    from .date_patterns import DateHit, HitType, extract_dates
    from .metadata import (
        DEFAULT_CORPUS_ROOT,
        DEFAULT_OUTPUT_PATH as DEFAULT_METADATA_PATH,
        build_metadata,
        discover_markdown_files,
        write_metadata_parquet,
        write_review_queue,
    )
except ImportError:
    from date_patterns import DateHit, HitType, extract_dates
    from metadata import (
        DEFAULT_CORPUS_ROOT,
        DEFAULT_OUTPUT_PATH as DEFAULT_METADATA_PATH,
        build_metadata,
        discover_markdown_files,
        write_metadata_parquet,
        write_review_queue,
    )


DEFAULT_OUTPUT_PATH = Path("data/hits.parquet")
DEFAULT_START_DATE = date(1961, 1, 20)
DEFAULT_END_DATE = date(1961, 12, 31)
DEFAULT_CONTEXT_WORDS = 300
WORD_RE = re.compile(r"\S+")


def scan_corpus(
    corpus_root: Path,
    metadata_path: Path,
    output_path: Path,
    *,
    start_date: date = DEFAULT_START_DATE,
    end_date: date = DEFAULT_END_DATE,
    context_words: int = DEFAULT_CONTEXT_WORDS,
    workers: int | None = None,
) -> list[dict[str, Any]]:
    """Scan source files and write raw hit rows to parquet."""

    metadata_rows = _load_or_build_metadata(corpus_root, metadata_path)
    metadata_by_path = {str(row["source_path"]): row for row in metadata_rows}
    files = discover_markdown_files(corpus_root)
    worker_count = _worker_count(workers)

    tasks = [
        (
            path,
            corpus_root,
            metadata_by_path.get(path.relative_to(corpus_root).as_posix(), {}),
            start_date.isoformat(),
            end_date.isoformat(),
            context_words,
        )
        for path in files
    ]
    if worker_count == 1:
        nested_rows = [_scan_file(task) for task in tasks]
    else:
        with ProcessPoolExecutor(max_workers=worker_count) as pool:
            nested_rows = list(pool.map(_scan_file, tasks, chunksize=8))

    rows = [row for file_rows in nested_rows for row in file_rows]
    rows.sort(
        key=lambda row: (
            str(row["bucket"]),
            str(row["source_path"]),
            int(row["span_start"]),
            str(row["matched_text"]),
        )
    )
    write_hits_parquet(rows, output_path)
    return rows


def write_hits_parquet(rows: Iterable[dict[str, Any]], output_path: Path) -> None:
    """Write hit rows to parquet using pyarrow."""

    pyarrow, parquet = _load_pyarrow()
    output_path.parent.mkdir(parents=True, exist_ok=True)
    table = pyarrow.Table.from_pylist(list(rows), schema=_hit_schema(pyarrow))
    parquet.write_table(table, output_path)


def _scan_file(task: tuple[Path, Path, dict[str, Any], str, str, int]) -> list[dict[str, Any]]:
    path, corpus_root, metadata, start_raw, end_raw, context_words = task
    text = path.read_text(encoding="utf-8", errors="replace")
    source_path = path.relative_to(corpus_root).as_posix()
    rows: list[dict[str, Any]] = []

    for hit in extract_dates(text):
        buckets = _hit_buckets(hit, date.fromisoformat(start_raw), date.fromisoformat(end_raw))
        if not buckets:
            continue
        context = _context_window(text, hit.span, context_words)
        for bucket in buckets:
            rows.append(
                _hit_row(
                    hit=hit,
                    bucket=bucket,
                    context=context,
                    source_path=source_path,
                    filename=path.name,
                    metadata=metadata,
                )
            )
    return rows


def _hit_buckets(
    hit: DateHit,
    start_date: date,
    end_date: date,
) -> list[dict[str, str | None]]:
    if hit.hit_type in {HitType.DAY, HitType.RANGE}:
        buckets = []
        for raw in hit.dates:
            parsed = date.fromisoformat(raw)
            if start_date <= parsed <= end_date:
                buckets.append(
                    {
                        "bucket": raw,
                        "bucket_type": "day",
                        "referenced_date": raw,
                        "referenced_month": raw[:7],
                        "referenced_quarter": _quarter_for_month(parsed.month),
                    }
                )
        return buckets

    if hit.hit_type == HitType.MONTH and hit.month and _month_in_scope(hit.month):
        return [
            {
                "bucket": f"month-level/{hit.month}",
                "bucket_type": "month",
                "referenced_date": None,
                "referenced_month": hit.month,
                "referenced_quarter": _quarter_for_month(int(hit.month[-2:])),
            }
        ]

    if hit.hit_type == HitType.QUARTER and hit.quarter_label:
        quarter = _quarter_from_label(hit.quarter_label)
        if quarter:
            return [
                {
                    "bucket": f"quarter-level/{quarter}",
                    "bucket_type": "quarter",
                    "referenced_date": None,
                    "referenced_month": None,
                    "referenced_quarter": quarter,
                }
            ]
    return []


def _hit_row(
    *,
    hit: DateHit,
    bucket: dict[str, str | None],
    context: str,
    source_path: str,
    filename: str,
    metadata: dict[str, Any],
) -> dict[str, Any]:
    hit_id = _hit_id(source_path, hit, bucket["bucket"])
    return {
        "hit_id": hit_id,
        "source_path": source_path,
        "filename": filename,
        "rif_number": metadata.get("rif_number"),
        "doc_date": metadata.get("doc_date", "unknown"),
        "originating_agency": metadata.get("originating_agency", "unknown"),
        "hit_type": hit.hit_type.value,
        "matched_text": hit.matched_text,
        "span_start": hit.span[0],
        "span_end": hit.span[1],
        "bucket": bucket["bucket"],
        "bucket_type": bucket["bucket_type"],
        "referenced_date": bucket["referenced_date"],
        "referenced_month": bucket["referenced_month"],
        "referenced_quarter": bucket["referenced_quarter"],
        "context": context,
        "context_word_count": len(WORD_RE.findall(context)),
    }


def _context_window(text: str, span: tuple[int, int], context_words: int) -> str:
    word_spans = [match.span() for match in WORD_RE.finditer(text)]
    if not word_spans:
        return ""

    hit_word_index = 0
    for index, word_span in enumerate(word_spans):
        if word_span[1] >= span[0]:
            hit_word_index = index
            break

    start_index = max(0, hit_word_index - context_words)
    end_index = min(len(word_spans), hit_word_index + context_words + 1)
    start_char = word_spans[start_index][0]
    end_char = word_spans[end_index - 1][1]
    return text[start_char:end_char].strip()


def _hit_id(source_path: str, hit: DateHit, bucket: str | None) -> str:
    raw = f"{source_path}:{hit.span[0]}:{hit.span[1]}:{hit.matched_text}:{bucket}"
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()


def _month_in_scope(raw: str) -> bool:
    return raw.startswith("1961-")


def _quarter_for_month(month: int) -> str:
    quarter = ((month - 1) // 3) + 1
    return f"1961-Q{quarter}"


def _quarter_from_label(label: str) -> str | None:
    lowered = label.lower()
    if "1961" not in lowered:
        return None
    if "q1" in lowered or "first quarter" in lowered or "winter" in lowered:
        return "1961-Q1"
    if "q2" in lowered or "second quarter" in lowered or "spring" in lowered:
        return "1961-Q2"
    if "q3" in lowered or "third quarter" in lowered or "summer" in lowered:
        return "1961-Q3"
    if "q4" in lowered or "fourth quarter" in lowered or "fall" in lowered:
        return "1961-Q4"
    if "autumn" in lowered or "late" in lowered:
        return "1961-Q4"
    if "early" in lowered:
        return "1961-Q1"
    if "mid" in lowered:
        return "1961-Q2"
    return None


def _load_or_build_metadata(corpus_root: Path, metadata_path: Path) -> list[dict[str, Any]]:
    if metadata_path.exists():
        return _read_parquet(metadata_path)

    rows = build_metadata(corpus_root)
    write_metadata_parquet(rows, metadata_path)
    write_review_queue(rows, Path("data/review_queue.jsonl"))
    return [
        {
            "filename": row.filename,
            "source_path": row.source_path,
            "rif_number": row.rif_number,
            "doc_date": row.doc_date,
            "originating_agency": row.originating_agency,
        }
        for row in rows
    ]


def _read_parquet(path: Path) -> list[dict[str, Any]]:
    _, parquet = _load_pyarrow()
    table = parquet.read_table(path)
    return table.to_pylist()


def _worker_count(workers: int | None) -> int:
    if workers is not None:
        return max(1, workers)
    cpu_count = os.cpu_count() or 2
    return max(1, cpu_count - 1)


def _hit_schema(pyarrow: Any) -> Any:
    return pyarrow.schema(
        [
            ("hit_id", pyarrow.string()),
            ("source_path", pyarrow.string()),
            ("filename", pyarrow.string()),
            ("rif_number", pyarrow.string()),
            ("doc_date", pyarrow.string()),
            ("originating_agency", pyarrow.string()),
            ("hit_type", pyarrow.string()),
            ("matched_text", pyarrow.string()),
            ("span_start", pyarrow.int64()),
            ("span_end", pyarrow.int64()),
            ("bucket", pyarrow.string()),
            ("bucket_type", pyarrow.string()),
            ("referenced_date", pyarrow.string()),
            ("referenced_month", pyarrow.string()),
            ("referenced_quarter", pyarrow.string()),
            ("context", pyarrow.string()),
            ("context_word_count", pyarrow.int64()),
        ]
    )


def _load_pyarrow() -> tuple[Any, Any]:
    try:
        import pyarrow  # type: ignore[import-not-found]
        import pyarrow.parquet as parquet  # type: ignore[import-not-found]
    except ImportError as exc:
        msg = (
            "pyarrow is required to read and write parquet files. Install the "
            "project dependencies, then rerun this command."
        )
        raise SystemExit(msg) from exc
    return pyarrow, parquet


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--corpus", type=Path, default=DEFAULT_CORPUS_ROOT)
    parser.add_argument("--metadata", type=Path, default=DEFAULT_METADATA_PATH)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT_PATH)
    parser.add_argument("--context-words", type=int, default=DEFAULT_CONTEXT_WORDS)
    parser.add_argument("--start-date", type=date.fromisoformat, default=DEFAULT_START_DATE)
    parser.add_argument("--end-date", type=date.fromisoformat, default=DEFAULT_END_DATE)
    parser.add_argument("--workers", type=int, default=None)
    return parser.parse_args()


def main() -> None:
    args = _parse_args()
    rows = scan_corpus(
        corpus_root=args.corpus.resolve(),
        metadata_path=args.metadata,
        output_path=args.output,
        start_date=args.start_date,
        end_date=args.end_date,
        context_words=args.context_words,
        workers=args.workers,
    )
    print(f"wrote {len(rows)} hit rows to {args.output}")


if __name__ == "__main__":
    main()
