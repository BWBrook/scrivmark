"use strict";
// The reading tab: the manuscript as it now reads, every edit in and nothing
// marked, beside the review tab. It follows the review tab's place, and a
// click on a passage takes the review tab there.

const $ = (s, r = document) => r.querySelector(s);
const $$ = (s, r = document) => Array.from(r.querySelectorAll(s));
const doc = $("#doc"), follow = $("#follow");
const link = new BroadcastChannel("scrivmark-follow");
let key = null, busyTimer = null;

try { follow.checked = localStorage.getItem("scrivmark-follow") !== "off"; } catch { /* storage may be blocked */ }
follow.addEventListener("change", () => {
  try { localStorage.setItem("scrivmark-follow", follow.checked ? "on" : "off"); } catch { /* ignore */ }
});

function busy(on) {
  clearTimeout(busyTimer);
  if (on) busyTimer = setTimeout(() => { $("#busy").hidden = false; $(".busylabel").textContent = "Updating the page…"; }, 250);
  else $("#busy").hidden = true;
}

function spans(b) {
  const [a, z] = (b.dataset.lines || "0-0").split("-").map(Number);
  return [a, z];
}

function blockAt(file, line) {
  const all = $$(`.scrv-b[data-file="${CSS.escape(file)}"]`, doc);
  return all.find(b => { const [a, z] = spans(b); return a <= line && line <= z; })
    || all.filter(b => spans(b)[0] <= line).pop() || null;
}

function topBlock() {
  return $$(".scrv-b", doc).find(x => x.getBoundingClientRect().bottom > 60) || null;
}

async function load() {
  busy(true);
  let st;
  try {
    const r = await fetch("/api/clean");
    st = await r.json();
    if (!r.ok) throw new Error(st.error || `HTTP ${r.status}`);
  } catch (e) {
    busy(false);
    key = null;
    doc.replaceChildren(Object.assign(document.createElement("p"), { className: "loading", textContent: `The manuscript could not be loaded: ${e.message}` }));
    return;
  }
  busy(false);
  if (st.key === key) return;
  const b = topBlock();
  const keep = b ? { file: b.dataset.file, line: spans(b)[0], top: b.getBoundingClientRect().top } : null;
  key = st.key;
  $("#roundid").textContent = st.round ? `round ${st.round}` : "";
  doc.innerHTML = st.html;
  for (const img of $$("img", doc)) img.loading = "lazy";
  if (keep) {
    const again = blockAt(keep.file, keep.line);
    if (again) scrollBy(0, again.getBoundingClientRect().top - keep.top);
  }
}

link.onmessage = e => {
  const m = e.data;
  if (!m || m.from !== "review" || !follow.checked) return;
  const b = blockAt(m.file, m.line);
  if (!b) return;
  if (m.kind === "scroll") scrollBy(0, b.getBoundingClientRect().top - m.top);
  else {
    b.scrollIntoView({ block: "center", behavior: "smooth" });
    b.classList.add("followed");
    setTimeout(() => b.classList.remove("followed"), 1200);
  }
};

doc.addEventListener("click", e => {
  if (e.target.closest("a")) return;
  const b = e.target.closest(".scrv-b");
  if (!b || !b.dataset.file) return;
  link.postMessage({ from: "read", kind: "go", file: b.dataset.file, line: spans(b)[0] });
});

const es = new EventSource("/api/events");
es.onmessage = () => load();
es.onopen = () => { $("#conn").hidden = true; };
es.onerror = () => { $("#conn").hidden = false; };
load();
