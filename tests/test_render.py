"""Tests for markdown rendering helpers."""
from __future__ import annotations

import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from render import EventInfo, RenderHit, load_key_events, render_day


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
    assert "[jfk_files_md/104/104-00000-00000.md]" in output
    assert "Before January 21, 1961 after." in output


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
