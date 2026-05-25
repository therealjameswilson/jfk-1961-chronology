const form = document.getElementById("site-search-form");
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
