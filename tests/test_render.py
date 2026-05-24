"""Tests for markdown rendering helpers."""
from __future__ import annotations

import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from render import (
    EventInfo,
    RenderHit,
    group_hits_by_day,
    load_key_events,
    render_day,
)


def test_load_key_events_without_yaml_dependency(tmp_path: Path) -> None:
    events_path = tmp_path / "events.yaml"
    events_path.write_text(
        """
days:
  1961-04-17:
    label: "Bay of Pigs invasion"
    note: "Landing begins."
months:
  1961-04:
    label: "Bay of Pigs crisis"
    note: "Failed invasion."
""",
        encoding="utf-8",
    )

    events = load_key_events(events_path)

    assert events["days"]["1961-04-17"].label == "Bay of Pigs invasion"
    assert events["months"]["1961-04"].note == "Failed invasion."


def test_render_day_keeps_unknown_date_hits_visible() -> None:
    hit = RenderHit(
        source_path="jfk_files_md/104/104-00000-00000.md",
        filename="104-00000-00000.md",
        rif_number="104-00000-00000",
        doc_date="unknown",
        originating_agency="unknown",
        matched_text="January 21, 1961",
        span_start=10,
        span_end=26,
        context="Before January 21, 1961 after.",
    )

    output = render_day(date(1961, 1, 21), [hit], EventInfo())

    assert "## Contemporaneous (1961)" in output
    assert "## Retrospective" in output
    assert "## Document Date Unknown" in output
    assert "# January 21, 1961" in output
    assert "[104-00000-00000.md]" in output
    assert "**Agency unknown** · Document date not identified" in output
    assert "NARA release: [2025 JFK Assassination Records Release]" in output
    assert "https://www.archives.gov/research/jfk/release-2025" in output
    assert "Excerpt (+/-300 characters around match):" in output
    assert "Before January 21, 1961 after." in output
    assert "*1 reference from 1 document in the 2025 NARA JFK release.*" in output


def test_render_day_zero_hit_stub_uses_required_text() -> None:
    output = render_day(date(1961, 1, 22), [], EventInfo())

    assert "No references in 2025 release." in output


def test_render_day_groups_retrospective_hits_by_agency() -> None:
    hit = RenderHit(
        source_path="jfk_files_md/157/157-00000-00000.md",
        filename="157-00000-00000.md",
        rif_number="157-00000-00000",
        doc_date="1975-09-01",
        originating_agency="SSCIA",
        matched_text="April 17, 1961",
        span_start=10,
        span_end=24,
        context="Before April 17, 1961 after.",
    )

    output = render_day(date(1961, 4, 17), [hit], EventInfo())

    assert "## Retrospective\n\n### SSCIA" in output
    assert "#### 157-00000-00000" in output
    assert "**SSCIA** · Document dated September 1, 1975" in output


def test_group_hits_by_day_recomputes_context_from_source(tmp_path: Path) -> None:
    source = "x" * 350 + "April 17, 1961" + "y" * 350
    source_root = tmp_path
    source_path = source_root / "jfk_files_md" / "104" / "sample.md"
    source_path.parent.mkdir(parents=True, exist_ok=True)
    source_path.write_text(source, encoding="utf-8")
    rows = [
        {
            "bucket_type": "day",
            "referenced_date": "1961-04-17",
            "source_path": "jfk_files_md/104/sample.md",
            "filename": "sample.md",
            "rif_number": None,
            "doc_date": "1975-09-01",
            "originating_agency": "SSCIA",
            "matched_text": "April 17, 1961",
            "span_start": 350,
            "span_end": 364,
            "context_window": "stale parquet context",
        }
    ]

    grouped = group_hits_by_day(
        rows,
        corpus_root=source_root,
        context_chars=300,
        source_cache={},
    )

    assert grouped["1961-04-17"][0].context == f"{'x' * 300}April 17, 1961{'y' * 300}"


def test_group_hits_by_day_falls_back_to_stored_context_window() -> None:
    rows = [
        {
            "bucket_type": "day",
            "referenced_date": "1961-04-17",
            "source_path": "missing.md",
            "filename": "missing.md",
            "rif_number": None,
            "doc_date": "1975-09-01",
            "originating_agency": "SSCIA",
            "matched_text": "April 17, 1961",
            "span_start": 999,
            "span_end": 1013,
            "context_window": "Stored context around April 17, 1961.",
        }
    ]

    grouped = group_hits_by_day(
        rows,
        corpus_root=Path("/definitely/missing"),
        context_chars=300,
        source_cache={},
    )

    assert grouped["1961-04-17"][0].context == "Stored context around April 17, 1961."
