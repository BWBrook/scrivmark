-- Citations and the reference list, in place of pandoc's --citeproc. Every
-- citation is processed in document order in a small document of its own,
-- whose result is cached (citecache.lua); the rendered citations and the
-- reference list are then put back. Editing prose leaves that small document
-- unchanged, so the style is not parsed again. Note styles move citations
-- into footnotes, which depends on the surrounding text, so they are handed
-- to citeproc whole.

local cache = dofile(pandoc.path.join({ pandoc.path.directory(PANDOC_SCRIPT_FILE), "citecache.lua" }))
local stringify = pandoc.utils.stringify

local function note_style(meta)
  if not meta.csl then return false end
  local xml = cache.slurp(stringify(meta.csl)) or ""
  return xml:match("<style[^>]-class=[\"']note[\"']") ~= nil
end

function Pandoc(doc)
  if note_style(doc.meta) then return (cache.citeproc(doc, nil)) end
  local cites, has_refs = {}, false
  doc:walk({
    Cite = function(c) cites[#cites + 1] = c end,
    Div = function(d) if d.identifier == "refs" then has_refs = true end end,
  })
  local blocks = pandoc.List({})
  for _, c in ipairs(cites) do blocks:insert(pandoc.Para({ c })) end
  local last = doc.blocks[#doc.blocks]
  local final_header = not has_refs and last and last.t == "Header"
  if has_refs then
    blocks:insert(pandoc.Div({}, pandoc.Attr("refs")))
  elseif final_header then
    blocks:insert(last)
  end
  local out, cached = cache.citeproc(pandoc.Pandoc(blocks, doc.meta), cache.dir(doc.meta))

  local rendered = {}
  for i = 1, #cites do
    local para = out.blocks[i]
    local c = para and para.t == "Para" and #para.content == 1 and para.content[1]
    if not c or c.t ~= "Cite" then
      io.stderr:write("scrivmark: citations processed without the cache (unexpected citeproc output)\n")
      return (cache.citeproc(doc, nil))
    end
    rendered[i] = c
  end
  local tail = pandoc.List({})
  for i = #cites + 1, #out.blocks do tail:insert(out.blocks[i]) end

  if cached then
    local listed = {}
    pandoc.Pandoc(tail):walk({ Div = function(d) local k = d.identifier:match("^ref%-(.*)$"); if k then listed[k] = true end end })
    local warned = {}
    if next(listed) or not doc.meta["suppress-bibliography"] then
      for _, c in ipairs(cites) do
        for _, ct in ipairs(c.citations) do
          if not listed[ct.id] and not warned[ct.id] then
            warned[ct.id] = true
            io.stderr:write("[WARNING] Citeproc: citation " .. ct.id .. " not found\n")
          end
        end
      end
    end
  end

  local n = 0
  doc = doc:walk({ Cite = function() n = n + 1; return rendered[n] end })
  if has_refs then
    doc = doc:walk({ Div = function(d) if d.identifier == "refs" then return tail end end })
  else
    if final_header then doc.blocks:remove(#doc.blocks) end
    doc.blocks:extend(tail)
  end
  return doc
end
