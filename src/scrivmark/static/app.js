"use strict";

const S = { st: null, key: null, editing: null, active: null, cur: -1, selPosted: false, compose: {}, popDraft: null, edits: {} };
const $ = (s, r = document) => r.querySelector(s);
const $$ = (s, r = document) => Array.from(r.querySelectorAll(s));
const doc = $("#doc"), margin = $("#margin"), tool = $("#tool"), pop = $("#pop");
const GENERATED = "span.citation, a.scrv-xref, .scrv-eqno, math, .scrv-unresolved, a.footnote-ref";
const HISTORICAL = ".scrv-del, .scrv-old, .edit";
const PLACEHOLDER = "￼";

history.replaceState(null, "", "/");

// Shown after a short delay, so quick requests don't flash it, and hidden after
// another, so a save followed by the page refresh reads as one piece of work.
const busy = { n: 0, label: "", show: null, hide: null };

function working(label) {
  busy.n++;
  busy.label = label;
  clearTimeout(busy.hide);
  const box = $("#busy");
  if (!box.hidden) box.querySelector(".busylabel").textContent = label;
  else if (!busy.show) busy.show = setTimeout(() => {
    busy.show = null;
    if (!busy.n) return;
    box.querySelector(".busylabel").textContent = busy.label;
    box.hidden = false;
  }, 250);
  return () => {
    if (--busy.n) return;
    clearTimeout(busy.show);
    busy.show = null;
    busy.hide = setTimeout(() => { if (!busy.n) $("#busy").hidden = true; }, 300);
  };
}

async function api(path, body, label) {
  const opts = body === undefined ? {} : {
    method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify(body),
  };
  const end = working(label || (body === undefined ? "Updating the page…" : "Saving…"));
  try {
    const r = await fetch(path, opts);
    const j = await r.json().catch(() => ({ error: `HTTP ${r.status}` }));
    if (!r.ok) throw Object.assign(new Error(j.error || "Something went wrong"), j);
    return j;
  } finally { end(); }
}

function el(tag, attrs = {}, ...kids) {
  const e = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (k === "class") e.className = v;
    else if (k.startsWith("on")) e.addEventListener(k.slice(2), v);
    else if (v !== null && v !== undefined) e.setAttribute(k, v);
  }
  for (const k of kids.flat()) if (k !== null && k !== undefined) e.append(k.nodeType ? k : String(k));
  return e;
}

function notice(msg, ms, action) {
  const n = $("#notice");
  n.replaceChildren(msg || "");
  if (action) n.append(" ", el("button", { onclick: () => { n.hidden = true; action.run(); } }, action.label));
  n.hidden = !msg;
  clearTimeout(notice.t);
  if (ms) notice.t = setTimeout(() => (n.hidden = true), ms);
}

// Unsent work (replies, comment edits, the selection popup, source edits) is
// kept in this browser per round, so a reload, a refresh or a stale selection
// does not lose it.
let workTimer = null;
const touched = new Set();

function workKey() { return S.st && S.st.round ? `scrivmark-work:${S.st.round.id}` : null; }

// Several tabs may be open on one round, so only the entries this tab has
// touched are written or removed; another tab's unsent work is left alone.
function saveWork(now = false, ...keys) {
  keys.forEach(k => touched.add(k));
  clearTimeout(workTimer);
  const write = () => {
    const k = workKey();
    if (!k || !touched.size) return;
    let data;
    try { data = JSON.parse(localStorage.getItem(k) || "null") || {}; } catch { data = {}; }
    data.compose = data.compose || {};
    data.edits = data.edits || {};
    for (const t of touched) {
      const [kind, ...rest] = t.split("|");
      const id = rest.join("|");
      if (kind === "compose") { if (S.compose[id]) data.compose[id] = S.compose[id]; else delete data.compose[id]; }
      if (kind === "edit") { if (S.edits[id]) data.edits[id] = S.edits[id]; else delete data.edits[id]; }
      if (kind === "pop") data.popDraft = S.popDraft;
    }
    touched.clear();
    const live = new Set([...S.st.threads, ...S.st.drafts, ...S.st.asks].map(x => x.id));
    for (const id of Object.keys(data.compose)) if (!live.has(id.split(":")[1])) delete data.compose[id];
    try {
      if (!Object.keys(data.compose).length && !data.popDraft && !Object.keys(data.edits).length) localStorage.removeItem(k);
      else localStorage.setItem(k, JSON.stringify(data));
    } catch { /* storage may be blocked; the work stays in this page */ }
  };
  if (now) write(); else workTimer = setTimeout(write, 300);
}

function restoreWork() {
  const k = workKey();
  if (!k) return;
  let data = null;
  try { data = JSON.parse(localStorage.getItem(k) || "null"); } catch { data = null; }
  if (!data) return;
  Object.assign(S.compose, data.compose || {});
  S.popDraft = data.popDraft || null;
  S.edits = data.edits || {};
  const left = [];
  if (S.popDraft) left.push(`an unsent ${S.popDraft.act === "ask" ? "question" : "comment"} on “${(S.popDraft.quote || "").slice(0, 40)}”`);
  const edits = Object.values(S.edits);
  if (edits.length) left.push(`${edits.length === 1 ? "an unsaved edit" : `${edits.length} unsaved edits`} (${edits.map(x => `${x.file} line ${x.first}`).join(", ")})`);
  if (left.length) {
    setTimeout(() => notice(`Kept from before: ${left.join(" and ")}. Select the passage again to finish a comment; open the passage with ✎ to restore an edit.`, 0,
      { label: "Discard them", run: () => { const slots = Object.keys(S.edits); S.popDraft = null; S.edits = {};
        saveWork(true, "pop|", ...slots.map(x => `edit|${x}`)); } }), 0);
  }
}

function fail(e) {
  notice(e.stale ? "The review changed while you were working; it has refreshed. Please try again." : e.message, 10000);
  if (e.stale) load(true);
}

// ------------------------------------------------------------------ loading

async function load(force = false) {
  let st;
  try { st = await api("/api/state"); } catch (e) {
    notice(e.message);
    if (!S.st) doc.replaceChildren(el("p", { class: "loading" }, `The manuscript could not be loaded: ${e.message}`));
    return;
  }
  if (!force && st.key === S.key) return;
  if (S.editing) { notice("The manuscript changed elsewhere; the page will refresh when you finish editing."); return; }
  const keep = scrollAnchor();
  const round = r => (r && r.round ? r.round.id : null);
  const fresh = !S.st || round(S.st) !== round(st);
  if (fresh && S.st) { saveWork(true); S.compose = {}; S.popDraft = null; S.edits = {}; touched.clear(); }
  S.st = st; S.key = st.key;
  if (fresh) restoreWork();
  draw();
  restoreScroll(keep);
}

function scrollAnchor() {
  const b = $$(".scrv-b", doc).find(x => x.getBoundingClientRect().bottom > 60);
  return b ? { file: b.dataset.file, lines: b.dataset.lines, top: b.getBoundingClientRect().top, y: scrollY } : { y: scrollY };
}

function restoreScroll(a) {
  const b = a.file && $$(".scrv-b", doc).find(x => x.dataset.file === a.file && x.dataset.lines === a.lines);
  if (b) scrollBy(0, b.getBoundingClientRect().top - a.top);
  else scrollTo(0, a.y);
}

// ------------------------------------------------------------------ drawing

const MODE = { reviewer: "Reviewing", author: "Responding", read: "Reading", closed: "Closed" };

function pending() { return S.st.changes.filter(c => c.state === "pending"); }

function draw() {
  const st = S.st;
  document.body.className = st.mode;
  $("#mode").textContent = MODE[st.mode];
  $("#mode").className = `badge ${st.mode}`;
  const r = st.round;
  const who = st.me ? ` · you are ${st.me}` : "";
  $("#roundid").textContent = r ? `${r.id} · ${st.mode === "author" ? "from " + r.reviewer.name : "reviewing " + r.author_branch}${who}` : st.branch;
  const stmt = $("#statement");
  stmt.hidden = !(r && st.mode !== "reviewer");
  if (r) stmt.textContent = r.statement;
  doc.innerHTML = st.html;
  if (st.mode === "reviewer" && st.drafts.length) doc.append(keepAllBar(st.drafts));
  decorate();
  drawOther();
  drawCounts();
  if (st.mode === "read") notice("Reading only. Start a review with `scrivmark start`, or open one with `scrivmark open <round>`.");
  else if (st.warnings.length) notice("pandoc: " + st.warnings.join(" "), 10000);
  requestAnimationFrame(() => { buildMargin(); if (Q.open) { drawQueue(); queueStatus(); } });
}

function drawCounts() {
  const st = S.st, open = st.threads.filter(t => t.status === "open").length;
  let txt = `${open} open comment${open === 1 ? "" : "s"}`;
  if (st.mode === "reviewer" && st.drafts.length) txt += ` · ${st.drafts.length} draft${st.drafts.length === 1 ? "" : "s"}`;
  const asks = st.asks.length;
  if (asks) txt += ` · ${asks} ask${asks === 1 ? "" : "s"}`;
  if (st.mode === "author") {
    const p = pending().length;
    txt = `${p} change${p === 1 ? "" : "s"} to decide · ` + txt;
  }
  $("#counts").textContent = st.round ? txt : "";
  const hint = $("#hint");
  let h = "";
  if (st.mode === "reviewer") h = st.drafts.length
    ? "Keep or drop each yellow draft before returning the review."
    : "When you have finished, run `scrivmark return` to hand the review back.";
  if (st.mode === "author" && !pending().length && !open) h = "Everything is decided. Run `scrivmark close` to merge the review.";
  hint.textContent = h;
  hint.hidden = !h;
}

function decisionButtons(c) {
  return [
    el("button", { class: "primary", onclick: () => decide(c.id, "accept") }, "Accept"),
    el("button", { onclick: () => decide(c.id, "reject") }, "Reject")];
}

function drawOther() {
  const box = $("#files"), st = S.st;
  const files = st.changes.filter(c => c.type === "file");
  const placed = new Set($$("[data-c]", doc).flatMap(x => x.dataset.c.split(" ")));
  const unplaced = st.changes.filter(c => c.type !== "file" && c.state === "pending" && !placed.has(c.id));
  const rows = [
    ...unplaced.map(c => el("div", { class: "row other", "data-q": c.id, tabindex: "-1" },
      el("div", { class: "what" }, el("div", { class: "muted small" }, `${c.id} · ${c.file} · not shown in the running text`),
        el("pre", { class: "diff-old" }, c.base || "(nothing)"), el("pre", { class: "diff-new" }, c.reviewer || "(removed)")),
      st.mode === "author" ? el("div", { class: "acts" }, ...decisionButtons(c)) : null)),
    ...files.map(c => el("div", { class: "row", "data-q": c.state === "pending" ? c.id : null, tabindex: "-1" },
      el("div", { class: "what" }, `${c.id} · ${c.reviewer} — ${c.state}`,
        el("div", { class: "muted small" }, `To inspect: git diff ${st.round.base.slice(0, 8)} ${(st.round.returned || "").slice(0, 8)} -- ${c.file}`)),
      c.state === "pending" && st.mode === "author" ? el("div", { class: "acts" }, ...decisionButtons(c)) : null)),
  ];
  box.hidden = !rows.length;
  box.replaceChildren(el("div", { class: "muted" }, "Changes outside the running text:"), ...rows);
}

function editable() { return S.st.mode === "reviewer" || S.st.mode === "author"; }

function decorate() {
  for (const img of $$("img", doc)) {
    img.classList.add("zoomable");
    img.title = "Click to zoom";
    img.addEventListener("click", e => { e.stopPropagation(); openZoom(img); });
  }
  for (const a of $$('a[href^="#ref-"]', doc)) {
    const ref = document.getElementById(a.getAttribute("href").slice(1));
    if (ref) a.title = ref.textContent.trim().replace(/\s+/g, " ");
  }
  if (editable()) {
    for (const b of $$(".scrv-b", doc)) {
      const ref = b.classList.contains("scrv-ref");
      b.prepend(el("button", { class: "edit", title: ref ? "Edit this reference's entry in the bibliography file" : "Edit this passage's source",
        "aria-label": ref ? "Edit this reference" : "Edit this passage",
        onclick: e => { e.stopPropagation(); startEdit(b); } }, "✎"));
    }
  }
  if (S.st.mode === "author") {
    const ids = new Set(pending().map(c => c.id));
    for (const x of $$("[data-c]", doc)) {
      if (!x.dataset.c.split(" ").some(id => ids.has(id))) continue;
      x.tabIndex = 0;
      x.setAttribute("role", "button");
      x.setAttribute("aria-label", "Tracked change: press Enter to accept or reject");
    }
  }
  for (const t of [...S.st.threads, ...S.st.drafts]) if (t.line) highlight(t);
  for (const a of S.st.asks) if (a.line) highlight(a, "ask");
}

function findBlock(file, line) {
  return $$(".scrv-b", doc).find(b => {
    if (b.dataset.file !== file) return false;
    const [a, z] = b.dataset.lines.split("-").map(Number);
    return a <= line && line <= z;
  });
}

function plainQuote(s) {
  return s.replace(/\[[^\]]*@[^\]]*\]/g, " ").replace(/!?\[([^\]]*)\]\([^)]*\)/g, "$1")
    .replace(/[*_`#]/g, "").replace(/\s+/g, " ").trim();
}

function highlight(t, kind = "") {
  const block = findBlock(t.anchor.file, t.line[0]);
  if (!block) return;
  const walker = document.createTreeWalker(block, NodeFilter.SHOW_TEXT, {
    acceptNode: n => n.parentElement.closest(HISTORICAL) ? NodeFilter.FILTER_REJECT : NodeFilter.FILTER_ACCEPT,
  });
  let text = "", n, opaqueAt = null;
  const map = [];
  const TYPO = { "\u2018": "'", "\u2019": "'", "\u201c": '"', "\u201d": '"', "\u2013": "-", "\u2014": "-", "\u00a0": " ", "\u2026": "..." };
  while ((n = walker.nextNode())) {
    const opaque = n.parentElement.closest("math, .citation");
    if (opaque) {
      if (opaque !== opaqueAt) { text += "\u0000"; map.push([n, 0]); opaqueAt = opaque; }
      continue;
    }
    opaqueAt = null;
    for (let i = 0; i < n.data.length; i++) {
      const raw = n.data[i];
      const out = /\s/.test(raw) ? " " : (TYPO[raw] ?? raw);
      for (const ch of out) {
        if (ch === " " && text.endsWith(" ")) continue;
        if (ch === "-" && text.endsWith("-") && raw === n.data[i - 1]) continue;
        text += ch;
        map.push([n, i]);
      }
    }
  }
  const hl = t.hl;
  const occurrences = needle => { const at = []; for (let i = text.indexOf(needle); i >= 0; i = text.indexOf(needle, i + 1)) at.push(i); return at; };
  const found = hl && hl.text ? occurrences(hl.text) : [];
  const at = found.length && found.length === hl.count ? found[hl.nth] : -1;
  const needle = hl ? hl.text : "";
  if (at === undefined || at < 0) { block.classList.add("block-hl"); t._mark = block; return; }
  const byNode = new Map();
  for (let k = at; k < at + needle.length; k++) {
    const [node, off] = map[k];
    const r = byNode.get(node) || [off, off];
    r[1] = off;
    byNode.set(node, r);
  }
  let first = null;
  for (const [node, [s, e]] of byNode) {
    const range = document.createRange();
    range.setStart(node, s);
    range.setEnd(node, e + 1);
    const m = el("mark", { class: `scrv-hl${t.status === "resolved" ? " resolved" : ""}${kind ? " " + kind : ""}`, "data-t": t.id });
    try { range.surroundContents(m); } catch { continue; }
    m.addEventListener("click", ev => { ev.stopPropagation(); setActive(t.id); });
    first = first || m;
  }
  t._mark = first || block;
}

// ------------------------------------------------------------------ margin

function buildMargin() {
  const st = S.st;
  saveWork();
  const active = document.activeElement;
  const owned = active && active.matches && active.matches("textarea[data-key]") && margin.contains(active)
    ? { key: active.dataset.key, a: active.selectionStart, z: active.selectionEnd, dir: active.selectionDirection, top: active.scrollTop }
    : null;
  margin.replaceChildren();
  const items = [
    ...st.drafts.map(d => ({ ...d, kind: "draft" })),
    ...st.asks.map(a => ({ ...a, kind: "ask" })),
    ...st.threads.map(t => ({ ...t, kind: "thread" })),
  ];
  const card = { draft: draftCard, ask: askCard, thread: threadCard };
  const reopen = { draft: editDraft, ask: askComment, thread: replyBox };
  for (const it of items) {
    const c = card[it.kind](it);
    c.dataset.t = it.id;
    c.addEventListener("click", () => setActive(it.id, false));
    margin.append(c);
    if (S.compose[`${it.kind}:${it.id}`]) reopen[it.kind](c, it, true);
    if (it.kind === "thread") for (const m of it.messages) if (S.compose[`msg:${it.id}:${m.id}`]) editMessage(c, it, m, true);
  }
  if (owned) {
    const ta = $(`textarea[data-key="${owned.key}"]`, margin);
    if (ta) {
      ta.focus({ preventScroll: true });
      ta.setSelectionRange(owned.a, owned.z, owned.dir);
      ta.scrollTop = owned.top;
    }
  }
  layoutMargin();
}

function layoutMargin() {
  const cards = $$(".card", margin);
  if (!cards.length || getComputedStyle(cards[0]).position !== "absolute") return;
  const top0 = margin.getBoundingClientRect().top;
  const all = [...S.st.threads, ...S.st.drafts, ...S.st.asks];
  const placed = cards.map(c => {
    const it = all.find(x => x.id === c.dataset.t);
    return { c, want: it && it._mark ? it._mark.getBoundingClientRect().top - top0 : -1 };
  }).sort((a, b) => a.want - b.want);
  let y = 0;
  for (const x of placed) {
    const top = Math.max(x.want, y);
    x.c.style.top = `${top}px`;
    y = top + x.c.offsetHeight + 8;
  }
  margin.style.minHeight = `${y}px`;
}

function quoteLine(it) {
  const gone = plainQuote(it.anchor.removed || (it.current === "" ? it.anchor.exact : ""));
  if (gone) return el("div", { class: "quote", title: gone }, `“${gone}” (removed)`);
  const q = plainQuote(it.current ?? it.anchor.exact);
  if (!q) return el("div", { class: "quote" }, "Where text was removed");
  return el("div", { class: "quote", title: q }, `“${q}”`);
}

function threadCard(t) {
  const orphan = !t.line;
  const c = el("div", { class: `card ${t.status}${orphan ? " orphan" : ""}`, tabindex: "-1" },
    orphan ? el("div", { class: "tag" }, lostWhy(t)) : null,
    quoteLine(t),
    ...t.messages.map(m => el("div", { class: "msg", "data-m": m.id },
      (t.editable || []).includes(m.id)
        ? el("button", { class: "msgedit", title: "Edit your comment (until the other side has seen it)",
            onclick: e => { e.stopPropagation(); editMessage(c, t, m); } }, "Edit")
        : null,
      el("span", { class: "by" }, m.by), m.body,
      m.edited_at ? el("span", { class: "muted small" }, " (edited)") : null)));
  if (!S.st.round || S.st.mode === "closed") return c;
  c.append(el("div", { class: "acts" },
    orphan ? el("button", { class: "primary", title: "Select the passage in the text first",
      onmousedown: e => e.preventDefault(), onclick: e => { e.stopPropagation(); reattach(t.id); } }, "Attach to selection") : null,
    el("button", { onclick: e => { e.stopPropagation(); replyBox(c, t); } }, "Reply"),
    t.status === "open"
      ? el("button", { onclick: e => { e.stopPropagation(); setThread(t.id, "resolved"); } }, "Resolve")
      : el("button", { onclick: e => { e.stopPropagation(); setThread(t.id, "open"); } }, "Reopen")));
  return c;
}

function lostWhy(it) {
  return it.where && it.where.why === "ambiguous"
    ? "Lost its place: its passage appeared twice and one copy was removed, so which one this was about can't be told"
    : "Lost its place: its passage was rewritten past recognition";
}

function draftCard(d) {
  const shown = S.st.rev;
  const c = el("div", { class: "card draft", tabindex: "-1" },
    el("div", { class: "tag" }, d.by ? `Draft from ${d.by}` : "Draft from your agent"),
    d.line ? null : el("div", { class: "tag" }, lostWhy(d) + (d.wording !== null
      ? ". Its edit can't be applied: make the change in the text yourself, then drop this draft." : "")),
    quoteLine(d),
    d.body ? el("div", { class: "msg" }, d.body) : el("div", { class: "muted small" }, "Line edit, no comment"),
    d.wording !== null ? el("div", { class: "wording", title: "Applied as an edit if kept" }, `→ ${d.wording || "(delete the passage)"}`) : null);
  c.append(el("div", { class: "acts" },
    el("button", { class: "primary", onclick: e => { e.stopPropagation(); triage(d.id, "keep", undefined, undefined, shown); } }, d.wording !== null ? "Keep & apply" : "Keep"),
    el("button", { onclick: e => { e.stopPropagation(); editDraft(c, d); } }, "Edit"),
    el("button", { onclick: e => { e.stopPropagation(); triage(d.id, "drop", undefined, undefined, shown); } }, "Drop")));
  return c;
}

function askCard(a) {
  const shown = S.st.rev;
  const c = el("div", { class: "card ask", tabindex: "-1" },
    el("div", { class: "tag" }, a.answer ? "Your agent answered" : "Question for your agent"),
    quoteLine(a),
    el("div", { class: "msg" }, el("span", { class: "by" }, "You"), a.question),
    a.answer ? el("div", { class: "msg answer" }, el("span", { class: "by" }, a.answered_by || "Agent"), a.answer)
      : el("div", { class: "muted small" }, "Not answered yet. In the chat, ask your agent to “check my asks”."),
    a.answer && a.wording != null ? el("div", { class: "wording", title: a.applied ? "Applied as an edit" : "Proposed wording" },
      a.applied ? `✓ Applied: ${a.wording || "(passage deleted)"}` : `→ ${a.wording || "(delete the passage)"}`) : null);
  c.append(el("div", { class: "acts" },
    a.answer && a.wording != null && !a.applied ? el("button", { class: "primary", onclick: e => { e.stopPropagation(); applyAsk(a.id, shown); } }, "Apply wording") : null,
    a.answer ? el("button", { class: a.wording != null && !a.applied ? "" : "primary", onclick: e => { e.stopPropagation(); askComment(c, a); } }, "Comment from this") : null,
    el("button", { onclick: e => { e.stopPropagation(); dismissAsk(a.id); } }, "Dismiss")));
  return c;
}

function askComment(card, a, restoring = false) {
  if ($("textarea", card)) return;
  const key = `ask:${a.id}`;
  S.compose[key] = S.compose[key] || { text: a.answer || "" };
  const ta = composer(key, "text", "Comment for the author…");
  const send = async () => {
    if (!ta.value.trim()) return;
    try {
      await api("/api/ask/update", { ask: a.id, comment: ta.value, rev: S.st.rev });
      delete S.compose[key]; touched.add(`compose|${key}`);
    } catch (e) { fail(e); }
  };
  ta.addEventListener("keydown", e => { if (e.key === "Enter" && (e.metaKey || e.ctrlKey)) send(); if (e.key === "Escape") closeComposer(key); });
  card.append(el("div", { class: "tag" }, "Comment for the author (edit before sending)"), ta,
    el("div", { class: "acts" }, el("button", { class: "primary", onclick: send }, "Add comment"),
      el("button", { onclick: () => closeComposer(key) }, "Cancel")));
  if (!restoring) ta.focus();
  layoutMargin();
}

async function reattach(id) {
  const s = currentSelection();
  if (!s || !s.text) return notice("First select the passage this comment is about, then press Attach to selection.", 6000);
  try {
    await api("/api/thread", { thread: id, reattach: true, file: s.file, lines: s.lines, text: s.text, before: s.before, key: s.key, rev: S.st.rev });
    getSelection().removeAllRanges();
  } catch (e) { fail(e); }
}

async function applyAsk(id, rev) {
  try { await api("/api/ask/update", { ask: id, apply: true, rev }); } catch (e) { fail(e); }
}

async function dismissAsk(id) {
  try { await api("/api/ask/update", { ask: id, done: true }); } catch (e) { fail(e); }
}

function composer(key, field, placeholder) {
  const ta = el("textarea", { placeholder, "data-key": `${key}:${field}` });
  ta.value = S.compose[key][field] ?? "";
  touched.add(`compose|${key}`);
  ta.addEventListener("input", () => { if (S.compose[key]) { S.compose[key][field] = ta.value; saveWork(false, `compose|${key}`); } });
  return ta;
}

function closeComposer(key) {
  delete S.compose[key]; touched.add(`compose|${key}`);
  buildMargin();
}

function replyBox(card, t, restoring = false) {
  if ($("textarea", card)) return;
  const key = `thread:${t.id}`;
  S.compose[key] = S.compose[key] || { text: "" };
  const ta = composer(key, "text", "Reply…");
  const send = async () => {
    if (!ta.value.trim()) return;
    try { await api("/api/reply", { thread: t.id, body: ta.value, rev: S.st.rev }); delete S.compose[key]; touched.add(`compose|${key}`); } catch (e) { fail(e); }
  };
  ta.addEventListener("keydown", e => { if (e.key === "Enter" && (e.metaKey || e.ctrlKey)) send(); if (e.key === "Escape") closeComposer(key); });
  card.append(ta, el("div", { class: "acts" }, el("button", { class: "primary", onclick: send }, "Send"),
    el("button", { onclick: () => closeComposer(key) }, "Cancel")));
  if (!restoring) ta.focus();
  layoutMargin();
}

function editMessage(card, t, m, restoring = false) {
  const row = $(`.msg[data-m="${m.id}"]`, card);
  if (!row || $("textarea", row)) return;
  const key = `msg:${t.id}:${m.id}`;
  S.compose[key] = S.compose[key] || { text: m.body };
  const ta = composer(key, "text", "Comment");
  const save = async () => {
    if (!ta.value.trim()) { notice("A comment needs some text.", 6000); return; }
    try {
      await api("/api/message", { thread: t.id, message: m.id, body: ta.value, rev: S.st.rev });
      delete S.compose[key]; touched.add(`compose|${key}`);
    } catch (e) { fail(e); }
  };
  ta.addEventListener("click", e => e.stopPropagation());
  ta.addEventListener("keydown", e => { if (e.key === "Enter" && (e.metaKey || e.ctrlKey)) save(); if (e.key === "Escape") closeComposer(key); });
  row.replaceChildren(el("span", { class: "by" }, m.by), ta, el("div", { class: "acts" },
    el("button", { class: "primary", onclick: e => { e.stopPropagation(); save(); } }, "Save"),
    el("button", { onclick: e => { e.stopPropagation(); closeComposer(key); } }, "Cancel")));
  if (!restoring) { ta.focus(); ta.setSelectionRange(ta.value.length, ta.value.length); }
  layoutMargin();
}

function editDraft(card, d, restoring = false) {
  if ($("textarea", card)) return;
  const shown = S.st.rev;
  const key = `draft:${d.id}`;
  S.compose[key] = S.compose[key] || { text: d.body, wording: d.wording };
  const body = composer(key, "text", "Comment");
  const wording = d.wording !== null ? composer(key, "wording", "Wording to apply") : null;
  const keep = async () => {
    await triage(d.id, "keep", body.value, wording ? wording.value : undefined, shown);
    delete S.compose[key]; touched.add(`compose|${key}`);
   
  };
  card.append(el("div", { class: "tag" }, "Comment"), body,
    wording ? el("div", { class: "tag" }, "Wording to apply") : null, wording,
    el("div", { class: "acts" }, el("button", { class: "primary", onclick: keep }, "Keep"),
      el("button", { onclick: () => closeComposer(key) }, "Cancel")));
  if (!restoring) body.focus();
  layoutMargin();
}

async function setThread(id, status) {
  try { await api("/api/thread", { thread: id, status, rev: S.st.rev }); } catch (e) { fail(e); }
}

const zoombox = document.getElementById("zoombox"), zoomImg = zoombox.querySelector("img");
let zoomScale = 1, zoomFit = true;

function setZoom(scale, fit = false) {
  zoomFit = fit;
  const view = zoombox.querySelector(".zoomview");
  const w = zoomImg.naturalWidth || 800, h = zoomImg.naturalHeight || 600;
  zoomScale = fit ? Math.min(1, (view.clientWidth - 8) / w, (view.clientHeight - 8) / h) : Math.max(0.05, Math.min(8, scale));
  zoomImg.style.width = `${Math.round(w * zoomScale)}px`;
  zoombox.querySelector(".zoomlevel").textContent = `${Math.round(zoomScale * 100)}%`;
}

function openZoom(img) {
  zoomImg.onload = () => setZoom(1, true);
  zoomImg.src = img.currentSrc || img.src;
  zoomImg.alt = img.alt;
  zoombox.showModal();
  if (zoomImg.complete && zoomImg.naturalWidth) setZoom(1, true);
}

zoombox.addEventListener("click", e => {
  const z = e.target.dataset.z;
  if (z === "in") setZoom(zoomScale * 1.25);
  else if (z === "out") setZoom(zoomScale / 1.25);
  else if (z === "fit") setZoom(1, true);
  else if (z === "close" || e.target === zoombox) zoombox.close();
});
zoombox.addEventListener("keydown", e => {
  if (e.key === "+" || e.key === "=") setZoom(zoomScale * 1.25);
  else if (e.key === "-") setZoom(zoomScale / 1.25);
  else if (e.key === "0") setZoom(1, true);
});
window.addEventListener("resize", () => { if (zoombox.open && zoomFit) setZoom(1, true); });

function keepAllBar(drafts) {
  const shown = S.st.rev;
  const edits = drafts.filter(d => d.wording !== null).length;
  const what = `${drafts.length} draft${drafts.length === 1 ? "" : "s"}` + (edits ? `, applying ${edits} edit${edits === 1 ? "" : "s"}` : "");
  const go = async ev => {
    if (!confirm(`Keep all ${what}? Drafts become your comments, and edits become your tracked changes. Any you want back can be undone like any other edit or comment.`)) return;
    const btn = ev.currentTarget;
    btn.disabled = true;
    btn.textContent = "Keeping…";
    try {
      const r = await api("/api/draft", { action: "keep_all", rev: shown }, `Keeping ${what}…`);
      notice(`Kept ${r.kept.length}.` + (r.left.length ? ` ${r.left.length} left for you: their passage changed, so edit or drop them.` : ""), 8000);
    } catch (e) { btn.disabled = false; btn.textContent = `Keep all ${what}`; fail(e); }
  };
  return el("div", { class: "keepall" },
    el("div", { class: "muted small" }, "Done with the ones you wanted to change or drop?"),
    el("button", { class: "primary", onclick: go }, `Keep all ${what}`));
}

async function triage(id, action, body, wording, rev) {
  try { await api("/api/draft", { draft: id, action, body, wording, rev }); } catch (e) { fail(e); throw e; }
}

function setActive(id, scroll = true) {
  S.active = id;
  $$(".card", margin).forEach(c => c.classList.toggle("active", c.dataset.t === id));
  $$("mark.scrv-hl", doc).forEach(m => m.classList.toggle("active", m.dataset.t === id));
  const it = [...S.st.threads, ...S.st.drafts, ...S.st.asks].find(x => x.id === id);
  const card = $(`.card[data-t="${CSS.escape(id)}"]`, margin);
  if (scroll && it && it._mark) it._mark.scrollIntoView({ block: "center", behavior: "smooth" });
  else if (scroll && card) card.scrollIntoView({ block: "center", behavior: "smooth" });
  const b = it && it._mark && it._mark.closest(".scrv-b");
  if (b && b.dataset.lines) tell("go", b);
}

// ------------------------------------------------------------------ changes

async function decide(id, decision) {
  closePop();
  try {
    await api("/api/decide", { change: id, decision, rev: S.st.rev });
    notice(`${id} ${decision === "accept" ? "accepted" : "rejected"}.`, 8000, { label: "Undo", run: () => undo(id) });
  } catch (e) { fail(e); }
}

async function undo(id) {
  try { await api("/api/undo", { change: id, rev: S.st.rev }); notice(`${id} is undecided again.`, 5000); } catch (e) { fail(e); }
}

function openDecision(target) {
  const ids = target.dataset.c.split(" ");
  const cs = ids.map(id => S.st.changes.find(c => c.id === id)).filter(c => c && c.state === "pending");
  if (!cs.length) return;
  pop.replaceChildren(...cs.map(c => el("div", {},
    el("div", { class: "diff" }, el("del", {}, c.base || "∅"), " → ", el("ins", {}, c.reviewer || "∅")),
    el("div", { class: "acts" }, ...decisionButtons(c)))));
  S.popReturn = target;
  place(pop, target.getBoundingClientRect());
  $("button", pop).focus();
}

function closePop() {
  if (pop.hidden) return;
  pop.hidden = true;
  if (S.popReturn && document.contains(S.popReturn)) S.popReturn.focus();
  S.popReturn = null;
}

doc.addEventListener("click", e => {
  const ch = e.target.closest("[data-c]");
  if (!ch || S.st.mode !== "author") return;
  e.stopPropagation();
  openDecision(ch);
});

doc.addEventListener("keydown", e => {
  const ch = e.target.closest("[data-c][tabindex]");
  if (ch && (e.key === "Enter" || e.key === " ")) { e.preventDefault(); openDecision(ch); }
});

pop.addEventListener("keydown", e => { if (e.key === "Escape") closePop(); });

function place(box, rect) {
  box.hidden = false;
  const w = box.offsetWidth;
  box.style.left = `${Math.max(8, Math.min(innerWidth - w - 8, rect.left + scrollX))}px`;
  box.style.top = `${rect.bottom + scrollY + 6}px`;
}

document.addEventListener("click", e => {
  if (!pop.contains(e.target) && !e.target.closest("[data-c]")) closePop();
});

// ------------------------------------------------------------------ selection

function sourceText(range) {
  const frag = range.cloneContents();
  for (const x of frag.querySelectorAll(HISTORICAL)) x.remove();
  for (const x of frag.querySelectorAll(GENERATED)) x.replaceWith(PLACEHOLDER);
  return frag.textContent;
}

function currentSelection() {
  const sel = getSelection();
  if (!sel || sel.isCollapsed || !sel.rangeCount) return null;
  const range = sel.getRangeAt(0);
  if (!doc.contains(range.commonAncestorContainer)) return null;
  const node = range.startContainer.nodeType === 1 ? range.startContainer : range.startContainer.parentElement;
  const block = node.closest(".scrv-b");
  if (!block || !block.dataset.file) return null;
  const pre = document.createRange();
  pre.setStart(block, 0);
  pre.setEnd(range.startContainer, range.startOffset);
  const sameBlock = block.contains(range.endContainer);
  return {
    file: block.dataset.file, lines: block.dataset.lines,
    text: sameBlock ? sourceText(range) : "", before: sourceText(pre).slice(-60),
    rect: range.getBoundingClientRect(), key: S.key,
  };
}

let selTimer = null;
document.addEventListener("selectionchange", () => {
  clearTimeout(selTimer);
  selTimer = setTimeout(onSelection, 250);
});

async function onSelection() {
  if (!S.st || !S.st.round || S.st.mode === "closed" || S.editing) return;
  const s = currentSelection();
  if (!s) {
    tool.hidden = true;
    if (S.selPosted) { S.selPosted = false; api("/api/selection", {}).catch(() => {}); }
    return;
  }
  S.sel = s;
  place(tool, s.rect);
  S.selPosted = true;
  api("/api/selection", { file: s.file, lines: s.lines, text: s.text, before: s.before, key: s.key }).catch(() => {});
}

tool.addEventListener("mousedown", e => e.preventDefault());
tool.addEventListener("click", e => {
  e.stopPropagation();
  const act = e.target.dataset.act;
  if (!act || !S.sel) return;
  const s = S.sel;
  tool.hidden = true;
  const ta = el("textarea", { placeholder: act === "comment" ? "Your comment…" : "What should your agent look at here?" });
  const same = S.popDraft && S.popDraft.act === act && S.popDraft.quote === s.text;
  const kept = same ? S.popDraft.text : "";
  const other = S.popDraft && !same ? S.popDraft : null;
  ta.value = kept;
  const remember = () => { S.popDraft = ta.value.trim() ? { act, text: ta.value, quote: s.text } : null; saveWork(false, "pop|"); };
  ta.addEventListener("input", remember);
  const send = async () => {
    if (!ta.value.trim()) return;
    try {
      const where = { file: s.file, lines: s.lines, text: s.text, before: s.before, key: s.key };
      if (act === "comment") await api("/api/comment", { ...where, body: ta.value, rev: S.st.rev });
      else { await api("/api/ask", { ...where, question: ta.value }); notice("Your question is in the margin. When you're ready, tell your agent: “check my asks”.", 7000); }
      S.popDraft = null;
      saveWork(true, "pop|");
      closePop();
    } catch (err) {
      if (err.stale) {
        remember();
        closePop();
        notice("The page changed while you wrote that, so the selection no longer matches. Your text is kept: select the passage again and press "
          + (act === "comment" ? "Comment." : "Ask agent."), 12000);
        load(true);
      } else fail(err);
    }
  };
  ta.addEventListener("keydown", ev => { if (ev.key === "Enter" && (ev.metaKey || ev.ctrlKey)) send(); if (ev.key === "Escape") closePop(); });
  pop.replaceChildren(
    el("div", { class: "muted" }, act === "comment" ? "Comment on the selection" : "Ask your agent about the selection"),
    ta, act === "ask" ? el("div", { class: "muted small" }, "Asks and their answers are kept in the review record when the round is handed on.") : null,
    el("div", { class: "acts" }, el("button", { class: "primary", onclick: send }, act === "comment" ? "Comment" : "Queue question"),
      el("button", { onclick: () => { S.popDraft = null; saveWork(true, "pop|"); closePop(); } }, "Cancel")),
    kept ? el("div", { class: "muted small" }, "Your earlier text, kept from before.") : null,
    other ? el("div", { class: "muted small" }, `You also have unsent text about “${(other.quote || "").slice(0, 40)}”. `,
      el("button", { onclick: () => { ta.value = other.text; remember(); ta.focus(); } }, "Use it here")) : null);
  place(pop, s.rect);
  ta.focus();
});

// ------------------------------------------------------------------ editing

async function startEdit(block) {
  if (S.editing) return;
  const token = Symbol("edit");
  S.editing = { block, token };
  let src;
  try { src = await api(`/api/source?file=${encodeURIComponent(block.dataset.file)}&lines=${block.dataset.lines}`); }
  catch (e) { if (S.editing && S.editing.token === token) S.editing = null; return fail(e); }
  if (!S.editing || S.editing.token !== token || !block.isConnected) {
    if (S.editing && S.editing.token === token) S.editing = null;
    return;
  }
  const kind = /\.(tex|ltx)$/.test(block.dataset.file) ? "LaTeX" : "Markdown";
  const ta = el("textarea", { spellcheck: "true", "aria-label": `${kind} source` });
  const slot = `${src.file}:${src.first}`, earlier = S.edits[slot];
  ta.value = earlier && earlier.file_rev === src.file_rev && earlier.last === src.last ? earlier.text : src.text;
  if (earlier && ta.value !== earlier.text) {
    notice(`You had an unsaved edit here, but ${src.file} has changed since, so it is not put back.`, 0,
      { label: "Copy my earlier text", run: () => navigator.clipboard?.writeText(earlier.text) });
  }
  ta.addEventListener("input", () => {
    S.edits[slot] = { file: src.file, first: src.first, last: src.last, file_rev: src.file_rev, text: ta.value };
    saveWork(false, `edit|${slot}`);
  });
  const box = el("div", { class: "editor" },
    el("div", { class: "hint" }, `${kind} source · ${src.file}, lines ${src.first}–${src.last} · Ctrl/⌘-Enter saves, Esc cancels`
      + (earlier && ta.value === earlier.text && earlier.text !== src.text ? " · your unsaved edit, restored" : "")),
    ta,
    el("div", { class: "acts" }, el("button", { class: "primary", onclick: save }, "Save"), el("button", { onclick: cancel }, "Cancel")));
  block.replaceWith(box);
  ta.style.height = `${Math.max(80, ta.scrollHeight + 6)}px`;
  ta.focus();
  ta.addEventListener("keydown", e => {
    if (e.key === "Enter" && (e.metaKey || e.ctrlKey)) save();
    if (e.key === "Escape") cancel();
  });
  async function save() {
    const btn = $("button.primary", box);
    if (btn.disabled) return;
    btn.disabled = true;
    btn.textContent = "Saving…";
    try {
      await api("/api/source", { file: src.file, first: src.first, last: src.last, text: ta.value, file_rev: src.file_rev });
      delete S.edits[slot];
      saveWork(true, `edit|${slot}`);
      done();
    } catch (e) { btn.disabled = false; btn.textContent = "Save"; notice(e.message, 12000); }
  }
  function cancel() { delete S.edits[slot]; saveWork(true, `edit|${slot}`); box.replaceWith(block); done(); }
  function done() { if (S.editing && S.editing.token === token) S.editing = null; load(true); }
}

// ------------------------------------------------------------------ navigation

function queue() {
  const st = S.st, open = st.threads.filter(t => t.status === "open");
  const outside = [...$$(".row[data-q]", $("#files")), ...$$(".card.orphan.open, .card.draft, .card.ask", margin)];
  const marks = open.map(t => $(`mark.scrv-hl[data-t="${t.id}"]`, doc) || t._mark).filter(Boolean);
  const changes = st.mode === "author"
    ? $$("[data-c][tabindex]", doc)
    : $$(".scrv-del, .scrv-ins, .scrv-old, .scrv-ins-block", doc);
  const inText = [...new Set([...marks, ...changes])]
    .sort((a, b) => (a.compareDocumentPosition(b) & Node.DOCUMENT_POSITION_FOLLOWING ? -1 : 1));
  return [...outside, ...inText];
}

function step(d) {
  const items = queue();
  if (!items.length) { notice("Nothing left to look at in this round.", 4000); return; }
  S.cur = (S.cur + d + items.length) % items.length;
  $$(".cur-item").forEach(x => x.classList.remove("cur-item"));
  const it = items[S.cur];
  it.classList.add("cur-item");
  it.scrollIntoView({ block: "center", behavior: "smooth" });
  if (it.tabIndex >= 0) it.focus({ preventScroll: true });
  if (it.dataset.t) setActive(it.dataset.t, false);
}

$("#next").onclick = () => step(1);
$("#prev").onclick = () => step(-1);
$("#help").onclick = () => $("#helpbox").showModal();
// ------------------------------------------------------------------ items list

const KINDS = { change: "Changes", comment: "Comments", draft: "Drafts", ask: "Asks", lost: "Lost" };
const Q = { open: false, kind: "all" };
try { Q.done = localStorage.getItem("scrivmark-qdone") === "on"; } catch { Q.done = false; }

function sectionOf(node, heads) {
  let h = null;
  for (const x of heads) {
    if (x === node || x.compareDocumentPosition(node) & Node.DOCUMENT_POSITION_FOLLOWING) h = x;
    else break;
  }
  return h ? h.textContent.replace(/\s+/g, " ").trim() : "";
}

function short(s, n = 90) {
  const t = (s || "").replace(/\s+/g, " ").trim();
  return t.length > n ? t.slice(0, n - 1) + "…" : t;
}

function items() {
  const st = S.st, out = [];
  const where = it => it.line ? `${it.anchor.file}:${it.line[0]}` : it.anchor.file;
  for (const t of st.threads) {
    const lost = !t.line;
    out.push({ kind: lost && t.status === "open" ? "lost" : "comment", id: t.id, done: t.status === "resolved",
      label: short(t.messages[0].body), quote: short(t.current ?? t.anchor.exact, 60), at: where(t),
      node: t._mark, go: () => setActive(t.id), state: lost ? "lost its place" : t.status });
  }
  for (const d of st.drafts) {
    out.push({ kind: d.line ? "draft" : "lost", id: d.id, done: false,
      label: d.body ? short(d.body) : `→ ${short(d.wording, 70)}`, quote: short(d.current ?? d.anchor.exact, 60),
      at: where(d), node: d._mark, go: () => setActive(d.id), state: d.by ? `from ${d.by}` : "draft" });
  }
  for (const a of st.asks) {
    out.push({ kind: "ask", id: a.id, done: !!a.done, label: short(a.question), quote: short(a.current ?? a.anchor.exact, 60),
      at: where(a), node: a._mark, go: () => setActive(a.id), state: a.answer ? "answered" : "waiting" });
  }
  if (st.mode === "author" || st.mode === "closed") {
    for (const c of st.changes) {
      const node = $(`[data-c~="${c.id}"]`, doc) || $(`.row[data-q="${c.id}"]`);
      out.push({ kind: "change", id: c.id, done: c.state !== "pending", at: c.file,
        label: c.type === "file" ? `${c.file}: ${c.reviewer}` : `${short(c.base, 40) || "(nothing)"} → ${short(c.reviewer, 40) || "(removed)"}`,
        node, go: () => node && (node.scrollIntoView({ block: "center", behavior: "smooth" }), node.focus?.({ preventScroll: true })),
        state: c.state });
    }
  } else if (st.mode === "reviewer") {
    const marks = $$(".scrv-del, .scrv-ins, .scrv-old, .scrv-ins-block", doc);
    for (let i = 0; i < marks.length; i++) {
      const m = marks[i], next = marks[i + 1];
      const pair = m.matches(".scrv-del") && next && next.matches(".scrv-ins") && m.nextElementSibling === next;
      const blk = m.closest(".scrv-b") || m;
      out.push({ kind: "change", id: `e${i}`, done: false, at: `${blk.dataset.file || ""}${blk.dataset.lines ? ":" + blk.dataset.lines.split("-")[0] : ""}`,
        label: pair ? `${short(m.textContent, 40)} → ${short(next.textContent, 40)}`
          : m.matches(".scrv-del, .scrv-old") ? `removed: ${short(m.textContent, 70)}` : `added: ${short(m.textContent, 70)}`,
        node: m, go: () => m.scrollIntoView({ block: "center", behavior: "smooth" }), state: "your edit" });
      if (pair) i++;
    }
  }
  const heads = $$("h1, h2, h3", doc);
  for (const it of out) {
    it.section = it.node && doc.contains(it.node) ? sectionOf(it.node, heads) : "";
    it.pos = it.node && doc.contains(it.node) ? it.node : null;
  }
  return out.sort((a, b) => {
    if (!a.pos || !b.pos) return (a.pos ? 0 : 1) - (b.pos ? 0 : 1);
    return a.pos.compareDocumentPosition(b.pos) & Node.DOCUMENT_POSITION_FOLLOWING ? -1 : 1;
  });
}

function drawQueue() {
  if (!Q.open || !S.st) return;
  const all = items(), shown = all.filter(it => Q.done || !it.done);
  const count = k => shown.filter(it => k === "all" || it.kind === k).length;
  $("#qfilters").replaceChildren(...["all", ...Object.keys(KINDS)].filter(k => k === "all" || all.some(it => it.kind === k)).map(k =>
    el("button", { class: `chip${Q.kind === k ? " on" : ""}`, "aria-pressed": String(Q.kind === k),
      onclick: () => { Q.kind = k; drawQueue(); } }, `${k === "all" ? "All" : KINDS[k]} ${count(k)}`)));
  const list = shown.filter(it => Q.kind === "all" || it.kind === Q.kind);
  let section = null;
  const rows = [];
  for (const it of list) {
    if (it.section !== section) {
      section = it.section;
      rows.push(el("li", { class: "qsec" }, section || (it.pos ? "Before the first heading" : "Outside the running text")));
    }
    rows.push(el("li", { class: `qitem ${it.kind}${it.done ? " done" : ""}` },
      el("button", { onclick: () => it.go(), title: it.at },
        el("span", { class: "qkind" }, KINDS[it.kind].replace(/s$/, "")),
        el("span", { class: "qlabel" }, it.label || it.quote),
        it.quote && it.label !== it.quote ? el("span", { class: "qquote" }, `“${it.quote}”`) : null,
        el("span", { class: "qmeta" }, `${it.state} · ${it.at}`))));
  }
  $("#qlist").replaceChildren(...(rows.length ? rows : [el("li", { class: "muted small" }, "Nothing here.")]));
}

function toggleQueue(open = !Q.open) {
  Q.open = open;
  $("#queue").hidden = !open;
  $("#queuebtn").setAttribute("aria-expanded", String(open));
  document.body.classList.toggle("queue-open", open);
  if (open) { drawQueue(); queueStatus(); }
}

async function queueStatus() {
  const box = $("#qstatus");
  if (!S.st || !S.st.round || S.st.mode === "closed" || S.st.mode === "read") { box.hidden = true; return; }
  try {
    const p = await api("/api/preflight", undefined, "Checking the round…");
    const what = p.transition === "return" ? "return" : "close";
    box.hidden = false;
    box.replaceChildren(p.ready
      ? el("span", {}, `✓ Ready to ${what}.`)
      : el("span", {}, `Not ready to ${what}: `, ...(p.blockers || []).map((b, i) => el("span", {}, (i ? "; " : "") + (b.text || b)))));
  } catch { box.hidden = true; }
}

$("#queuebtn").onclick = () => toggleQueue();
$("#qclose").onclick = () => toggleQueue(false);
$("#qdone").checked = Q.done;
$("#qdone").onchange = e => {
  Q.done = e.target.checked;
  try { localStorage.setItem("scrivmark-qdone", Q.done ? "on" : "off"); } catch { /* ignore */ }
  drawQueue();
};

// ------------------------------------------------------------------ reading tab

const link = new BroadcastChannel("scrivmark-follow");
let followTimer = null;

$("#readtab").addEventListener("click", () => window.open("/read", "scrivmark-read"));

function tell(kind, block) {
  const [line] = block.dataset.lines.split("-").map(Number);
  link.postMessage({ from: "review", kind, file: block.dataset.file, line, top: block.getBoundingClientRect().top });
}

addEventListener("scroll", () => {
  clearTimeout(followTimer);
  followTimer = setTimeout(() => {
    const b = $$(".scrv-b", doc).find(x => x.getBoundingClientRect().bottom > 60);
    if (b && b.dataset.lines) tell("scroll", b);
  }, 120);
}, { passive: true });

link.onmessage = e => {
  const m = e.data;
  if (!m || m.from !== "read") return;
  const b = findBlock(m.file, m.line);
  if (!b) return;
  b.scrollIntoView({ block: "center", behavior: "smooth" });
  b.classList.add("followed");
  setTimeout(() => b.classList.remove("followed"), 1200);
};

document.addEventListener("keydown", e => {
  if (e.target.closest("textarea, input") || e.metaKey || e.ctrlKey || e.altKey) return;
  if (e.key === "j") step(1);
  if (e.key === "k") step(-1);
  if (e.key === "?") $("#helpbox").showModal();
  if (e.key === "q") toggleQueue();
  if (e.key === "Escape" && Q.open) toggleQueue(false);
});
addEventListener("resize", () => S.st && layoutMargin());

// ------------------------------------------------------------------ live updates

const es = new EventSource("/api/events");
es.onmessage = () => load();
es.onopen = () => { $("#conn").hidden = true; };
es.onerror = () => { $("#conn").hidden = false; };
load(true);
