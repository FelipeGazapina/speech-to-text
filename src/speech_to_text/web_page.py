"""The single page behind the app window (see webui.py). Plain HTML/CSS/JS, no external files."""

PAGE = r"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Speech to Text</title>
<style>
  :root {
    --bg: #ffffff; --panel: #f6f6f8; --fg: #1d1d1f; --muted: #6e6e73; --line: #e3e3e8;
    --accent: #0a6cff; --accent-fg: #ffffff; --danger: #d70015; --me: #0a6cff; --others: #8a3ffc;
    --selected: #e8f0ff; --badge: #eeeef2;
  }
  @media (prefers-color-scheme: dark) {
    :root {
      --bg: #1c1c1e; --panel: #232326; --fg: #f2f2f7; --muted: #98989f; --line: #38383c;
      --accent: #3d8bff; --danger: #ff453a; --me: #5ea0ff; --others: #b58cff;
      --selected: #1f3350; --badge: #2f2f33;
    }
  }
  * { box-sizing: border-box; }
  html, body { height: 100%; margin: 0; }
  body { font: 14px/1.45 -apple-system, BlinkMacSystemFont, "Helvetica Neue", sans-serif; background: var(--bg);
         color: var(--fg); display: flex; flex-direction: column; overflow: hidden; }
  button { font: inherit; cursor: pointer; border: 1px solid var(--line); background: var(--panel); color: var(--fg);
           border-radius: 7px; padding: 5px 12px; }
  button:hover { filter: brightness(0.97); }
  button.primary { background: var(--accent); border-color: var(--accent); color: var(--accent-fg); }
  button.danger { color: var(--danger); }
  button:disabled { opacity: .5; cursor: default; }
  input[type=search] { font: inherit; width: 100%; padding: 6px 10px; border: 1px solid var(--line); border-radius: 7px;
                       background: var(--bg); color: var(--fg); margin-bottom: 6px; }
  header { display: flex; align-items: center; gap: 18px; padding: 10px 18px; border-bottom: 1px solid var(--line); }
  header h1 { font-size: 15px; margin: 0; font-weight: 600; }
  nav { display: flex; gap: 4px; }
  nav button { border: none; background: none; padding: 6px 12px; color: var(--muted); }
  nav button.active { background: var(--panel); color: var(--fg); font-weight: 600; }
  .spacer { flex: 1; }
  #meeting-status { color: var(--muted); font-size: 13px; }
  #meeting-button.recording { background: var(--danger); border-color: var(--danger); color: #fff; }
  main { flex: 1; display: flex; min-height: 0; }
  .view { flex: 1; display: none; min-height: 0; }
  .view.active { display: flex; }
  aside { width: 300px; border-right: 1px solid var(--line); overflow-y: auto; background: var(--panel); }
  .meeting-row { padding: 10px 14px; border-bottom: 1px solid var(--line); cursor: pointer; }
  .meeting-row.selected { background: var(--selected); }
  .meeting-row .title { font-weight: 600; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  .meta { color: var(--muted); font-size: 12px; }
  .badge { display: inline-block; font-size: 11px; padding: 1px 7px; border-radius: 10px; background: var(--badge);
           color: var(--muted); margin-left: 4px; }
  .detail { flex: 1; overflow-y: auto; padding: 22px 28px 40px; }
  .detail h2 { margin: 0 0 4px; font-size: 22px; }
  .toolbar { display: flex; gap: 8px; flex-wrap: wrap; margin: 14px 0; align-items: center; }
  .subtabs { display: flex; gap: 4px; border-bottom: 1px solid var(--line); margin: 8px 0 16px; }
  .subtabs button { border: none; border-bottom: 2px solid transparent; border-radius: 0; background: none;
                    color: var(--muted); padding: 8px 12px; }
  .subtabs button.active { color: var(--fg); border-bottom-color: var(--accent); font-weight: 600; }
  .summary h2 { font-size: 17px; margin: 18px 0 6px; }
  .summary h3 { font-size: 15px; margin: 14px 0 4px; }
  .summary ul { margin: 4px 0; padding-left: 22px; }
  .summary li.task { list-style: none; margin-left: -20px; }
  .summary p { margin: 6px 0; }
  .notice { background: var(--panel); border: 1px solid var(--line); border-radius: 8px; padding: 10px 14px;
            color: var(--muted); margin: 12px 0; }
  .seg { display: grid; grid-template-columns: 58px 70px 1fr; gap: 10px; padding: 5px 0; }
  .seg .time { color: var(--muted); font-variant-numeric: tabular-nums; font-size: 12px; padding-top: 1px; }
  .seg .who { font-weight: 600; font-size: 13px; }
  .seg .who.me { color: var(--me); }
  .seg .who.others { color: var(--others); }
  .empty { margin: auto; text-align: center; color: var(--muted); max-width: 360px; }
  .dictations { flex: 1; overflow-y: auto; padding: 16px 28px 40px; }
  .dictation { border-bottom: 1px solid var(--line); padding: 10px 0; display: flex; gap: 12px; }
  .dictation .text { flex: 1; white-space: pre-wrap; overflow-wrap: anywhere; }
  .dictation .heard { color: var(--muted); font-size: 12px; margin-top: 3px; }
  mark { background: #ffe066; color: inherit; border-radius: 2px; }
</style>
</head>
<body>
<header>
  <h1>Speech to Text</h1>
  <nav>
    <button data-view="notetaker" class="active">Notetaker</button>
    <button data-view="dictations">Dictations</button>
  </nav>
  <div class="spacer"></div>
  <span id="meeting-status"></span>
  <button id="meeting-button" class="primary">● Start meeting notes</button>
</header>
<main>
  <section id="notetaker" class="view active">
    <aside id="meeting-list"></aside>
    <div class="detail" id="meeting-detail"></div>
  </section>
  <section id="dictations" class="view">
    <div class="dictations">
      <input type="search" id="dictation-search" placeholder="Search your dictations…">
      <div id="dictation-list"></div>
    </div>
  </section>
</main>
<script>
const token = new URLSearchParams(location.hash.slice(1)).get("token") || "";
const state = { meetings: [], selected: null, detail: null, subtab: "summary", lastStatuses: "" };

async function api(path, body) {
  const response = await fetch(path, {
    method: body === undefined ? "GET" : "POST",
    headers: { "X-Token": token, "Content-Type": "application/json" },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  const data = await response.json();
  if (!response.ok) throw new Error(data.error || response.statusText);
  return data;
}

function el(tag, attrs = {}, ...children) {
  const node = document.createElement(tag);
  for (const [key, value] of Object.entries(attrs)) {
    if (key === "class") node.className = value;
    else if (key.startsWith("on")) node.addEventListener(key.slice(2), value);
    else node.setAttribute(key, value);
  }
  for (const child of children.flat()) if (child != null) node.append(child);
  return node;
}

function duration(seconds) {
  if (!seconds) return "";
  const s = Math.round(seconds), h = Math.floor(s / 3600), m = Math.floor((s % 3600) / 60);
  return h ? `${h} h ${m} min` : m ? `${m} min` : `${s} s`;
}
function clock(seconds) {
  const s = Math.floor(seconds), h = Math.floor(s / 3600), m = Math.floor((s % 3600) / 60), r = s % 60;
  const pad = (n) => String(n).padStart(2, "0");
  return h ? `${h}:${pad(m)}:${pad(r)}` : `${pad(m)}:${pad(r)}`;
}
const STATUS = { recording: "recording", processing: "transcribing…", failed: "failed" };

// Minimal Markdown for the summaries: headings, bullet lists, task items, bold. Text is escaped first.
function renderMarkdown(markdown) {
  const box = el("div", { class: "summary" });
  let list = null;
  const inline = (text) => {
    const span = document.createElement("span");
    text.split(/(\*\*[^*]+\*\*)/).forEach((part) => {
      if (/^\*\*[^*]+\*\*$/.test(part)) span.append(el("strong", {}, part.slice(2, -2)));
      else span.append(part);
    });
    return span;
  };
  for (const raw of (markdown || "").split("\n")) {
    const line = raw.trimEnd();
    const bullet = line.match(/^\s*[-*]\s+(\[[ xX]\]\s+)?(.*)$/);
    if (bullet) {
      if (!list) { list = el("ul"); box.append(list); }
      const item = el("li", bullet[1] ? { class: "task" } : {});
      if (bullet[1]) item.append(bullet[1].includes(" ]") ? "☐ " : "☑ ");
      item.append(inline(bullet[2]));
      list.append(item);
      continue;
    }
    list = null;
    const heading = line.match(/^(#{1,3})\s+(.*)$/);
    if (heading) box.append(el(heading[1].length === 3 ? "h3" : "h2", {}, inline(heading[2])));
    else if (line.trim()) box.append(el("p", {}, inline(line)));
  }
  return box;
}

async function loadMeetings() {
  state.meetings = await api("/api/meetings");
  const statuses = state.meetings.map((m) => `${m.id}:${m.status}:${m.has_summary}:${m.title}`).join("|");
  const changed = statuses !== state.lastStatuses;
  state.lastStatuses = statuses;
  renderMeetingList();
  if (state.selected == null && state.meetings.length) selectMeeting(state.meetings[0].id);
  else if (changed && state.selected != null) selectMeeting(state.selected, true);
  else if (!state.meetings.length) renderDetail();
}

function renderMeetingList() {
  const list = document.getElementById("meeting-list");
  list.replaceChildren(...state.meetings.map((m) =>
    el("div", { class: "meeting-row" + (m.id === state.selected ? " selected" : ""), onclick: () => selectMeeting(m.id) },
      el("div", { class: "title" }, m.title),
      el("div", { class: "meta" }, m.started_at.slice(0, 16).replace("T", " "), " · ", duration(m.duration_seconds),
        STATUS[m.status] ? el("span", { class: "badge" }, STATUS[m.status]) : null))));
}

async function selectMeeting(id, keepTab) {
  state.selected = id;
  if (!keepTab) state.subtab = "summary";
  renderMeetingList();
  try { state.detail = await api(`/api/meetings/${id}`); } catch { state.detail = null; }
  renderDetail();
}

function renderDetail() {
  const box = document.getElementById("meeting-detail");
  const m = state.detail;
  if (!m) {
    box.replaceChildren(el("div", { class: "empty" },
      el("h2", {}, "No meetings yet"),
      el("p", {}, "Click “Start meeting notes” when a call begins. Your voice and the other people’s " +
        "(from the computer’s audio) are recorded, then transcribed and summarized on this Mac when you stop.")));
    return;
  }
  const labels = m.speaker_labels || { me: "You", others: "Others" };
  const notices = [];
  if (m.status === "recording") notices.push("Recording… the transcript and summary appear here when you stop.");
  if (m.status === "processing") notices.push("Transcribing… this takes a few minutes for a long meeting.");
  if (m.status === "failed") notices.push(m.error || "Something went wrong.");
  if (!m.system_audio) notices.push("Only your microphone was recorded: allow Screen & System Audio Recording " +
    "for Speech to Text (System Settings → Privacy & Security) to include the other people.");
  if (m.status === "done" && m.error) notices.push(m.error);

  const summaryText = m.summary ? `# ${m.title}\n\n${m.summary}` : "";
  const body = state.subtab === "summary"
    ? (m.summary ? renderMarkdown(m.summary) : el("p", { class: "meta" }, m.status === "done" ? "No summary yet." : ""))
    : renderTranscript(m, labels);

  box.replaceChildren(
    el("h2", {}, m.title),
    el("div", { class: "meta" }, m.started_at.slice(0, 16), " · ", duration(m.duration_seconds),
      m.language ? ` · ${m.language.toUpperCase()}` : ""),
    ...notices.map((text) => el("div", { class: "notice" }, text)),
    el("div", { class: "toolbar" },
      (state.subtab === "summary" ? summaryText : m.transcript_text)
        ? el("button", { onclick: () => copy(state.subtab === "summary" ? summaryText : m.transcript_text) },
            state.subtab === "summary" ? "Copy summary" : "Copy transcript")
        : null,
      m.segments.length ? el("button", { onclick: () => summarize(m.id) }, m.summary ? "Regenerate summary" : "Generate summary") : null,
      m.status === "failed" ? el("button", { onclick: () => summarize(m.id) }, "Try again") : null,
      el("button", { onclick: () => rename(m) }, "Rename"),
      el("div", { class: "spacer" }),
      m.status !== "recording" ? el("button", { class: "danger", onclick: () => remove(m) }, "Delete") : null),
    el("div", { class: "subtabs" },
      ["summary", "transcript"].map((tab) => el("button", {
        class: state.subtab === tab ? "active" : "",
        onclick: () => { state.subtab = tab; renderDetail(); },
      }, tab === "summary" ? "Summary" : `Transcript (${m.segments.length})`))),
    body);
}

function renderTranscript(m, labels) {
  if (!m.segments.length) return el("p", { class: "meta" }, m.status === "done" ? "Nobody spoke, apparently." : "");
  return el("div", {}, m.segments.map((s) => el("div", { class: "seg" },
    el("span", { class: "time" }, clock(s.start)),
    el("span", { class: `who ${s.speaker}` }, labels[s.speaker] || s.speaker),
    el("span", {}, s.text))));
}

async function copy(text) {
  await api("/api/copy", { text });
  flash("Copied");
}
async function summarize(id) {
  await api(`/api/meetings/${id}/summarize`, {});
  flash("Working on it… the result appears here when ready");
}
async function rename(m) {
  const title = prompt("Meeting title", m.title);
  if (title && title.trim()) { await api(`/api/meetings/${m.id}/rename`, { title: title.trim() }); loadMeetings(); }
}
async function remove(m) {
  if (!confirm(`Delete “${m.title}”? Its transcript and summary are removed for good.`)) return;
  await api(`/api/meetings/${m.id}/delete`, {});
  state.selected = null; state.detail = null;
  loadMeetings();
}
function flash(text) {
  const status = document.getElementById("meeting-status");
  status.textContent = text;
  setTimeout(() => { if (status.textContent === text) status.textContent = ""; }, 2500);
}

async function pollState() {
  try {
    const s = await api("/api/state");
    const button = document.getElementById("meeting-button");
    button.classList.toggle("recording", s.meeting_recording);
    button.textContent = s.meeting_recording ? `■ Stop meeting notes (${clock(s.meeting_elapsed)})` : "● Start meeting notes";
    button.disabled = !s.model_ready && !s.meeting_recording;
    const status = document.getElementById("meeting-status");
    if (s.meeting_progress) status.textContent = s.meeting_progress;
    else if (!s.model_ready) status.textContent = "Loading the speech model…";
    else if (status.textContent.startsWith("Transcribing") || status.textContent.startsWith("Summarizing")) status.textContent = "";
    if (s.meetings_version !== state.meetingsVersion) { state.meetingsVersion = s.meetings_version; loadMeetings(); }
  } catch (error) { /* the app is restarting */ }
}

document.getElementById("meeting-button").addEventListener("click", async (event) => {
  const recording = event.target.classList.contains("recording");
  try {
    const result = await api(recording ? "/api/meetings/stop" : "/api/meetings/start", {});
    if (result.meeting_id) { state.selected = result.meeting_id; }
    if (result.message) flash(result.message);
  } catch (error) { alert(error.message); }
  pollState();
});

for (const button of document.querySelectorAll("nav button")) {
  button.addEventListener("click", () => {
    document.querySelectorAll("nav button").forEach((b) => b.classList.toggle("active", b === button));
    document.querySelectorAll(".view").forEach((v) => v.classList.toggle("active", v.id === button.dataset.view));
    if (button.dataset.view === "dictations") loadDictations();
  });
}

let searchTimer;
document.getElementById("dictation-search").addEventListener("input", () => {
  clearTimeout(searchTimer);
  searchTimer = setTimeout(loadDictations, 200);
});
async function loadDictations() {
  const query = document.getElementById("dictation-search").value.trim();
  const items = await api(`/api/dictations?limit=300${query ? "&q=" + encodeURIComponent(query) : ""}`);
  const list = document.getElementById("dictation-list");
  if (!items.length) { list.replaceChildren(el("p", { class: "meta" }, query ? "Nothing matches." : "No dictations yet.")); return; }
  list.replaceChildren(...items.map((d) => el("div", { class: "dictation" },
    el("div", { class: "text" }, d.text,
      el("div", { class: "meta" }, d.created_at, d.language ? ` · ${d.language.toUpperCase()}` : "",
        d.app_name ? ` · ${d.app_name}` : "", d.status && d.status !== "pasted" ? el("span", { class: "badge" }, d.status.replace("_", " ")) : null),
      d.raw_text && d.raw_text !== d.text ? el("div", { class: "heard" }, "heard: ", d.raw_text) : null),
    el("div", {}, el("button", { onclick: () => copy(d.text) }, "Copy")))));
}

const hashParams = new URLSearchParams(location.hash.slice(1));
if (hashParams.get("meeting")) state.selected = Number(hashParams.get("meeting"));
if (hashParams.get("view") === "dictations") document.querySelector('nav button[data-view="dictations"]').click();
pollState();
setInterval(pollState, 1000);
</script>
</body>
</html>
"""
