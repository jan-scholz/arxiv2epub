-- Pandoc Lua filter that tidies LaTeXML HTML (arXiv's HTML view, or our own
-- latexml-oxide run) into something that reads well on an e-ink reader.

local function has_class(el, cls)
  for _, c in ipairs(el.classes) do
    if c == cls then return true end
  end
  return false
end

local function has_class_prefix(el, prefix)
  for _, c in ipairs(el.classes) do
    if c:sub(1, #prefix) == prefix then return true end
  end
  return false
end

-- Title and author block come from the EPUB metadata instead; section
-- headings move up one level so pandoc splits the book per section.
function Header(el)
  if has_class(el, "ltx_title_document") then return {} end
  if has_class(el, "ltx_title_abstract") then
    el.level = 1  -- give the abstract its own chapter/TOC entry
  elseif el.level > 1 then
    el.level = el.level - 1
  end
  return el
end

-- LaTeXML wraps display math in a layout table with the equation number in a
-- side cell; flatten each row into "math (tag)" on its own paragraph.
local function flatten_equation_table(tbl)
  local paras = {}
  local function rows_of(body)
    local out = {}
    for _, r in ipairs(body.body or {}) do table.insert(out, r) end
    return out
  end
  local bodies = {}
  for _, b in ipairs(tbl.bodies) do
    for _, r in ipairs(rows_of(b)) do table.insert(bodies, r) end
  end
  for _, row in ipairs(bodies) do
    local inlines = pandoc.Inlines({})
    local tag = nil
    for _, cell in ipairs(row.cells) do
      local blocks = pandoc.Blocks(cell.contents)
      blocks:walk({
        Math = function(m)
          inlines:insert(pandoc.Math("DisplayMath", m.text))
        end,
        Span = function(s)
          if has_class(s, "ltx_tag_equation") or has_class(s, "ltx_tag") then
            tag = pandoc.utils.stringify(s)
          end
        end,
      })
    end
    if #inlines > 0 then
      if tag then
        -- Rendered first so the CSS float puts it on the equation's line.
        inlines:insert(1, pandoc.Span(pandoc.Str(tag), { class = "eqn-tag" }))
      end
      table.insert(paras, pandoc.Para(inlines))
    end
  end
  return paras
end

function Table(el)
  if has_class(el, "ltx_equation") or has_class(el, "ltx_equationgroup") then
    local paras = flatten_equation_table(el)
    if #paras > 0 then return paras end
  end
  return el
end

function Div(el)
  if has_class(el, "ltx_authors") or has_class_prefix(el, "ltx_page_")
     or has_class(el, "ltx_TOC") or has_class(el, "ltx_ERROR") then
    return {}
  end
  -- Code listings arrive as one Div per line; turn them into a real CodeBlock.
  if has_class(el, "ltx_listing") then
    local lines = {}
    el:walk({
      Div = function(line)
        if has_class(line, "ltx_listingline") then
          table.insert(lines, pandoc.utils.stringify(line))
        end
      end,
    })
    if #lines > 0 then return pandoc.CodeBlock(table.concat(lines, "\n")) end
  end
  return el
end

-- Footnotes are nested spans; turn them into real pandoc notes so Kobo shows
-- them as pop-ups instead of inline clutter.
function Span(el)
  if has_class(el, "ltx_ERROR") then return {} end
  if has_class(el, "ltx_note") and not has_class(el, "ltx_note_frontmatter") then
    local content = nil
    el:walk({
      Span = function(s)
        if has_class(s, "ltx_note_content") and not content then content = s.content end
      end,
    })
    if content then
      local cleaned = pandoc.Inlines({})
      for i, inline in ipairs(content) do
        local skip = (i == 1 and inline.t == "Superscript")
          or (inline.t == "Span" and has_class(inline, "ltx_note_type"))
        if not skip then cleaned:insert(inline) end
      end
      return pandoc.Note({ pandoc.Para(cleaned) })
    end
  end
  return el
end

-- Let images scale to the screen instead of the fixed pt-derived size.
function Image(el)
  el.attributes.width = nil
  el.attributes.height = nil
  el.attributes.style = nil
  return el
end
