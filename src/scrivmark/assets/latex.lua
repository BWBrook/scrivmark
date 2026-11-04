-- scrivmark LaTeX filter. Runs before scrivmark.lua.
--  * reads each `scrv-tex` code block as LaTeX, with the definitions from the
--    file's preamble (carried in the preceding `scrv-texdefs` block);
--  * turns redline marks (\textcolor{scrvdel-<nonce>-<change>}{...}) into
--    scrv-del / scrv-ins spans, and % comments into unprinted notes;
--  * renames LaTeX labels to scrivmark's fig-/tbl-/eq-/sec- identifiers and
--    \ref links to cross-reference citations, so numbering is shared;
--  * replaces each tikzpicture with an image the viewer draws on demand.

local defs, tikz_full, tikz_min = "", "", ""
local cache = nil
local nonce = ""
local labels = {}

local function hex(s)
  return (s:gsub(".", function(c) return string.format("%02x", c:byte()) end))
end

local function unhex(h)
  return (h:gsub("%x%x", function(x) return string.char(tonumber(x, 16)) end))
end

local function split_comment(line)
  local i = 1
  while i <= #line do
    local c = line:sub(i, i)
    local delim = line:match("^\\verb%*?(%p)", i)
    if delim then
      local close = line:find(delim, i + #line:match("^\\verb%*?%p", i), true)
      i = (close or #line) + 1
    elseif c == "\\" then
      i = i + 2
    elseif c == "%" then
      return line:sub(1, i - 1), line:sub(i + 1)
    else
      i = i + 1
    end
  end
  return line, nil
end

local VERBATIM = { verbatim = true, ["verbatim*"] = true, lstlisting = true, minted = true, Verbatim = true,
  comment = true, filecontents = true, ["filecontents*"] = true }

local function note(text)
  return "\\textcolor{scrvnote-" .. hex(text) .. "}{}"
end

-- A % comment ends the line and swallows the newline, as in TeX. Runs of
-- whole-line comments become one note.
local function uncomment(src)
  local out, pending, join = {}, {}, false
  local function flush()
    if #pending > 0 then
      table.insert(out, note(table.concat(pending, "\n")) .. "\n")
      pending = {}
    end
  end
  local verbatim = nil
  for line in (src .. "\n"):gmatch("(.-)\n") do
    local code, comment
    if verbatim then
      code, comment = line, nil
      if line:find("\\end%s*{" .. verbatim .. "}") then verbatim = nil end
    else
      code, comment = split_comment(line)
      local env = code:match("\\begin%s*{(%a+%*?)}")
      if env and VERBATIM[env] and not code:find("\\end%s*{" .. env:gsub("%*", "%%*") .. "}") then
        verbatim = env:gsub("%*", "%%*")
        code, comment = line, nil
      end
    end
    if join then code = code:gsub("^%s+", "") end
    if comment and not code:match("%S") then
      local text = comment:gsub("^%s+", ""):gsub("%s+$", "")
      if text ~= "" then table.insert(pending, text) end
      join = true
    else
      flush()
      if comment then
        local text = comment:gsub("^%s+", ""):gsub("%s+$", "")
        table.insert(out, code .. (text ~= "" and note(text) or ""))
        join = true
      else
        -- a blank line after a swallowed newline still ends the paragraph
        table.insert(out, code .. ((join and not code:match("%S")) and "\n\n" or "\n"))
        join = false
      end
    end
  end
  flush()
  return table.concat(out)
end

local function tikz(src)
  return (src:gsub("\\begin%s*{tikzpicture}.-\\end%s*{tikzpicture}", function(body)
    local key = pandoc.utils.sha1(tikz_full .. body)
    if cache then
      for _, pair in ipairs({ { "", tikz_full }, { ".min", tikz_min } }) do
        local path = cache .. "/" .. key .. pair[1] .. ".tex"
        local f = io.open(path, "r")
        if f then
          f:close()
        else
          f = io.open(path, "w")
          if f then
            f:write(pair[2], "\\begin{document}\n", body, "\n\\end{document}\n")
            f:close()
          end
        end
      end
    end
    return "\\includegraphics{/scrv-tikz/" .. key .. ".svg}"
  end))
end

local function prepare(src)
  src = uncomment(tikz(src))
  src = src:gsub("\\input%s*%b{}", ""):gsub("\\include%s*%b{}", ""):gsub("\\subfile%s*%b{}", "")
  src = src:gsub("\\input%s+[^%s{}\\]+", "")
  -- the manuscript must not pull other files into the page
  for _, cmd in ipairs({ "lstinputlisting", "verbatiminput", "VerbatimInput", "inputminted", "includepdf" }) do
    src = src:gsub("\\" .. cmd .. "%s*%b[]", "\\" .. cmd):gsub("\\" .. cmd .. "%s*%b{}%s*%b{}", function(m)
      return cmd == "inputminted" and "\\texttt{[file listing]}" or nil
    end):gsub("\\" .. cmd .. "%s*(%b{})", function(arg) return "\\texttt{[file " .. arg:sub(2, -2) .. "]}" end)
  end
  -- older pandoc reads \cref as \ref; keep the difference in the label
  src = src:gsub("\\[cC]ref%s*{", "\\ref{scrvcref:"):gsub("\\autoref%s*{", "\\ref{scrvcref:")
  return src
end

local KIND = { Figure = "fig", Table = "tbl", Header = "sec" }

local function relabel(el)
  local k = KIND[el.t]
  if k and el.identifier ~= "" then
    if el.identifier:match("^" .. k .. "%-") then
      labels[el.identifier] = el.identifier
      return nil
    end
    local id = k .. "-" .. el.identifier
    labels[el.identifier] = id
    el.identifier = id
    return el
  end
end

local function equations(para)
  local out, changed = pandoc.Inlines({}), false
  for _, x in ipairs(para.content) do
    out:insert(x)
    if x.t == "Math" and x.mathtype == "DisplayMath" then
      local label = x.text:match("\\label%s*{([^}]*)}")
      if label then
        -- one number per display, as the viewer shows it
        for other in x.text:gmatch("\\label%s*{([^}]*)}") do labels[other] = "eq-" .. label end
        x.text = x.text:gsub("\\label%s*%b{}", "")
        out:insert(pandoc.Space())
        out:insert(pandoc.Str("{#eq-" .. label .. "}"))
        changed = true
      end
    end
  end
  if changed then para.content = out; return para end
end

-- {\rm x} and friends are valid TeX but not understood by pandoc's maths reader.
local FONTS = { rm = "mathrm", bf = "mathbf", it = "mathit", sf = "mathsf", tt = "mathtt", cal = "mathcal" }

local function modern_fonts(m)
  local text = m.text:gsub("{\\(%a+)%s+([^{}]*)}", function(sw, body)
    if FONTS[sw] then return "\\" .. FONTS[sw] .. "{" .. body .. "}" end
  end)
  if text ~= m.text then m.text = text; return m end
end

local function marks(sp)
  local style = sp.attributes["style"] or ""
  local kind, rest = style:match("^color:%s*scrv(%a+)%-(.*)$")
  if not kind then return nil end
  if kind == "note" then
    return pandoc.Span({ pandoc.Str(unhex(rest)) }, pandoc.Attr("", { "scrv-note" }, { n = nonce }))
  end
  if kind == "del" or kind == "ins" then
    local nonce, cid = rest:match("^(%x*)%-(.*)$")
    local attrs = { n = nonce or "" }
    if cid and cid ~= "" then attrs.c = cid end
    return pandoc.Span(sp.content, pandoc.Attr("", { "scrv-" .. kind }, attrs))
  end
end

local function only_notes(para)
  local spans = pandoc.List({})
  for _, x in ipairs(para.content) do
    if x.t == "Span" and x.classes:includes("scrv-note") and x.attributes["n"] == nonce then
      spans:insert(x)
    elseif x.t ~= "Space" and x.t ~= "SoftBreak" then
      return nil
    end
  end
  if #spans == 0 then return nil end
  local paras = {}
  for _, sp in ipairs(spans) do table.insert(paras, pandoc.Para(sp.content)) end
  return pandoc.Div(paras, pandoc.Attr("", { "scrv-note" }, { n = nonce }))
end

local function read(src)
  local ok, doc = pcall(pandoc.read, defs .. "\n" .. prepare(src), "latex", nil, {})
  if not ok then
    return { pandoc.CodeBlock(src, { class = "latex" }) }
  end
  local blocks = doc.blocks:walk({ Span = marks, Figure = relabel, Table = relabel, Header = relabel, Para = equations,
    Math = modern_fonts })
  return blocks:walk({ Para = only_notes })
end

local convert = {
  traverse = "topdown",
  CodeBlock = function(cb)
    if cb.classes:includes("scrv-texdefs") then
      local d, full, min = cb.text:match("^(.-)\n%%%%scrv%-tikz\n(.-)\n%%%%scrv%-tikz%-min\n(.*)$")
      defs, tikz_full, tikz_min = d or cb.text, full or "", min or ""
      return {}
    end
    if cb.classes:includes("scrv-tex") then
      return read(cb.text)
    end
  end,
}

local function reference(l)
  local kind = l.attributes["reference-type"]
  local target = l.attributes["reference"]
  if not kind or not target then return nil end
  local named = kind == "ref+label" or kind == "ref+Label" or target:match("^scrvcref:") ~= nil
  target = target:gsub("^scrvcref:", "")
  local citations = {}
  for t in target:gmatch("[^,%s]+") do
    table.insert(citations, pandoc.Citation(labels[t] or ("sec-" .. t), named and "NormalCitation" or "SuppressAuthor"))
  end
  local cite = pandoc.Cite({ pandoc.Str(target) }, citations)
  if kind == "eqref" then
    return { pandoc.Str("("), cite, pandoc.Str(")") }
  end
  return cite
end

function Pandoc(doc)
  cache = doc.meta["scrv-cache"] and pandoc.utils.stringify(doc.meta["scrv-cache"]) or nil
  nonce = doc.meta["scrv-nonce"] and pandoc.utils.stringify(doc.meta["scrv-nonce"]) or ""
  doc.blocks = doc.blocks:walk(convert)
  doc.blocks = doc.blocks:walk({ Link = reference })
  return doc
end
