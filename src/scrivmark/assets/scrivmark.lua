-- scrivmark render filter. Runs before citeproc.
--  * numbers Quarto-style cross-references (fig, tbl, eq, sec) in current text;
--  * keeps deleted material out of numbering and the bibliography, rendering
--    its citations separately;
--  * rebases image paths to the source file's directory under /files/;
--  * neutralises link and image targets with unsafe schemes.

local stringify = pandoc.utils.stringify
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
        labels[h.identifier] = { kind = "sec", title = stringify(h.content) }
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
      return img
    end,
    Link = function(l)
      l.target = safe_target(l.target)
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
  local ok, done = pcall(pandoc.utils.citeproc, pandoc.Pandoc(paras, m))
  if not ok then return doc end
  local rendered = {}
  for i, para in ipairs(done.blocks) do
    local first = para.content and para.content[1]
    if first and first.t == "Cite" then rendered[tostring(i)] = first.content end
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

function Pandoc(doc)
  doc.blocks = label_pass(doc.blocks, false)
  doc.blocks = refs_pass(doc.blocks, "", false)
  return render_deleted_cites(doc)
end
