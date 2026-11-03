"use strict";

const S = { st: null, key: null, editing: null, pendingReload: false, active: null, cur: -1, selPosted: false };
const $ = (s, r = document) => r.querySelector(s);
const $$ = (s, r = document) => Array.from(r.querySelectorAll(s));
const doc = $("#doc"), margin = $("#margin"), tool = $("#tool"), pop = $("#pop");

history.replaceState(null, "", "/");

async function api(path, body) {
  const opts = body === undefined ? {} : {
    method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify(body),
  };
  const r = await fetch(path, opts);
  const j = await r.json().catch(() => ({ error: `HTTP ${r.status}` }));
  if (!r.ok) throw Object.assign(new Error(j.error || "Something went wrong"), j);
  return j;
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

function notice(msg, ms) {
  const n = $("#notice");
  n.textContent = msg;
  n.hidden = !msg;
  clearTimeout(notice.t);
  if (ms) notice.t = setTimeout(() => (n.hidden = true), ms);
}

function fail(e) {
  notice(e.stale ? "The review changed while you were working; refreshed. Please try again." : e.message, 8000);
  if (e.stale) load(true);
}

// ------------------------------------------------------------------ loading

async function load(force = false) {
  let st;
  try { st = await api("/api/state"); } catch (e) { notice(e.message); return; }
  if (!force && st.key === S.key) return;
  if (S.editing) { S.pendingReload = true; notice("The manuscript changed elsewhere; it will refresh when you finish editing."); return; }
  const keep = scrollAnchor();
  S.st = st; S.key = st.key;
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

function draw() {
  const st = S.st;
  document.body.className = st.mode;
  $("#mode").textContent = MODE[st.mode];
  $("#mode").className = `badge ${st.mode}`;
  const r = st.round;
  $("#roundid").textContent = r ? `${r.id} · ${st.mode === "author" ? "from " + r.reviewer.name : "on " + r.author_branch}` : st.branch;
  const stmt = $("#statement");
  stmt.hidden = !(r && st.mode !== "reviewer");
  if (r) stmt.textContent = r.statement;
  doc.innerHTML = st.html;
  decorate();
  drawFiles();
  drawCounts();
  if (st.mode === "read") notice("Reading only. Start a review with `scrivmark start`, or open one with `scrivmark open <round>`.");
  else if (st.warnings.length) notice("pandoc: " + st.warnings.join(" "), 10000);
  requestAnimationFrame(drawMargin);
}

function drawCounts() {
  const st = S.st, open = st.threads.filter(t => t.status === "open").length;
  let txt = `${open} open comment${open === 1 ? "" : "s"}`;
  if (st.mode === "reviewer" && st.drafts.length) txt += ` · ${st.drafts.length} draft${st.drafts.length === 1 ? "" : "s"}`;
  if (st.mode === "author") {
    const p = st.changes.filter(c => c.state === "pending").length;
    txt = `${p} change${p === 1 ? "" : "s"} to decide · ` + txt;
  }
  $("#counts").textContent = S.st.round ? txt : "";
}

function drawFiles() {
  const box = $("#files"), fc = S.st.changes.filter(c => c.type === "file");
  box.hidden = !fc.length;
  box.replaceChildren(el("div", { class: "muted" }, "Changes to whole files:"),
    ...fc.map(c => el("div", { class: "row", "data-c": c.id },
      el("span", {}, `${c.reviewer} — ${c.state}`),
      c.state === "pending" && S.st.mode === "author" ? [
        el("button", { onclick: () => decide(c.id, "accept") }, "Accept"),
        el("button", { onclick: () => decide(c.id, "reject") }, "Reject")] : null)));
}

function editable() { return S.st.mode === "reviewer" || S.st.mode === "author"; }

function decorate() {
  for (const a of $$('a[href^="#ref-"]', doc)) {
    const ref = document.getElementById(a.getAttribute("href").slice(1));
    if (ref) a.title = ref.textContent.trim().replace(/\s+/g, " ");
  }
  if (editable()) {
    for (const b of $$(".scrv-b", doc)) {
      b.prepend(el("button", { class: "edit", title: "Edit this passage's Markdown", onclick: e => { e.stopPropagation(); startEdit(b); } }, "✎"));
    }
  }
  for (const t of [...S.st.threads, ...S.st.drafts]) if (t.line) highlight(t);
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

function highlight(t) {
  const block = findBlock(t.anchor.file, t.line[0]);
  if (!block) return;
  t._block = block;
  const nodes = [], walker = document.createTreeWalker(block, NodeFilter.SHOW_TEXT, {
    acceptNode: n => n.parentElement.closest(".edit, .scrv-del, .scrv-old") ? NodeFilter.FILTER_REJECT : NodeFilter.FILTER_ACCEPT,
  });
  let text = "", n;
  const map = [];
  while ((n = walker.nextNode())) {
    nodes.push(n);
    for (let i = 0; i < n.data.length; i++) {
      const ch = /\s/.test(n.data[i]) ? " " : n.data[i];
      if (ch === " " && text.endsWith(" ")) continue;
      text += ch;
      map.push([n, i]);
    }
  }
  let needle = plainQuote(t.current || t.anchor.exact);
  let at = needle ? text.indexOf(needle) : -1;
  if (at < 0 && needle.length > 30) { needle = needle.slice(0, 30); at = text.indexOf(needle); }
  if (at < 0) { block.classList.add("block-hl"); t._mark = block; return; }
  const start = map[at], end = map[at + needle.length - 1];
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
    const m = el("mark", { class: "scrv-hl", "data-t": t.id });
    try { range.surroundContents(m); } catch { continue; }
    m.addEventListener("click", ev => { ev.stopPropagation(); setActive(t.id); });
    first = first || m;
  }
  t._mark = first || block;
}

// ------------------------------------------------------------------ margin

function drawMargin() {
  const st = S.st;
  margin.replaceChildren();
  const items = [
    ...st.drafts.map(d => ({ ...d, kind: "draft" })),
    ...st.threads.map(t => ({ ...t, kind: "thread" })),
  ];
  const cards = items.map(it => {
    const c = it.kind === "draft" ? draftCard(it) : threadCard(it);
    c.dataset.t = it.id;
    c.addEventListener("click", () => setActive(it.id, false));
    margin.append(c);
    const live = [...st.threads, ...st.drafts].find(x => x.id === it.id);
    return { c, mark: live && live._mark };
  });
  if (getComputedStyle(cards[0]?.c || margin).position !== "absolute") return;
  const top0 = margin.getBoundingClientRect().top;
  cards.forEach(x => (x.want = x.mark ? x.mark.getBoundingClientRect().top - top0 : -1));
  cards.sort((a, b) => a.want - b.want);
  let y = 0;
  for (const x of cards) {
    const top = Math.max(x.want, y);
    x.c.style.top = `${top}px`;
    y = top + x.c.offsetHeight + 8;
  }
  margin.style.minHeight = `${y}px`;
}

function quoteLine(it) {
  const q = plainQuote(it.current ?? it.anchor.exact);
  if (!q) return el("div", { class: "quote" }, "Where text was removed");
  return el("div", { class: "quote", title: q }, `“${q}”`);
}

function threadCard(t) {
  const orphan = !t.line;
  const c = el("div", { class: `card ${t.status}${orphan ? " orphan" : ""}` },
    orphan ? el("div", { class: "tag" }, "Lost its place — the passage changed") : null,
    quoteLine(t),
    ...t.messages.map(m => el("div", { class: "msg" }, el("span", { class: "by" }, m.by), m.body)));
  if (!S.st.round || S.st.mode === "closed") return c;
  const acts = el("div", { class: "acts" },
    el("button", { onclick: e => { e.stopPropagation(); replyBox(c, t); } }, "Reply"),
    t.status === "open"
      ? el("button", { onclick: e => { e.stopPropagation(); setThread(t.id, "resolved"); } }, "Resolve")
      : el("button", { onclick: e => { e.stopPropagation(); setThread(t.id, "open"); } }, "Reopen"));
  c.append(acts);
  return c;
}

function draftCard(d) {
  const c = el("div", { class: "card draft" },
    el("div", { class: "tag" }, "Draft from your agent"),
    quoteLine(d),
    el("div", { class: "msg" }, d.body),
    d.wording !== null ? el("div", { class: "wording", title: "Applied as an edit if kept" }, `→ ${d.wording}`) : null);
  c.append(el("div", { class: "acts" },
    el("button", { class: "primary", onclick: e => { e.stopPropagation(); triage(d.id, "keep"); } }, d.wording !== null ? "Keep & apply" : "Keep"),
    el("button", { onclick: e => { e.stopPropagation(); editDraft(c, d); } }, "Edit"),
    el("button", { onclick: e => { e.stopPropagation(); triage(d.id, "drop"); } }, "Drop")));
  return c;
}

function replyBox(card, t) {
  if ($("textarea", card)) return;
  const ta = el("textarea", { placeholder: "Reply…" });
  const send = async () => {
    if (!ta.value.trim()) return;
    try { await api("/api/reply", { thread: t.id, body: ta.value, rev: S.st.rev }); } catch (e) { fail(e); }
  };
  ta.addEventListener("keydown", e => { if (e.key === "Enter" && (e.metaKey || e.ctrlKey)) send(); if (e.key === "Escape") drawMargin(); });
  card.append(ta, el("div", { class: "acts" }, el("button", { class: "primary", onclick: send }, "Send"), el("button", { onclick: drawMargin }, "Cancel")));
  ta.focus();
}

function editDraft(card, d) {
  if ($("textarea", card)) return;
  const body = el("textarea", {}, d.body);
  const wording = d.wording !== null ? el("textarea", { title: "Wording to apply" }, d.wording) : null;
  card.append(el("div", { class: "tag" }, "Comment"), body,
    wording ? el("div", { class: "tag" }, "Wording to apply") : null, wording,
    el("div", { class: "acts" },
      el("button", { class: "primary", onclick: () => triage(d.id, "keep", body.value, wording ? wording.value : undefined) }, "Keep"),
      el("button", { onclick: drawMargin }, "Cancel")));
  body.focus();
}

async function setThread(id, status) {
  try { await api("/api/thread", { thread: id, status, rev: S.st.rev }); } catch (e) { fail(e); }
}

async function triage(id, action, body, wording) {
  try { await api("/api/draft", { draft: id, action, body, wording, rev: S.st.rev }); } catch (e) { fail(e); }
}

function setActive(id, scroll = true) {
  S.active = id;
  $$(".card", margin).forEach(c => c.classList.toggle("active", c.dataset.t === id));
  $$("mark.scrv-hl", doc).forEach(m => m.classList.toggle("active", m.dataset.t === id));
  const it = [...S.st.threads, ...S.st.drafts].find(x => x.id === id);
  if (scroll && it && it._mark) it._mark.scrollIntoView({ block: "center", behavior: "smooth" });
}

// ------------------------------------------------------------------ changes

async function decide(id, decision) {
  pop.hidden = true;
  try { await api("/api/decide", { change: id, decision, rev: S.st.rev }); } catch (e) { fail(e); }
}

doc.addEventListener("click", e => {
  const ch = e.target.closest("[data-c]");
  if (!ch || S.st.mode !== "author") return;
  const ids = ch.dataset.c.split(" ");
  const changes = ids.map(id => S.st.changes.find(c => c.id === id)).filter(c => c && c.state === "pending");
  if (!changes.length) return;
  e.stopPropagation();
  pop.replaceChildren(...changes.map(c => el("div", {},
    el("div", { class: "diff" }, el("del", {}, c.base || "∅"), " → ", el("ins", {}, c.reviewer || "∅")),
    el("div", { class: "acts" },
      el("button", { class: "primary", onclick: () => decide(c.id, "accept") }, "Accept"),
      el("button", { onclick: () => decide(c.id, "reject") }, "Reject")))));
  place(pop, ch.getBoundingClientRect());
  pop.hidden = false;
});

function place(box, rect) {
  box.hidden = false;
  const w = box.offsetWidth;
  box.style.left = `${Math.max(8, Math.min(innerWidth - w - 8, rect.left + scrollX))}px`;
  box.style.top = `${rect.bottom + scrollY + 6}px`;
}

document.addEventListener("click", e => {
  if (!pop.contains(e.target) && !e.target.closest("[data-c]")) pop.hidden = true;
});

// ------------------------------------------------------------------ selection

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
    text: sameBlock ? sel.toString() : "", before: pre.toString().slice(-60), rect: range.getBoundingClientRect(),
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
  api("/api/selection", { file: s.file, lines: s.lines, text: s.text, before: s.before }).catch(() => {});
}

tool.addEventListener("mousedown", e => e.preventDefault());
tool.addEventListener("click", e => {
  e.stopPropagation();
  const act = e.target.dataset.act;
  if (!act || !S.sel) return;
  const s = S.sel;
  tool.hidden = true;
  const ta = el("textarea", { placeholder: act === "comment" ? "Your comment…" : "What should your agent look at here?" });
  const send = async () => {
    if (!ta.value.trim()) return;
    try {
      if (act === "comment") await api("/api/comment", { file: s.file, lines: s.lines, text: s.text, before: s.before, body: ta.value, rev: S.st.rev });
      else { await api("/api/ask", { file: s.file, lines: s.lines, text: s.text, before: s.before, question: ta.value }); notice("Queued. Tell your agent: “check my asks”.", 6000); }
      pop.hidden = true;
    } catch (err) { fail(err); }
  };
  ta.addEventListener("keydown", ev => { if (ev.key === "Enter" && (ev.metaKey || ev.ctrlKey)) send(); if (ev.key === "Escape") pop.hidden = true; });
  pop.replaceChildren(
    el("div", { class: "muted" }, act === "comment" ? "Comment on the selection" : "Ask your agent about the selection"),
    ta, el("div", { class: "acts" }, el("button", { class: "primary", onclick: send }, act === "comment" ? "Comment" : "Queue question"),
      el("button", { onclick: () => (pop.hidden = true) }, "Cancel")));
  place(pop, s.rect);
  ta.focus();
});

// ------------------------------------------------------------------ editing

async function startEdit(block) {
  if (S.editing) return;
  let src;
  try { src = await api(`/api/source?file=${encodeURIComponent(block.dataset.file)}&lines=${block.dataset.lines}`); } catch (e) { return fail(e); }
  S.editing = block;
  const ta = el("textarea", { spellcheck: "true" }, src.text);
  const box = el("div", { class: "editor" },
    el("div", { class: "hint" }, `Markdown source · ${src.file}, lines ${src.first}–${src.last} · Ctrl/⌘-Enter saves, Esc cancels`),
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
    try {
      await api("/api/source", { file: src.file, first: src.first, last: src.last, text: ta.value, file_rev: src.file_rev });
      done();
    } catch (e) { notice(e.message, 10000); }
  }
  function cancel() { box.replaceWith(block); done(); }
  function done() { S.editing = null; load(true); }
}

// ------------------------------------------------------------------ navigation

function navItems() {
  const marks = [...new Set($$("mark.scrv-hl", doc).map(m => m.dataset.t))].map(id => $(`mark.scrv-hl[data-t="${id}"]`, doc));
  const changes = S.st.mode === "author"
    ? $$("[data-c]", doc).filter(x => x.dataset.c.split(" ").some(id => S.st.changes.find(c => c.id === id && c.state === "pending")))
    : $$(".scrv-del, .scrv-ins, .scrv-old, .scrv-ins-block", doc);
  return [...marks, ...changes].sort((a, b) => a.compareDocumentPosition(b) & Node.DOCUMENT_POSITION_FOLLOWING ? -1 : 1);
}

function step(d) {
  const items = navItems();
  if (!items.length) return;
  S.cur = (S.cur + d + items.length) % items.length;
  $$(".cur-item", doc).forEach(x => x.classList.remove("cur-item"));
  const it = items[S.cur];
  it.classList.add("cur-item");
  it.scrollIntoView({ block: "center", behavior: "smooth" });
  if (it.dataset.t) setActive(it.dataset.t, false);
}

$("#next").onclick = () => step(1);
$("#prev").onclick = () => step(-1);
$("#help").onclick = () => $("#helpbox").showModal();
document.addEventListener("keydown", e => {
  if (e.target.closest("textarea, input") || e.metaKey || e.ctrlKey || e.altKey) return;
  if (e.key === "j") step(1);
  if (e.key === "k") step(-1);
  if (e.key === "?") $("#helpbox").showModal();
});
addEventListener("resize", () => S.st && drawMargin());

// ------------------------------------------------------------------ live updates

const es = new EventSource("/api/events");
es.onmessage = () => load();
load(true);
