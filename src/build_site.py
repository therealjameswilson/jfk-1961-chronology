"""Build a static GitHub Pages site for the 1961-1963 JFK chronologies."""
from __future__ import annotations

import calendar
import html
import json
import re
import shutil
from collections import Counter
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path
from typing import Any, Iterable

import markdown

try:
    from .redaction import scrub_security_numbers
except ImportError:
    from redaction import scrub_security_numbers


REPO_ROOT = Path(__file__).resolve().parents[1]
DOCS_ROOT = REPO_ROOT / "docs"
MD_EXTENSIONS = ["tables", "fenced_code", "nl2br", "sane_lists"]
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
class YearConfig:
    year: int
    repo_root: Path
    start_date: date
    end_date: date


@dataclass(frozen=True)
class EventInfo:
    label: str = ""
    note: str = ""


@dataclass(frozen=True)
class YearStats:
    year: int
    total_documents: int
    total_hits: int
    distinct_hit_days: int
    total_possible_days: int
    top_days: tuple[tuple[str, int], ...]
    daily_files: int
    monthly_files: int
    day_hit_counts: dict[str, int]


YEAR_CONFIGS = (
    YearConfig(1961, REPO_ROOT, date(1961, 1, 20), date(1961, 12, 31)),
    YearConfig(1962, REPO_ROOT.parent / "jfk-1962-chronology-fresh", date(1962, 1, 1), date(1962, 12, 31)),
    YearConfig(1963, REPO_ROOT.parent / "jfk-1963-chronology-fresh", date(1963, 1, 1), date(1963, 11, 22)),
)


def main() -> None:
    year_data = []
    for config in YEAR_CONFIGS:
        events = load_key_events(config.repo_root / "key_events.yaml")
        stats = collect_stats(config)
        year_data.append((config, events, stats))

    if DOCS_ROOT.exists():
        shutil.rmtree(DOCS_ROOT)
    DOCS_ROOT.mkdir(parents=True)
    write_text(DOCS_ROOT / "style.css", site_css())
    write_text(DOCS_ROOT / "search.js", search_js())
    write_text(DOCS_ROOT / "search-index.json", build_search_index(year_data))
    write_text(DOCS_ROOT / "search.html", render_search_page())
    write_text(DOCS_ROOT / "index.html", render_landing(year_data))

    html_count = 2
    for config, events, stats in year_data:
        html_count += build_year(config, events, stats)

    print_summary(year_data, html_count)


def collect_stats(config: YearConfig) -> YearStats:
    metadata_rows = read_parquet(config.repo_root / "data/metadata.parquet")
    hit_rows = read_parquet(config.repo_root / "data/hits.parquet")
    day_hits = [
        row
        for row in hit_rows
        if row.get("bucket_type") == "day"
        and row.get("referenced_date")
        and config.start_date <= date.fromisoformat(str(row["referenced_date"])) <= config.end_date
    ]
    hit_counts = Counter(str(row["referenced_date"]) for row in day_hits)
    top_days = tuple(hit_counts.most_common(10))
    return YearStats(
        year=config.year,
        total_documents=len(metadata_rows),
        total_hits=len(hit_rows),
        distinct_hit_days=len(hit_counts),
        total_possible_days=sum(1 for _ in iter_days(config.start_date, config.end_date)),
        top_days=top_days,
        daily_files=count_files(config.repo_root / "output/by-day", "*.md"),
        monthly_files=count_files(config.repo_root / "output/by-month", "*.md"),
        day_hit_counts=dict(hit_counts),
    )


def build_year(config: YearConfig, events: dict[str, dict[str, EventInfo]], stats: YearStats) -> int:
    year_root = DOCS_ROOT / str(config.year)
    year_root.mkdir(parents=True)
    write_text(year_root / "index.html", render_year_index(config, events, stats))
    html_count = 1

    for month in iter_months(config.start_date, config.end_date):
        month_root = year_root / f"{month.month:02d}"
        month_root.mkdir(parents=True)
        markdown_path = config.repo_root / "output/by-month" / f"{month.year:04d}-{month.month:02d}.md"
        write_text(month_root / "index.html", render_month_page(config, month, markdown_path))
        html_count += 1

    for current in iter_days(config.start_date, config.end_date):
        markdown_path = config.repo_root / "output/by-day" / f"{current.isoformat()}.md"
        write_text(year_root / f"{current.month:02d}-{current.day:02d}.html", render_day_page(config, current, markdown_path))
        html_count += 1

    return html_count


def render_landing(
    year_data: list[tuple[YearConfig, dict[str, dict[str, EventInfo]], YearStats]]
) -> str:
    rows = []
    links = []
    for config, _, stats in year_data:
        rows.append(
            "<tr>"
            f"<th scope=\"row\">{config.year}</th>"
            f"<td>{stats.total_documents:,}</td>"
            f"<td>{stats.total_hits:,}</td>"
            f"<td>{stats.distinct_hit_days:,} / {stats.total_possible_days:,}</td>"
            f"<td>{stats.daily_files:,}</td>"
            f"<td>{stats.monthly_files:,}</td>"
            "</tr>"
        )
        links.append(f"<li><a href=\"{config.year}/index.html\">{config.year} calendar</a></li>")

    body = f"""
<h1>JFK Presidency Day-by-Day: What the 2025 Assassination Records Release Says</h1>
<p>This site presents a dual-axis chronology of the Kennedy presidency from the 2025 NARA JFK assassination records release, using the doctly/jfk markdown corpus. Each day separates contemporaneous records from later retrospective references, preserving source excerpts generated by the automated metadata, date-extraction, and rendering pipeline.</p>

<h2>Summary Stats</h2>
<table>
  <thead>
    <tr>
      <th>Year</th>
      <th>Documents scanned</th>
      <th>Date hits</th>
      <th>Days with hits</th>
      <th>Daily files</th>
      <th>Monthly rollups</th>
    </tr>
  </thead>
  <tbody>
    {''.join(rows)}
  </tbody>
</table>

<h2>Calendars</h2>
<ul class="year-links">
  {''.join(links)}
</ul>
"""
    intro = (
        "This landing page introduces the public chronology, summarizes the "
        "three yearly datasets, and links to the browsable calendars."
    )
    return page("JFK Presidency Day-by-Day", body, "style.css", intro)


def render_search_page() -> str:
    body = """
<h1>Search</h1>
<p>Search dates, document IDs, agencies, key event labels, monthly rollups, and excerpt text. Results link to the generated chronology pages.</p>
<div id="search-results" class="search-results">Enter a search term above.</div>
<script src="search.js" defer></script>
"""
    intro = (
        "This search page looks across the generated daily pages, monthly "
        "rollups, calendars, and source excerpts in this public chronology."
    )
    return page("Search", body, "style.css", intro)


def render_year_index(
    config: YearConfig,
    events: dict[str, dict[str, EventInfo]],
    stats: YearStats,
) -> str:
    month_cards = []
    for month in iter_months(config.start_date, config.end_date):
        month_cards.append(render_calendar_month(config, events, stats, month))

    top_rows = []
    for day, hits in stats.top_days:
        parsed = date.fromisoformat(day)
        event = events["days"].get(day)
        label = event.label if event else ""
        top_rows.append(
            "<tr>"
            f"<td><a href=\"{parsed.month:02d}-{parsed.day:02d}.html\">{html.escape(day)}</a></td>"
            f"<td>{html.escape(label)}</td>"
            f"<td>{hits:,}</td>"
            "</tr>"
        )

    body = f"""
{breadcrumb([("Home", "../index.html"), (str(config.year), None)])}
<h1>{config.year}</h1>
<p>{stats.distinct_hit_days:,} of {stats.total_possible_days:,} days have at least one reference. Color density: white = 0, light blue = 1-5, medium blue = 6-20, dark blue = 21+.</p>
<div class="calendar-grid">
  {''.join(month_cards)}
</div>

<h2>Top 10 Days By Hit Count</h2>
<table>
  <thead><tr><th>Date</th><th>Label</th><th>Hits</th></tr></thead>
  <tbody>{''.join(top_rows)}</tbody>
</table>
"""
    intro = (
        f"This calendar shows every day in the {config.year} scope and uses "
        "color density to show how often each date is referenced in the release."
    )
    return page(f"{config.year} Calendar", body, "../style.css", intro)


def render_calendar_month(
    config: YearConfig,
    events: dict[str, dict[str, EventInfo]],
    stats: YearStats,
    month: date,
) -> str:
    cal = calendar.Calendar(firstweekday=6)
    header_cells = "".join(f"<th>{day}</th>" for day in ("Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"))
    rows = []
    for week in cal.monthdatescalendar(month.year, month.month):
        cells = []
        for current in week:
            if current.month != month.month or current < config.start_date or current > config.end_date:
                cells.append("<td class=\"out-of-scope\"></td>")
                continue
            day_key = current.isoformat()
            hits = stats.day_hit_counts.get(day_key, 0)
            event = events["days"].get(day_key)
            title = event.label if event and event.label else f"{_human_date(current)}: {hits} hits"
            if event and event.label:
                title = f"{event.label}: {hits} hits"
            cells.append(
                f"<td class=\"{density_class(hits)}\">"
                f"<a title=\"{html.escape(title, quote=True)}\" href=\"{current.month:02d}-{current.day:02d}.html\">"
                f"{current.day}</a></td>"
            )
        rows.append(f"<tr>{''.join(cells)}</tr>")

    return f"""
<section class="month-card">
  <h2><a href="{month.month:02d}/index.html">{MONTH_NAMES[month.month]}</a></h2>
  <table class="calendar-month">
    <thead><tr>{header_cells}</tr></thead>
    <tbody>{''.join(rows)}</tbody>
  </table>
</section>
"""


def render_month_page(config: YearConfig, month: date, markdown_path: Path) -> str:
    body = f"""
{breadcrumb([("Home", "../../index.html"), (str(config.year), "../index.html"), (MONTH_NAMES[month.month], None)])}
{markdown_to_html(markdown_path.read_text(encoding="utf-8"))}
"""
    intro = (
        f"This monthly rollup summarizes references to {MONTH_NAMES[month.month]} "
        f"{config.year}, including hit-count tables and the most-referenced days."
    )
    return page(f"{MONTH_NAMES[month.month]} {config.year}", body, "../../style.css", intro)


def render_day_page(config: YearConfig, current: date, markdown_path: Path) -> str:
    markdown_text = markdown_path.read_text(encoding="utf-8")
    day_nav = render_day_nav(config, current)
    body = f"""
{breadcrumb([
    ("Home", "../index.html"),
    (str(config.year), "index.html"),
    (MONTH_NAMES[current.month], f"{current.month:02d}/index.html"),
    (f"{MONTH_NAMES[current.month]} {current.day}", None),
])}
{day_nav}
<main class="daily-content">
{markdown_with_section_classes(markdown_text)}
</main>
{day_nav}
"""
    intro = (
        f"This daily page gathers records that mention {_human_date(current)}, "
        "separating same-year documents, later retrospective records, and records "
        "with unidentified document dates."
    )
    return page(_human_date(current), body, "../style.css", intro)


def render_day_nav(config: YearConfig, current: date) -> str:
    prev_day = current - timedelta(days=1)
    next_day = current + timedelta(days=1)
    prev_html = (
        f"<a href=\"{prev_day.month:02d}-{prev_day.day:02d}.html\">&larr; {_human_date(prev_day)}</a>"
        if prev_day >= config.start_date
        else "<span></span>"
    )
    next_html = (
        f"<a href=\"{next_day.month:02d}-{next_day.day:02d}.html\">{_human_date(next_day)} &rarr;</a>"
        if next_day <= config.end_date
        else "<span></span>"
    )
    return f"""
<nav class="day-nav" aria-label="Day navigation">
  {prev_html}
  <a href="index.html">{config.year} calendar</a>
  {next_html}
</nav>
"""


def markdown_with_section_classes(raw: str) -> str:
    segments: list[tuple[str | None, list[str]]] = []
    current_title: str | None = None
    current_lines: list[str] = []

    def flush() -> None:
        nonlocal current_lines, current_title
        if current_lines:
            segments.append((current_title, current_lines))
        current_title = None
        current_lines = []

    for line in raw.splitlines():
        if line.startswith("## "):
            flush()
            current_title = line[3:].strip()
        current_lines.append(line)
    flush()

    html_parts = []
    for title, lines in segments:
        segment_html = markdown_to_html("\n".join(lines))
        section_class = chronology_section_class(title or "")
        if section_class:
            html_parts.append(f"<section class=\"chron-section {section_class}\">{segment_html}</section>")
        else:
            html_parts.append(segment_html)
    return "\n".join(html_parts)


def markdown_to_html(raw: str) -> str:
    return markdown.markdown(scrub_security_numbers(raw), extensions=MD_EXTENSIONS)


def chronology_section_class(title: str) -> str:
    if title.startswith("Contemporaneous"):
        return "contemporaneous"
    if title.startswith("Retrospective"):
        return "retrospective"
    if title.startswith("Document Date Unknown"):
        return "unknown-date"
    return ""


def breadcrumb(items: list[tuple[str, str | None]]) -> str:
    parts = []
    for label, href in items:
        escaped = html.escape(label)
        if href:
            parts.append(f"<a href=\"{href}\">{escaped}</a>")
        else:
            parts.append(f"<span>{escaped}</span>")
    return f"<nav class=\"breadcrumb\" aria-label=\"Breadcrumb\">{' &gt; '.join(parts)}</nav>"


def page(title: str, body: str, css_href: str, intro: str) -> str:
    root_prefix = css_href.removesuffix("style.css")
    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{html.escape(title)}</title>
  <link rel="stylesheet" href="{css_href}">
</head>
<body>
{release_banner()}
{site_search(root_prefix)}
{page_intro(intro)}
{body}
</body>
</html>
"""


def site_search(root_prefix: str) -> str:
    return f"""
<form class="site-search" id="site-search-form" action="{root_prefix}search.html" method="get" role="search">
  <label for="site-search-input">Search this chronology</label>
  <div class="site-search-row">
    <input id="site-search-input" name="q" type="search" placeholder="Date, document ID, agency, excerpt..." autocomplete="off">
    <button type="submit">Search</button>
  </div>
</form>
"""


def page_intro(text: str) -> str:
    return f"""
<section class="page-intro" aria-label="Page introduction">
  <p>{html.escape(text)}</p>
</section>
"""


def release_banner() -> str:
    return """
<aside class="release-banner" role="note">
  <strong>Declassification note:</strong>
  All cited documents are part of NARA's
  <a href="https://www.archives.gov/research/jfk/release-2025">2025 JFK Assassination Records Release</a>
  and are presented as declassified public records. Any classification markings shown in the original files indicate their original classifications, not a current classification status.
</aside>
"""


def build_search_index(
    year_data: list[tuple[YearConfig, dict[str, dict[str, EventInfo]], YearStats]]
) -> str:
    entries: list[dict[str, str | int]] = [
        {
            "title": "JFK Presidency Day-by-Day",
            "url": "index.html",
            "type": "Overview",
            "date": "",
            "year": "",
            "content": (
                "Project landing page with methodology, summary statistics, "
                "and links to yearly calendars."
            ),
        },
        {
            "title": "Search",
            "url": "search.html",
            "type": "Search",
            "date": "",
            "year": "",
            "content": "Search the generated chronology pages.",
        },
    ]
    for config, events, stats in year_data:
        top_days = " ".join(day for day, _ in stats.top_days)
        entries.append(
            {
                "title": f"{config.year} calendar",
                "url": f"{config.year}/index.html",
                "type": "Calendar",
                "date": "",
                "year": config.year,
                "content": (
                    f"{config.year} calendar with {stats.distinct_hit_days} "
                    f"days containing references. Top days: {top_days}."
                ),
            }
        )
        for month in iter_months(config.start_date, config.end_date):
            month_key = f"{month.year:04d}-{month.month:02d}"
            markdown_path = config.repo_root / "output/by-month" / f"{month_key}.md"
            event = events["months"].get(month_key)
            title = f"{MONTH_NAMES[month.month]} {config.year}"
            if event and event.label:
                title = f"{title}: {event.label}"
            entries.append(
                {
                    "title": title,
                    "url": f"{config.year}/{month.month:02d}/index.html",
                    "type": "Month",
                    "date": month_key,
                    "year": config.year,
                    "content": search_text(markdown_path),
                }
            )
        for current in iter_days(config.start_date, config.end_date):
            day_key = current.isoformat()
            markdown_path = config.repo_root / "output/by-day" / f"{day_key}.md"
            event = events["days"].get(day_key)
            title = _human_date(current)
            if event and event.label:
                title = f"{title}: {event.label}"
            entries.append(
                {
                    "title": title,
                    "url": f"{config.year}/{current.month:02d}-{current.day:02d}.html",
                    "type": "Day",
                    "date": day_key,
                    "year": config.year,
                    "content": search_text(markdown_path),
                }
            )
    return json.dumps(entries, ensure_ascii=True, indent=2)


def search_text(path: Path) -> str:
    if not path.exists():
        return ""
    raw = scrub_security_numbers(path.read_text(encoding="utf-8"))
    raw = re.sub(r"<https?://[^>]+>", " ", raw)
    raw = re.sub(r"https?://\S+", " ", raw)
    raw = re.sub(r"[#>*_`|\\[\\]()]", " ", raw)
    return re.sub(r"\s+", " ", raw).strip()


def search_js() -> str:
    return r"""const form = document.getElementById("site-search-form");
const input = document.getElementById("site-search-input");
const results = document.getElementById("search-results");

const params = new URLSearchParams(window.location.search);
const initialQuery = params.get("q") || "";
if (input) {
  input.value = initialQuery;
}

if (form && input) {
  form.addEventListener("submit", (event) => {
    const query = input.value.trim();
    if (!query) {
      event.preventDefault();
      input.focus();
    }
  });
}

if (results) {
  if (initialQuery.trim()) {
    runSearch(initialQuery.trim());
  } else {
    results.innerHTML = '<p class="search-empty">Enter a search term above.</p>';
  }
}

async function runSearch(query) {
  results.innerHTML = '<p class="search-empty">Searching...</p>';
  const response = await fetch("search-index.json");
  const index = await response.json();
  const terms = query.toLowerCase().split(/\s+/).filter(Boolean);
  const matches = index
    .map((entry) => ({ entry, score: scoreEntry(entry, terms), snippet: snippet(entry.content || "", terms) }))
    .filter((item) => item.score > 0)
    .sort((a, b) => b.score - a.score || a.entry.title.localeCompare(b.entry.title))
    .slice(0, 50);

  if (!matches.length) {
    results.innerHTML = `<p class="search-empty">No results for <strong>${escapeHtml(query)}</strong>.</p>`;
    return;
  }

  results.innerHTML = `
    <p class="search-count">${matches.length} result${matches.length === 1 ? "" : "s"} for <strong>${escapeHtml(query)}</strong>.</p>
    <ol class="search-list">
      ${matches.map(renderResult).join("")}
    </ol>
  `;
}

function scoreEntry(entry, terms) {
  const title = (entry.title || "").toLowerCase();
  const date = (entry.date || "").toLowerCase();
  const type = (entry.type || "").toLowerCase();
  const content = (entry.content || "").toLowerCase();
  const haystack = `${title} ${date} ${type} ${content}`;
  if (!terms.every((term) => haystack.includes(term))) {
    return 0;
  }
  return terms.reduce((score, term) => {
    if (title.includes(term)) score += 12;
    if (date.includes(term)) score += 10;
    if (type.includes(term)) score += 4;
    const contentHits = content.split(term).length - 1;
    return score + Math.min(contentHits, 8);
  }, 0);
}

function snippet(content, terms) {
  if (!content) return "";
  const lower = content.toLowerCase();
  const positions = terms.map((term) => lower.indexOf(term)).filter((pos) => pos >= 0);
  const first = positions.length ? Math.min(...positions) : 0;
  const start = Math.max(0, first - 120);
  const end = Math.min(content.length, first + 220);
  const prefix = start > 0 ? "..." : "";
  const suffix = end < content.length ? "..." : "";
  return `${prefix}${content.slice(start, end)}${suffix}`;
}

function renderResult({ entry, snippet }) {
  const meta = [entry.type, entry.date].filter(Boolean).join(" - ");
  return `
    <li class="search-result">
      <h2><a href="${escapeAttribute(entry.url)}">${escapeHtml(entry.title)}</a></h2>
      ${meta ? `<p class="search-meta">${escapeHtml(meta)}</p>` : ""}
      ${snippet ? `<p>${escapeHtml(snippet)}</p>` : ""}
    </li>
  `;
}

function escapeHtml(value) {
  return String(value).replace(/[&<>"']/g, (char) => ({
    "&": "&amp;",
    "<": "&lt;",
    ">": "&gt;",
    '"': "&quot;",
    "'": "&#39;",
  }[char]));
}

function escapeAttribute(value) {
  return escapeHtml(value).replace(/`/g, "&#96;");
}
"""


def site_css() -> str:
    return """body {
  background: #fff;
  color: #202124;
  font-family: Georgia, "Times New Roman", serif;
  line-height: 1.55;
  margin: 0 auto;
  max-width: 800px;
  padding: 2rem 1rem 4rem;
}

a {
  color: #0b57d0;
}

table {
  border-collapse: collapse;
  margin: 1rem 0 1.5rem;
  width: 100%;
}

th,
td {
  border-bottom: 1px solid #dadce0;
  padding: 0.45rem 0.5rem;
  text-align: left;
  vertical-align: top;
}

blockquote {
  border-left: 4px solid #dadce0;
  color: #303134;
  margin: 1rem 0;
  padding: 0.25rem 0 0.25rem 1rem;
}

code,
pre,
.daily-content h3,
.daily-content h4 {
  font-family: "SFMono-Regular", Consolas, "Liberation Mono", monospace;
}

.breadcrumb,
.day-nav {
  color: #5f6368;
  font-size: 0.95rem;
  margin: 0 0 1rem;
}

.day-nav {
  border-bottom: 1px solid #dadce0;
  border-top: 1px solid #dadce0;
  display: grid;
  gap: 0.5rem;
  grid-template-columns: 1fr auto 1fr;
  padding: 0.65rem 0;
}

.day-nav a:last-child {
  text-align: right;
}

.calendar-grid {
  display: grid;
  gap: 1.25rem;
  grid-template-columns: repeat(auto-fit, minmax(230px, 1fr));
}

.month-card h2 {
  font-size: 1.15rem;
  margin-bottom: 0.4rem;
}

.calendar-month th,
.calendar-month td {
  border: 1px solid #dadce0;
  padding: 0;
  text-align: center;
}

.calendar-month th {
  font-size: 0.75rem;
  padding: 0.25rem;
}

.calendar-month a {
  color: inherit;
  display: block;
  min-height: 1.9rem;
  padding-top: 0.35rem;
  text-decoration: none;
}

.out-of-scope {
  background: #f8f9fa;
}

.hit-0 {
  background: #fff;
}

.hit-low {
  background: #d7e8ff;
}

.hit-medium {
  background: #7eb6f6;
}

.hit-high {
  background: #185abc;
  color: #fff;
}

.chron-section {
  border-left: 5px solid #dadce0;
  margin: 1.5rem 0;
  padding-left: 1rem;
}

.chron-section.contemporaneous {
  border-left-color: #1a73e8;
}

.chron-section.retrospective {
  border-left-color: #f29900;
}

.chron-section.unknown-date {
  border-left-color: #80868b;
}

.year-links {
  font-size: 1.1rem;
}

.site-search {
  border-bottom: 1px solid #dadce0;
  margin: 0 0 1.25rem;
  padding: 0 0 1rem;
}

.site-search label {
  color: #3c4043;
  display: block;
  font-size: 0.9rem;
  margin: 0 0 0.35rem;
}

.site-search-row {
  display: grid;
  gap: 0.5rem;
  grid-template-columns: 1fr auto;
}

.site-search input {
  border: 1px solid #bdc1c6;
  font: inherit;
  min-width: 0;
  padding: 0.5rem 0.6rem;
}

.site-search button {
  background: #174ea6;
  border: 1px solid #174ea6;
  color: #fff;
  cursor: pointer;
  font: inherit;
  padding: 0.5rem 0.8rem;
}

.search-count,
.search-empty,
.search-meta {
  color: #5f6368;
}

.search-list {
  padding-left: 1.4rem;
}

.search-result {
  margin: 0 0 1.25rem;
}

.search-result h2 {
  font-size: 1.15rem;
  margin: 0 0 0.2rem;
}

.search-result p {
  margin: 0.2rem 0;
}

.release-banner {
  background: #fff8e8;
  border: 1px solid #f0c36d;
  border-left: 5px solid #d97706;
  margin: 0 0 1.5rem;
  padding: 0.75rem 0.9rem;
}

.page-intro {
  color: #3c4043;
  margin: 0 0 1.25rem;
}

.page-intro p {
  margin: 0;
}
"""


def load_key_events(path: Path) -> dict[str, dict[str, EventInfo]]:
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
            current_values[value_match.group("field")] = strip_yaml_scalar(
                value_match.group("value")
            )
    flush()
    return events


def strip_yaml_scalar(raw: str) -> str:
    value = raw.strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in {'"', "'"}:
        return value[1:-1]
    return value


def read_parquet(path: Path) -> list[dict[str, Any]]:
    import pyarrow.parquet as parquet

    return parquet.read_table(path).to_pylist()


def iter_days(start_date: date, end_date: date) -> Iterable[date]:
    current = start_date
    while current <= end_date:
        yield current
        current += timedelta(days=1)


def iter_months(start_date: date, end_date: date) -> Iterable[date]:
    current = date(start_date.year, start_date.month, 1)
    end_month = date(end_date.year, end_date.month, 1)
    while current <= end_month:
        yield current
        _, days_in_month = calendar.monthrange(current.year, current.month)
        current += timedelta(days=days_in_month)


def density_class(hits: int) -> str:
    if hits == 0:
        return "hit-0"
    if hits <= 5:
        return "hit-low"
    if hits <= 20:
        return "hit-medium"
    return "hit-high"


def count_files(path: Path, pattern: str) -> int:
    return sum(1 for _ in path.glob(pattern))


def write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _human_date(value: date) -> str:
    return f"{MONTH_NAMES[value.month]} {value.day}, {value.year}"


def print_summary(
    year_data: list[tuple[YearConfig, dict[str, dict[str, EventInfo]], YearStats]],
    html_count: int,
) -> None:
    for _, events, stats in year_data:
        print(f"\n{stats.year}")
        print(f"  Total documents scanned: {stats.total_documents}")
        print(f"  Total date hits scoped to year: {stats.total_hits}")
        print(
            "  Distinct days with hits: "
            f"{stats.distinct_hit_days}/{stats.total_possible_days}"
        )
        print("  Top 10 days by hit count:")
        for day, hits in stats.top_days:
            event = events["days"].get(day)
            label = f" - {event.label}" if event and event.label else ""
            print(f"    {day}: {hits}{label}")
        print(f"  Total daily files generated: {stats.daily_files}")
        print(f"  Total monthly rollup files generated: {stats.monthly_files}")
    print(f"\nHTML files generated: {html_count}")


if __name__ == "__main__":
    main()
