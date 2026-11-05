-- scrivmark render filter. Runs before citations.lua.
--  * numbers Quarto-style cross-references (fig, tbl, eq, sec) in current text;
--  * keeps deleted material out of numbering and the bibliography, rendering
--    its citations separately;
--  * rebases image paths to the source file's directory under /files/;
--  * neutralises link and image targets with unsafe schemes.

local stringify = pandoc.utils.stringify
local cache = dofile(pandoc.path.join({ pandoc.path.directory(PANDOC_SCRIPT_FILE), "citecache.lua" }))
local nonce = ""
local TRUSTED_KEYS = { file = true, lines = true, c = true }
local SAFE_KEYS = { width = true, height = true, title = true, lang = true, dir = true }
local labels = {}
local counts = { fig = 0, tbl = 0, eq = 0 }
local names = { fig = "Figure", tbl = "Table", eq = "Equation", sec = "Section" }

local function has_class(el, c)
  for _, x in ipairs(el.classes or el.attr and el.attr.classes or {}) do
    if x == c then return true end
  end
  return false
end

local function kind_of(id)
  return id and id:match("^(%a+)%-") or nil
end

local function safe_target(t)
  local scheme = t:match("^(%a[%w+.-]*):")
  if scheme and not ({ http = 1, https = 1, mailto = 1 })[scheme:lower()] then
    return "#"
  end
  return t
end

local function rebase(src, dir)
  if src:match("^%a[%w+.-]*:") or src:match("^/") or src:match("^#") then return safe_target(src) end
  local path = (dir ~= "" and (dir .. "/") or "") .. src
  local parts = {}
  for seg in path:gmatch("[^/]+") do
    if seg == ".." then table.remove(parts) elseif seg ~= "." then table.insert(parts, seg) end
  end
  return "/files/" .. table.concat(parts, "/")
end

local function dirname(f)
  return f and f:match("^(.*)/[^/]*$") or ""
end

-- pass 0: only scrivmark's own wrappers (carrying this render's nonce) may
-- claim source locations or scrv- classes; manuscript attributes are reduced
-- to a safe allowlist, so nothing in the manuscript can script the viewer.

local function clean(el)
  if not el.attr then return nil end
  local trusted = el.attributes["n"] == nonce and nonce ~= ""
  local classes = pandoc.List({})
  for _, c in ipairs(el.classes) do
    if trusted or not c:match("^scrv%-") then classes:insert(c) end
  end
  local kept = {}
  for k, v in pairs(el.attributes) do
    if (trusted and TRUSTED_KEYS[k]) or SAFE_KEYS[k] then kept[k] = v end
  end
  el.attr = pandoc.Attr(el.identifier, classes, kept)
  return el
end

local sanitize = {
  Div = clean, Span = clean, Header = clean, CodeBlock = clean, Code = clean,
  Image = clean, Link = clean, Figure = clean, Table = clean,
}

-- pass 1: labels ---------------------------------------------------------

local function eq_label(para)
  local c = para.content
  for i = 1, #c do
    if c[i].t == "Math" and c[i].mathtype == "DisplayMath" then
      local j = i + 1
      if c[j] and (c[j].t == "Space" or c[j].t == "SoftBreak") then j = j + 1 end
      if c[j] and c[j].t == "Str" then
        local id = c[j].text:match("^{#(eq%-[^}]+)}$")
        if id then return i, j, id end
      end
    end
  end
end

local function tbl_label(tbl)
  if tbl.identifier and tbl.identifier:match("^tbl%-") then return tbl.identifier end
  local long = tbl.caption.long
  local last = long[#long]
  if not last or not last.content then return nil end
  local c = last.content
  local id = c[#c] and c[#c].t == "Str" and c[#c].text:match("^{#(tbl%-[^}]+)}$")
  if id then
    c:remove(#c)
    if c[#c] and (c[#c].t == "Space" or c[#c].t == "SoftBreak") then c:remove(#c) end
    return id
  end
end

local function label_pass(blocks, old)
  return blocks:walk({
    traverse = "topdown",
    Div = function(d)
      if has_class(d, "scrv-old") then
        d.content = label_pass(d.content, true)
        return d, false
      end
    end,
    Figure = function(f)
      local k = kind_of(f.identifier)
      if old then f.identifier = ""; return f end
      if k == "fig" and not labels[f.identifier] then
        counts.fig = counts.fig + 1
        labels[f.identifier] = { kind = "fig", n = counts.fig }
        local cap = f.caption.long[1]
        if cap and cap.content then
          cap.content:insert(1, pandoc.Space())
          cap.content:insert(1, pandoc.Strong({ pandoc.Str("Figure " .. counts.fig .. ":") }))
        end
      end
      return f
    end,
    Table = function(t)
      local id = tbl_label(t)
      if id and not old then
        counts.tbl = counts.tbl + 1
        labels[id] = { kind = "tbl", n = counts.tbl }
        t.identifier = id
        local long = t.caption.long
        if long[1] and long[1].content then
          long[1].content:insert(1, pandoc.Space())
          long[1].content:insert(1, pandoc.Strong({ pandoc.Str("Table " .. counts.tbl .. ":") }))
        end
      end
      return t
    end,
    Para = function(p)
      local i, j, id = eq_label(p)
      if not id then return nil end
      local math = p.content[i]
      for _ = i + 1, j do p.content:remove(i + 1) end
      if old then return p end
      counts.eq = counts.eq + 1
      labels[id] = { kind = "eq", n = counts.eq }
      p.content[i] = pandoc.Span({ math, pandoc.Span({ pandoc.Str("(" .. counts.eq .. ")") }, { class = "scrv-eqno" }) },
        { id = id, class = "scrv-eq" })
      return p
    end,
    Header = function(h)
      if not old and kind_of(h.identifier) == "sec" then
        local current = h.content:walk({ Span = function(s) if has_class(s, "scrv-del") then return {} end end })
        labels[h.identifier] = { kind = "sec", title = stringify(current) }
      end
    end,
  })
end

-- pass 2: references, deleted citations, paths ----------------------------

local deleted_cites = {}

local function is_xref(cite)
  for _, c in ipairs(cite.citations) do
    if not names[kind_of(c.id) or ""] then return false end
  end
  return true
end

local function xref_inlines(cite)
  local out = pandoc.Inlines({})
  for idx, c in ipairs(cite.citations) do
    if idx > 1 then out:insert(pandoc.Str(";")); out:insert(pandoc.Space()) end
    local l = labels[c.id]
    local text
    if l and l.kind == "sec" then
      text = l.title
    elseif l and c.mode == "SuppressAuthor" then
      text = tostring(l.n)
    elseif l then
      text = names[l.kind] .. " " .. l.n
    else
      text = "??"
    end
    if l then
      out:insert(pandoc.Link({ pandoc.Str(text) }, "#" .. c.id, "", { class = "scrv-xref" }))
    else
      out:insert(pandoc.Span({ pandoc.Str(text) }, { class = "scrv-unresolved", title = c.id }))
    end
  end
  return out
end

local function inline_filter(dir, old)
  return {
    Cite = function(c)
      if is_xref(c) then return xref_inlines(c) end
      if old then
        table.insert(deleted_cites, c)
        return pandoc.Span({ pandoc.Str(stringify(c.content)) }, { class = "scrv-oldcite", k = tostring(#deleted_cites) })
      end
    end,
    Image = function(img)
      img.src = rebase(img.src, dir)
      local name = img.src:match("([^/]*)$") or ""
      if img.src:match("^/files/") and (name:match("%.pdf$") or name:match("%.eps$") or not name:match("%.")) then
        img.src = "/png/" .. img.src:sub(8) .. ".png"
      end
      return img
    end,
    Link = function(l)
      if l.target:match("^#") then return l end
      l.target = rebase(l.target, dir)
      return l
    end,
  }
end

local function refs_pass(blocks, dir, old)
  return blocks:walk({
    traverse = "topdown",
    Div = function(d)
      local f = d.attributes["file"]
      local is_old = old or has_class(d, "scrv-old")
      if f or has_class(d, "scrv-old") then
        d.content = refs_pass(d.content, f and dirname(f) or dir, is_old)
        return d, false
      end
    end,
    Span = function(s)
      if has_class(s, "scrv-del") then
        return s:walk(inline_filter(dir, true)), false
      end
    end,
    Cite = inline_filter(dir, old).Cite,
    Image = inline_filter(dir, old).Image,
    Link = inline_filter(dir, old).Link,
  })
end

local function render_deleted_cites(doc)
  if #deleted_cites == 0 or not doc.meta.bibliography then return doc end
  local paras = {}
  for _, cite in ipairs(deleted_cites) do
    table.insert(paras, pandoc.Para({ cite }))
  end
  local m = {}
  for k, v in pairs(doc.meta) do m[k] = v end
  m["suppress-bibliography"] = true
  m = pandoc.Meta(m)
  local ok, done = pcall(cache.citeproc, pandoc.Pandoc(paras, m), cache.dir(doc.meta))
  if not ok then io.stderr:write("scrivmark: deleted citations not rendered: " .. tostring(done) .. "\n"); return doc end
  local rendered = {}
  for i, para in ipairs(done.blocks) do
    local first = para.content and para.content[1]
    if first and first.t == "Cite" then
      rendered[tostring(i)] = first.content:walk({ Link = function(l) return l.content end })
    end
  end
  return doc:walk({
    Span = function(sp)
      if has_class(sp, "scrv-oldcite") and rendered[sp.attributes.k] then
        sp.content = rendered[sp.attributes.k]
        return sp
      end
    end,
  })
end

-- HTML comments: raw HTML is off, so pandoc reads `<!-- ... -->` as text
-- (with smart punctuation, `<!–` ... `–>`). Show each as a muted note: the
-- reviewer sees what the author left in the source, marked as not printed.

local function opens(s) return s:match("^<!%-%-(.*)$") or s:match("^<!–(.*)$") end
local function closes(s) return s:match("^(.*)%-%->$") or s:match("^(.*)–>$") end

local function comment_inlines(inl)
  local out, i = pandoc.List({}), 1
  while i <= #inl do
    local x = inl[i]
    local rest = x.t == "Str" and opens(x.text)
    local j = nil
    if rest then
      for k = i, #inl do
        local y = inl[k]
        local s = (k == i) and rest or (y.t == "Str" and y.text or nil)
        if s and closes(s) then j = k break end
      end
    end
    if j then
      local body = pandoc.List({})
      for k = i, j do body:insert(inl[k]:clone()) end
      local first = (i == j) and closes(rest) or rest
      if first == "" then body:remove(1) else body[1] = pandoc.Str(first) end
      if i ~= j then
        local last = closes(body[#body].text)
        if last == "" then body:remove(#body) else body[#body] = pandoc.Str(last) end
      end
      while #body > 0 and (body[1].t == "Space" or body[1].t == "SoftBreak") do body:remove(1) end
      while #body > 0 and (body[#body].t == "Space" or body[#body].t == "SoftBreak") do body:remove(#body) end
      out:insert(pandoc.Span(body, pandoc.Attr("", {"scrv-note"})))
      i = j + 1
    else
      out:insert(x)
      i = i + 1
    end
  end
  return out
end

local function comment_block(blk)
  local inl = comment_inlines(blk.content)
  if #inl == 1 and inl[1].t == "Span" and has_class(inl[1], "scrv-note") then
    return pandoc.Div({pandoc.Para(inl[1].content)}, pandoc.Attr("", {"scrv-note"}))
  end
  blk.content = inl
  return blk
end

local comments = { Para = comment_block, Plain = comment_block }

function Pandoc(doc)
  nonce = doc.meta["scrv-nonce"] and stringify(doc.meta["scrv-nonce"]) or ""
  doc = doc:walk(sanitize)
  doc = doc:walk(comments)
  doc.blocks = label_pass(doc.blocks, false)
  doc.blocks = refs_pass(doc.blocks, "", false)
  return render_deleted_cites(doc)
end
