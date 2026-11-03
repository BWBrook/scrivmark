"use strict";

const S = { st: null, key: null, editing: null, active: null, cur: -1, selPosted: false, compose: {}, focus: null };
const $ = (s, r = document) => r.querySelector(s);
const $$ = (s, r = document) => Array.from(r.querySelectorAll(s));
const doc = $("#doc"), margin = $("#margin"), tool = $("#tool"), pop = $("#pop");
const GENERATED = "span.citation, a.scrv-xref, .scrv-eqno, math, .scrv-unresolved, a.footnote-ref";
const HISTORICAL = ".scrv-del, .scrv-old, .edit";
const PLACEHOLDER = "￼";

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

function notice(msg, ms, action) {
  const n = $("#notice");
  n.replaceChildren(msg || "");
  if (action) n.append(" ", el("button", { onclick: () => { n.hidden = true; action.run(); } }, action.label));
  n.hidden = !msg;
  clearTimeout(notice.t);
  if (ms) notice.t = setTimeout(() => (n.hidden = true), ms);
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

function pending() { return S.st.changes.filter(c => c.state === "pending"); }

function draw() {
  const st = S.st;
  document.body.className = st.mode;
  $("#mode").textContent = MODE[st.mode];
  $("#mode").className = `badge ${st.mode}`;
  const r = st.round;
  const who = st.me ? ` · you are ${st.me}` : "";
  $("#roundid").textContent = r ? `${r.id} · ${st.mode === "author" ? "from " + r.reviewer.name : "on " + r.author_branch}${who}` : st.branch;
  const stmt = $("#statement");
  stmt.hidden = !(r && st.mode !== "reviewer");
  if (r) stmt.textContent = r.statement;
  doc.innerHTML = st.html;
  decorate();
  drawOther();
  drawCounts();
  if (st.mode === "read") notice("Reading only. Start a review with `scrivmark start`, or open one with `scrivmark open <round>`.");
  else if (st.warnings.length) notice("pandoc: " + st.warnings.join(" "), 10000);
  requestAnimationFrame(buildMargin);
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
  for (const a of $$('a[href^="#ref-"]', doc)) {
    const ref = document.getElementById(a.getAttribute("href").slice(1));
    if (ref) a.title = ref.textContent.trim().replace(/\s+/g, " ");
  }
  if (editable()) {
    for (const b of $$(".scrv-b", doc)) {
      b.prepend(el("button", { class: "edit", title: "Edit this passage's Markdown", "aria-label": "Edit this passage",
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
  let text = "", n;
  const map = [];
  while ((n = walker.nextNode())) {
    for (let i = 0; i < n.data.length; i++) {
      const ch = /\s/.test(n.data[i]) ? " " : n.data[i];
      if (ch === " " && text.endsWith(" ")) continue;
      text += ch;
      map.push([n, i]);
    }
  }
  const nth = (needle, k) => { let i = -1; for (let j = 0; j <= k; j++) { i = text.indexOf(needle, i + 1); if (i < 0) return -1; } return i; };
  let needle = plainQuote(t.current || t.anchor.exact);
  let at = needle ? nth(needle, t.nth || 0) : -1;
  if (at < 0 && needle.length > 30) { needle = needle.slice(0, 30); at = nth(needle, t.nth || 0); }
  if (at < 0) { block.classList.add("block-hl"); t._mark = block; return; }
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
  const focusKey = S.focus;
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
  }
  if (focusKey) {
    const ta = $(`textarea[data-key="${focusKey}"]`, margin);
    if (ta) { ta.focus({ preventScroll: true }); ta.setSelectionRange(ta.value.length, ta.value.length); }
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
  const q = plainQuote(it.current ?? it.anchor.exact);
  if (!q) return el("div", { class: "quote" }, "Where text was removed");
  return el("div", { class: "quote", title: q }, `“${q}”`);
}

function threadCard(t) {
  const orphan = !t.line;
  const c = el("div", { class: `card ${t.status}${orphan ? " orphan" : ""}`, tabindex: "-1" },
    orphan ? el("div", { class: "tag" }, "Lost its place: the passage was changed or removed") : null,
    quoteLine(t),
    ...t.messages.map(m => el("div", { class: "msg" }, el("span", { class: "by" }, m.by), m.body)));
  if (!S.st.round || S.st.mode === "closed") return c;
  c.append(el("div", { class: "acts" },
    el("button", { onclick: e => { e.stopPropagation(); replyBox(c, t); } }, "Reply"),
    t.status === "open"
      ? el("button", { onclick: e => { e.stopPropagation(); setThread(t.id, "resolved"); } }, "Resolve")
      : el("button", { onclick: e => { e.stopPropagation(); setThread(t.id, "open"); } }, "Reopen")));
  return c;
}

function draftCard(d) {
  const c = el("div", { class: "card draft", tabindex: "-1" },
    el("div", { class: "tag" }, "Draft from your agent"),
    quoteLine(d),
    el("div", { class: "msg" }, d.body),
    d.wording !== null ? el("div", { class: "wording", title: "Applied as an edit if kept" }, `→ ${d.wording || "(delete the passage)"}`) : null);
  c.append(el("div", { class: "acts" },
    el("button", { class: "primary", onclick: e => { e.stopPropagation(); triage(d.id, "keep"); } }, d.wording !== null ? "Keep & apply" : "Keep"),
    el("button", { onclick: e => { e.stopPropagation(); editDraft(c, d); } }, "Edit"),
    el("button", { onclick: e => { e.stopPropagation(); triage(d.id, "drop"); } }, "Drop")));
  return c;
}

function askCard(a) {
  const c = el("div", { class: "card ask", tabindex: "-1" },
    el("div", { class: "tag" }, a.answer ? "Your agent answered" : "Question for your agent"),
    quoteLine(a),
    el("div", { class: "msg" }, el("span", { class: "by" }, "You"), a.question),
    a.answer ? el("div", { class: "msg answer" }, el("span", { class: "by" }, "Agent"), a.answer)
      : el("div", { class: "muted small" }, "Not answered yet. In the chat, ask your agent to “check my asks”."));
  c.append(el("div", { class: "acts" },
    a.answer ? el("button", { class: "primary", onclick: e => { e.stopPropagation(); askComment(c, a); } }, "Comment from this") : null,
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
      delete S.compose[key]; S.focus = null;
    } catch (e) { fail(e); }
  };
  ta.addEventListener("keydown", e => { if (e.key === "Enter" && (e.metaKey || e.ctrlKey)) send(); if (e.key === "Escape") closeComposer(key); });
  card.append(el("div", { class: "tag" }, "Comment for the author (edit before sending)"), ta,
    el("div", { class: "acts" }, el("button", { class: "primary", onclick: send }, "Add comment"),
      el("button", { onclick: () => closeComposer(key) }, "Cancel")));
  if (!restoring) ta.focus();
  layoutMargin();
}

async function dismissAsk(id) {
  try { await api("/api/ask/update", { ask: id, done: true }); } catch (e) { fail(e); }
}

function composer(key, field, placeholder) {
  const ta = el("textarea", { placeholder, "data-key": `${key}:${field}` });
  ta.value = S.compose[key][field] ?? "";
  ta.addEventListener("input", () => { if (S.compose[key]) S.compose[key][field] = ta.value; });
  ta.addEventListener("focus", () => (S.focus = `${key}:${field}`));
  return ta;
}

function closeComposer(key) {
  delete S.compose[key];
  S.focus = null;
  buildMargin();
}

function replyBox(card, t, restoring = false) {
  if ($("textarea", card)) return;
  const key = `thread:${t.id}`;
  S.compose[key] = S.compose[key] || { text: "" };
  const ta = composer(key, "text", "Reply…");
  const send = async () => {
    if (!ta.value.trim()) return;
    try { await api("/api/reply", { thread: t.id, body: ta.value, rev: S.st.rev }); delete S.compose[key]; S.focus = null; } catch (e) { fail(e); }
  };
  ta.addEventListener("keydown", e => { if (e.key === "Enter" && (e.metaKey || e.ctrlKey)) send(); if (e.key === "Escape") closeComposer(key); });
  card.append(ta, el("div", { class: "acts" }, el("button", { class: "primary", onclick: send }, "Send"),
    el("button", { onclick: () => closeComposer(key) }, "Cancel")));
  if (!restoring) ta.focus();
  layoutMargin();
}

function editDraft(card, d, restoring = false) {
  if ($("textarea", card)) return;
  const key = `draft:${d.id}`;
  S.compose[key] = S.compose[key] || { text: d.body, wording: d.wording };
  const body = composer(key, "text", "Comment");
  const wording = d.wording !== null ? composer(key, "wording", "Wording to apply") : null;
  const keep = async () => {
    await triage(d.id, "keep", body.value, wording ? wording.value : undefined);
    delete S.compose[key];
    S.focus = null;
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

async function triage(id, action, body, wording) {
  try { await api("/api/draft", { draft: id, action, body, wording, rev: S.st.rev }); } catch (e) { fail(e); throw e; }
}

function setActive(id, scroll = true) {
  S.active = id;
  $$(".card", margin).forEach(c => c.classList.toggle("active", c.dataset.t === id));
  $$("mark.scrv-hl", doc).forEach(m => m.classList.toggle("active", m.dataset.t === id));
  const it = [...S.st.threads, ...S.st.drafts, ...S.st.asks].find(x => x.id === id);
  if (scroll && it && it._mark) it._mark.scrollIntoView({ block: "center", behavior: "smooth" });
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
  const send = async () => {
    if (!ta.value.trim()) return;
    try {
      const where = { file: s.file, lines: s.lines, text: s.text, before: s.before, key: s.key };
      if (act === "comment") await api("/api/comment", { ...where, body: ta.value, rev: S.st.rev });
      else { await api("/api/ask", { ...where, question: ta.value }); notice("Your question is in the margin. When you're ready, tell your agent: “check my asks”.", 7000); }
      closePop();
    } catch (err) { fail(err); }
  };
  ta.addEventListener("keydown", ev => { if (ev.key === "Enter" && (ev.metaKey || ev.ctrlKey)) send(); if (ev.key === "Escape") closePop(); });
  pop.replaceChildren(
    el("div", { class: "muted" }, act === "comment" ? "Comment on the selection" : "Ask your agent about the selection"),
    ta, el("div", { class: "acts" }, el("button", { class: "primary", onclick: send }, act === "comment" ? "Comment" : "Queue question"),
      el("button", { onclick: closePop }, "Cancel")));
  place(pop, s.rect);
  ta.focus();
});

// ------------------------------------------------------------------ editing

async function startEdit(block) {
  if (S.editing) return;
  let src;
  try { src = await api(`/api/source?file=${encodeURIComponent(block.dataset.file)}&lines=${block.dataset.lines}`); } catch (e) { return fail(e); }
  S.editing = block;
  const ta = el("textarea", { spellcheck: "true", "aria-label": "Markdown source" });
  ta.value = src.text;
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
    } catch (e) { notice(e.message, 12000); }
  }
  function cancel() { box.replaceWith(block); done(); }
  function done() { S.editing = null; load(true); }
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
document.addEventListener("keydown", e => {
  if (e.target.closest("textarea, input") || e.metaKey || e.ctrlKey || e.altKey) return;
  if (e.key === "j") step(1);
  if (e.key === "k") step(-1);
  if (e.key === "?") $("#helpbox").showModal();
});
addEventListener("resize", () => S.st && layoutMargin());

// ------------------------------------------------------------------ live updates

const es = new EventSource("/api/events");
es.onmessage = () => load();
es.onopen = () => { $("#conn").hidden = true; };
es.onerror = () => { $("#conn").hidden = false; };
load(true);
