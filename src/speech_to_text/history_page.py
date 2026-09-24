"""Render the whole history as a local, searchable web page (menu bar -> Show all history…)."""

from __future__ import annotations

import html
from pathlib import Path

from .history import HistoryStore

_STATUS_TEXT = {
    "pasted": "pasted",
    "saved": "saved",
    "paste_failed": "⚠️ paste failed",
    "filtered": "🔇 filtered",
    "failed": "❌ failed — retry from the menu",
    "recovered": "♻️ recovered",
}

_PAGE = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Dictation history</title>
<style>
  :root {{ --bg: #fff; --fg: #1d1d1f; --muted: #6e6e73; --line: #e5e5ea; --accent: #0a84ff; --card: #f5f5f7; }}
  @media (prefers-color-scheme: dark) {{
    :root {{ --bg: #1c1c1e; --fg: #f5f5f7; --muted: #98989d; --line: #38383a; --card: #2c2c2e; }}
  }}
  body {{ margin: 0; font: 15px/1.45 -apple-system, BlinkMacSystemFont, sans-serif; background: var(--bg); color: var(--fg); }}
  header {{ position: sticky; top: 0; background: var(--bg); border-bottom: 1px solid var(--line); padding: 16px 24px; }}
  h1 {{ margin: 0 0 4px; font-size: 20px; }}
  .meta {{ color: var(--muted); font-size: 13px; }}
  input {{ width: 100%; max-width: 560px; box-sizing: border-box; margin-top: 12px; padding: 8px 12px;
           font: inherit; border: 1px solid var(--line); border-radius: 8px; background: var(--card); color: var(--fg); }}
  main {{ padding: 8px 24px 40px; max-width: 900px; }}
  article {{ padding: 12px 0; border-bottom: 1px solid var(--line); }}
  .row {{ display: flex; gap: 12px; align-items: baseline; color: var(--muted); font-size: 12px; flex-wrap: wrap; }}
  .text {{ margin: 4px 0 0; white-space: pre-wrap; overflow-wrap: anywhere; }}
  .heard {{ color: var(--muted); font-size: 13px; margin-top: 4px; white-space: pre-wrap; }}
  button {{ margin-left: auto; font: inherit; font-size: 12px; border: 1px solid var(--line); background: var(--card);
            color: var(--fg); border-radius: 6px; padding: 2px 10px; cursor: pointer; }}
  .lang {{ font-weight: 600; text-transform: uppercase; }}
</style>
</head>
<body>
<header>
  <h1>Dictation history</h1>
  <div class="meta">{count} dictations · newest first · stored only on this Mac</div>
  <input id="q" type="search" placeholder="Search…" autofocus>
</header>
<main id="list">
{rows}
</main>
<script>
  const q = document.getElementById("q");
  q.addEventListener("input", () => {{
    const needle = q.value.toLowerCase();
    for (const el of document.querySelectorAll("article")) {{
      el.hidden = needle && !el.textContent.toLowerCase().includes(needle);
    }}
  }});
  document.getElementById("list").addEventListener("click", async (event) => {{
    if (event.target.tagName !== "BUTTON") return;
    const text = event.target.closest("article").querySelector(".text").textContent;
    try {{ await navigator.clipboard.writeText(text); }}
    catch {{
      const area = Object.assign(document.createElement("textarea"), {{ value: text }});
      document.body.append(area); area.select(); document.execCommand("copy"); area.remove();
    }}
    event.target.textContent = "Copied";
    setTimeout(() => (event.target.textContent = "Copy"), 1200);
  }});
</script>
</body>
</html>
"""


def render_history_page(store: HistoryStore, limit: int = 100_000) -> str:
    rows = []
    for item in store.recent(limit):
        text = item.best_text or "(no text: transcription failed, audio kept for retry)"
        heard = ""
        if item.raw_text and item.raw_text.strip() != text.strip():
            heard = f'<div class="heard">heard: {html.escape(item.raw_text)}</div>'
        app = f"<span>→ {html.escape(item.app_name)}</span>" if item.app_name else ""
        corrected = "<span>✏️ corrected</span>" if item.corrected_text else ""
        rows.append(
            "<article>"
            f'<div class="row"><span>#{item.id}</span><span>{html.escape(item.created_at)}</span>'
            f'<span class="lang">{html.escape(item.language or "")}</span>{app}'
            f"<span>{_STATUS_TEXT.get(item.status, item.status)}</span>{corrected}<button>Copy</button></div>"
            f'<p class="text">{html.escape(text)}</p>{heard}'
            "</article>"
        )
    return _PAGE.format(count=len(rows), rows="\n".join(rows) or "<p>Nothing yet.</p>")


def write_history_page(store: HistoryStore) -> Path:
    path = store.path.parent / "history.html"
    path.write_text(render_history_page(store), encoding="utf-8")
    return path
