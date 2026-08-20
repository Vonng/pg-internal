-- Normalize the Hugo Relearn HTML emitted by interdb.jp into portable Pandoc AST.

local function has_class(el, wanted)
  for _, class in ipairs(el.classes or {}) do
    if class == wanted then
      return true
    end
  end
  return false
end

local function plain_label(blocks)
  local value = pandoc.utils.stringify(blocks or {})
  value = value:gsub("^%s+", ""):gsub("%s+$", "")
  return value
end

local function inline_source(inlines)
  local parts = {}
  for _, inline in ipairs(inlines or {}) do
    if inline.t == "Str" then
      table.insert(parts, inline.text)
    elseif inline.t == "Space" then
      table.insert(parts, " ")
    elseif inline.t == "SoftBreak" or inline.t == "LineBreak" then
      table.insert(parts, "\n")
    elseif inline.t == "Code" or inline.t == "RawInline" then
      table.insert(parts, inline.text)
    else
      table.insert(parts, pandoc.utils.stringify(inline))
    end
  end
  return table.concat(parts)
end

function Div(el)
  if has_class(el, "headline") or has_class(el, "footline") then
    return {}
  end

  if has_class(el, "wrap-code") or has_class(el, "box-content") then
    return el.content
  end

  if has_class(el, "box-label") then
    return el.content
  end

  if has_class(el, "notices") then
    local label = "Note"
    local body = pandoc.List:new()
    local first_content_block = true

    for _, block in ipairs(el.content) do
      if block.t == "Div" and has_class(block, "box-label") then
        local candidate = plain_label(block.content)
        if candidate ~= "" then
          label = candidate
        end
      elseif block.t == "Div" and has_class(block, "box-content") then
        body:extend(block.content)
      elseif first_content_block and (block.t == "Plain" or block.t == "Para") then
        -- Pandoc walks child divs before their parents, so an unwrapped
        -- .box-label reaches us as the first plain paragraph.
        local candidate = plain_label({block})
        if candidate ~= "" then
          label = candidate
        end
      else
        body:insert(block)
      end
      first_content_block = false
    end

    local quoted = pandoc.List:new({
      pandoc.Para({
        pandoc.Strong({
          pandoc.Str(label:gsub("[%s%.:]+$", "") .. ":")
        })
      })
    })
    quoted:extend(body)
    return pandoc.BlockQuote(quoted)
  end

  if has_class(el, "expand") then
    local label = "Details"
    local body = pandoc.List:new()
    local label_found = false
    for _, block in ipairs(el.content) do
      if block.t == "Div" and has_class(block, "expand-content") then
        body:extend(block.content)
      elseif not label_found and (block.t == "Plain" or block.t == "Para") then
        local candidate = plain_label({block})
        if candidate ~= "" then
          label = candidate
          label_found = true
        else
          body:insert(block)
        end
      else
        body:insert(block)
      end
    end
    local expanded = pandoc.List:new({
      pandoc.Para({pandoc.Strong({pandoc.Str(label)})})
    })
    expanded:extend(body)
    return expanded
  end

  if has_class(el, "expand-content") then
    return el.content
  end

  -- Relearn uses divs for layout, columns, badges, code wrappers, and controls.
  -- The semantic content is in their children; theme classes are not portable.
  return el.content
end

function CodeBlock(el)
  local language = nil
  for _, class in ipairs(el.classes or {}) do
    local candidate = class:gsub("^language%-", "")
    if candidate ~= "highlight" and candidate ~= "chroma" and candidate ~= "" then
      language = candidate:lower()
      break
    end
  end

  if language then
    el.classes = {language}
  else
    -- A class forces the GFM writer to use a fenced code block instead of
    -- four-space indentation, which is clearer and easier for Hugo to render.
    el.classes = {"text"}
  end
  el.identifier = ""
  el.attributes = {}
  return el
end

function Link(el)
  el.identifier = ""
  el.classes = {}
  el.attributes = {}
  return el
end

function Image(el)
  el.identifier = ""
  el.classes = {}
  el.attributes = {}
  return el
end

function Span(el)
  if has_class(el, "math") then
    -- The source already carries KaTeX-compatible $...$ or $$...$$
    -- delimiters. Preserve that text instead of letting GFM escape it.
    local source = inline_source(el.content)
    if source:sub(1, 2) ~= "$$"
        and source:sub(1, 1) == "$"
        and source:sub(-1) == "$" then
      source = "\\(" .. source:sub(2, -2) .. "\\)"
    end
    return pandoc.RawInline("markdown", source)
  end
  el.identifier = ""
  el.classes = {}
  el.attributes = {}
  return el
end

function Superscript(el)
  for _, inline in ipairs(el.content or {}) do
    if inline.t == "Note" then
      -- The upstream HTML already wraps footnote references in <sup>.
      -- Pandoc's Note writer supplies its own superscript presentation.
      return el.content
    end
  end
  return el
end

function Table(el)
  el.identifier = ""
  el.classes = {}
  el.attributes = {}
  return el
end

function Figure(el)
  local caption_text = plain_label(el.caption and el.caption.long or {})
  -- Caption line breaks sometimes stringify without whitespace.
  caption_text = caption_text:gsub("([%.!?])([A-Z])", "%1 %2")
  local captured = nil

  el.content:walk({
    Image = function(image)
      if not captured then
        captured = image
      end
      return image
    end
  })

  if not captured then
    return el.content
  end

  if caption_text ~= "" then
    captured.caption = {pandoc.Str(caption_text)}
    return {
      pandoc.Para({captured}),
      pandoc.Para({pandoc.Emph({pandoc.Str(caption_text)})})
    }
  end

  return pandoc.Para({captured})
end
