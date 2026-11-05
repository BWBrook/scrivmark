-- citeproc with a cache on disk. Parsing a large CSL style (Chicago 18th
-- edition is 168 KB) takes seconds and is repeated on every render, so the
-- result for a given set of citations, bibliography and style is kept and
-- reused. The key covers the document handed to citeproc (without scrivmark's
-- per-render metadata) and the contents of the bibliography and style files.

local M = {}
local stringify = pandoc.utils.stringify

local function slurp(path)
  local f = io.open(path, "rb")
  if not f then return nil end
  local s = f:read("a")
  f:close()
  return s
end
M.slurp = slurp

local function paths(meta)
  local out = {}
  local b = meta.bibliography
  if b then
    if pandoc.utils.type(b) == "List" then
      for _, x in ipairs(b) do out[#out + 1] = stringify(x) end
    else
      out[#out + 1] = stringify(b)
    end
  end
  if meta.csl then out[#out + 1] = stringify(meta.csl) end
  return out
end

-- Returns the processed document and whether it came from the cache.
function M.citeproc(doc, dir)
  local meta = {}
  for k, v in pairs(doc.meta) do
    if not k:match("^scrv%-") and k ~= "pagetitle" then meta[k] = v end
  end
  doc = pandoc.Pandoc(doc.blocks, pandoc.Meta(meta))
  if not dir or dir == "" then return pandoc.utils.citeproc(doc), false end
  local parts = { pandoc.write(doc, "json") }
  for _, p in ipairs(paths(doc.meta)) do parts[#parts + 1] = p .. "\0" .. (slurp(p) or "") end
  local file = dir .. "/" .. pandoc.utils.sha1(table.concat(parts, "\0")) .. ".json"
  local hit = slurp(file)
  if hit then return pandoc.read(hit, "json"), true end
  local out = pandoc.utils.citeproc(doc)
  local f = io.open(file .. ".tmp", "wb")
  if f then
    f:write(pandoc.write(out, "json"))
    f:close()
    os.rename(file .. ".tmp", file)
  end
  return out, false
end

function M.dir(meta)
  return meta["scrv-cites"] and stringify(meta["scrv-cites"]) or nil
end

return M
