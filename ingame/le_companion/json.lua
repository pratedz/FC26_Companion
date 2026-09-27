--[[ le_companion/json.lua — JSON encode/decode for the v3 wire protocol.

WHY THIS MODULE EXISTS, AND WHY IT IS NOT A STRAIGHT COPY

  Derived from rxi's json.lua (MIT), which LE already ships at
  lua/libs/v2/imports/external/json.lua. That file is a good starting point and
  a bad finishing point for a wire protocol. Four things had to change, and
  every one of them corresponds to a way a result file would otherwise lie:

  1. EMPTY ARRAY vs EMPTY OBJECT. rxi encodes any table with no `[1]` — which
     includes every empty table — as `[]`. But docs/schema/result.schema.json
     declares `ops`, `failures` and `log` as arrays and `counts`, `data`, `env`,
     `steps`, `index` and `error` as objects. A job with zero failures must emit
     `"failures": []` and a job with no counts must emit `"counts": {}`, and a
     bare `{}` in Lua cannot tell you which. So tables carry an explicit shape:
     json.array(t) / json.object(t) tag via a metatable, decode tags what it
     parsed, and empty containers therefore round-trip. Untagged tables keep
     rxi's heuristic (has [1] -> array, else object), with the default for a
     genuinely empty untagged table being `{}`.

  2. null. rxi maps JSON null to Lua nil, so `{"readback": null}` loses the key
     entirely and `[1, null, 2]` decodes to a sparse table whose length is
     undefined. The result contract uses null meaningfully — `"error": null`
     means "no error", `"writes_performed": null` means "unknown", and
     `failures[].readback` may legitimately be null. So null decodes to the
     json.null sentinel and encodes back.

  3. Non-ASCII. Player names come out of the game DB as raw bytes of unknown
     encoding. If one invalid UTF-8 byte reaches the file, Python's
     `path.read_text(encoding="utf-8")` raises and read_json() returns None —
     the result silently becomes "no result", which the app renders as the job
     never having run. encode() therefore escapes every byte >= 0x80 as \uXXXX
     (surrogate-paired above the BMP) and replaces invalid sequences with
     U+FFFD, exactly like Python's json.dumps(ensure_ascii=True). Pure-ASCII
     JSON is always valid UTF-8. Pass {ascii = false} to opt out.

  4. Integers. rxi formats every number with "%.14g", so a 16-digit id becomes
     `1.0000000000000e+15`. Lua 5.4 distinguishes integers from floats; this
     encoder uses "%d" for integers, which is what the schema's `"type":
     "integer"` fields require.

  Also: keys are emitted in sorted order (Python writes with sort_keys=True),
  which makes the core's output byte-stable and therefore golden-testable; and
  decode never raises out of try_decode(), because a malformed job file must
  become a `poisoned` result, not a Lua error inside the render thread.

  rxi's original bit-level string parser is replaced with a single-pass one:
  the original applies three sequential gsubs, so a literal backslash followed
  by `u0041` is rewritten as if it were a unicode escape.

  ORIGINAL COPYRIGHT (rxi/json.lua, MIT):
    Copyright (c) 2019 rxi. Permission is hereby granted, free of charge, to
    any person obtaining a copy of this software and associated documentation
    files (the "Software"), to deal in the Software without restriction...
    THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND.
    Full text: lua/libs/v2/imports/external/json.lua
--]]

local json = {_version = "2.0.0", _derived_from = "rxi/json.lua 0.1.1"}

local MAX_DEPTH = 64

-------------------------------------------------------------------------------
-- Sentinels and shape tagging
-------------------------------------------------------------------------------

--- The JSON `null` value. Distinct from nil, so a key can exist and be null.
json.null = setmetatable({}, {
    __tostring = function() return "null" end,
    __newindex = function() error("json.null is immutable") end,
})

function json.is_null(v)
    return v == json.null
end

local ARRAY_MT  = {__jsonshape = "array"}
local OBJECT_MT = {__jsonshape = "object"}

--- Tag `t` (default a fresh table) as a JSON array. Encodes as [...] even empty.
function json.array(t)
    return setmetatable(t or {}, ARRAY_MT)
end

--- Tag `t` (default a fresh table) as a JSON object. Encodes as {...} even empty.
function json.object(t)
    return setmetatable(t or {}, OBJECT_MT)
end

--- A fresh empty array. Call it — do not share one instance across results.
function json.empty_array()
    return setmetatable({}, ARRAY_MT)
end

--- A fresh empty object.
function json.empty_object()
    return setmetatable({}, OBJECT_MT)
end

--- The declared shape of a table: "array", "object", or nil (undeclared).
function json.shape(t)
    local mt = getmetatable(t)
    if mt == ARRAY_MT then return "array" end
    if mt == OBJECT_MT then return "object" end
    if type(mt) == "table" and mt.__jsonshape then return mt.__jsonshape end
    return nil
end

-------------------------------------------------------------------------------
-- Encode
-------------------------------------------------------------------------------

local escape_map = {
    ["\\"] = "\\\\",
    ["\""] = "\\\"",
    ["\b"] = "\\b",
    ["\f"] = "\\f",
    ["\n"] = "\\n",
    ["\r"] = "\\r",
    ["\t"] = "\\t",
}

-- Everything printable-ASCII except `"` (34) and `\` (92) is emitted verbatim.
local PLAIN_PATTERN = "[^\32-\33\35-\91\93-\126]"

local function u_escape(cp)
    if cp < 0x10000 then
        return string.format("\\u%04x", cp)
    end
    cp = cp - 0x10000
    return string.format("\\u%04x\\u%04x",
        0xD800 + (cp >> 10), 0xDC00 + (cp & 0x3FF))
end

--- Escape one byte-run into a JSON string body.
-- Walks bytes so a multi-byte UTF-8 sequence becomes ONE \uXXXX (or a surrogate
-- pair) rather than three mojibake escapes, and so an invalid byte becomes
-- U+FFFD instead of corrupting the file.
local function escape_body(s, ascii)
    local out = {}
    local n = #s
    local i = 1
    while i <= n do
        local b = s:byte(i)
        local ch = s:sub(i, i)
        local mapped = escape_map[ch]
        if mapped then
            out[#out + 1] = mapped
            i = i + 1
        elseif b < 0x20 or b == 0x7F then
            out[#out + 1] = string.format("\\u%04x", b)
            i = i + 1
        elseif b < 0x80 then
            out[#out + 1] = ch
            i = i + 1
        elseif not ascii then
            out[#out + 1] = ch
            i = i + 1
        else
            -- Decode one UTF-8 sequence, or emit U+FFFD and advance one byte.
            local need, cp = 0, 0
            if b >= 0xC2 and b <= 0xDF then
                need, cp = 1, b & 0x1F
            elseif b >= 0xE0 and b <= 0xEF then
                need, cp = 2, b & 0x0F
            elseif b >= 0xF0 and b <= 0xF4 then
                need, cp = 3, b & 0x07
            end
            local ok = need > 0 and (i + need) <= n
            if ok then
                for k = 1, need do
                    local c = s:byte(i + k)
                    if c < 0x80 or c > 0xBF then ok = false; break end
                    cp = (cp << 6) | (c & 0x3F)
                end
            end
            if ok and cp <= 0x10FFFF then
                out[#out + 1] = u_escape(cp)
                i = i + need + 1
            else
                out[#out + 1] = "\\ufffd"
                i = i + 1
            end
        end
    end
    return table.concat(out)
end

local function encode_string(s, ascii)
    if not s:find(PLAIN_PATTERN) then
        return '"' .. s .. '"'
    end
    return '"' .. escape_body(s, ascii) .. '"'
end

local mtype = math.type

local function encode_number(v)
    if v ~= v then error("json: cannot encode NaN") end
    if v == math.huge or v == -math.huge then error("json: cannot encode infinity") end
    if mtype then
        if mtype(v) == "integer" then return string.format("%d", v) end
    elseif v == math.floor(v) and math.abs(v) < 2 ^ 53 then
        return string.format("%d", v)
    end
    return string.format("%.14g", v)
end

local encode_value

local function encode_array(t, opts, stack, depth, nl, pad, pad_in)
    local parts = {}
    local n = #t
    for i = 1, n do
        parts[i] = encode_value(t[i], opts, stack, depth + 1)
    end
    if n == 0 then return "[]" end
    if opts.indent then
        return "[" .. nl .. pad_in .. table.concat(parts, "," .. nl .. pad_in) .. nl .. pad .. "]"
    end
    return "[" .. table.concat(parts, ",") .. "]"
end

local function encode_object(t, opts, stack, depth, nl, pad, pad_in)
    -- Sorted keys: byte-stable output, so results are golden-testable and
    -- Python's sort_keys=True output and ours diff cleanly against each other.
    local ks = {}
    for k in pairs(t) do
        if type(k) == "string" then
            ks[#ks + 1] = k
        elseif type(k) == "number" then
            ks[#ks + 1] = k
        else
            error("json: invalid key type '" .. type(k) .. "'")
        end
    end
    table.sort(ks, function(a, b) return tostring(a) < tostring(b) end)
    local parts = {}
    local sep = opts.indent and ": " or ":"
    for _, k in ipairs(ks) do
        local v = t[k]
        if v ~= nil then
            parts[#parts + 1] = encode_string(tostring(k), opts.ascii) .. sep
                .. encode_value(v, opts, stack, depth + 1)
        end
    end
    if #parts == 0 then return "{}" end
    if opts.indent then
        return "{" .. nl .. pad_in .. table.concat(parts, "," .. nl .. pad_in) .. nl .. pad .. "}"
    end
    return "{" .. table.concat(parts, ",") .. "}"
end

encode_value = function(v, opts, stack, depth)
    if v == json.null then return "null" end
    local tv = type(v)
    if tv == "nil" then return "null" end
    if tv == "boolean" then return v and "true" or "false" end
    if tv == "number" then return encode_number(v) end
    if tv == "string" then return encode_string(v, opts.ascii) end
    if tv ~= "table" then
        error("json: cannot encode type '" .. tv .. "'")
    end

    if depth > MAX_DEPTH then error("json: nesting deeper than " .. MAX_DEPTH) end
    if stack[v] then error("json: circular reference") end
    stack[v] = true

    local nl, pad, pad_in = "", "", ""
    if opts.indent then
        nl = "\n"
        pad = string.rep(" ", opts.indent * depth)
        pad_in = string.rep(" ", opts.indent * (depth + 1))
    end

    local shape = json.shape(v)
    local res
    if shape == "array" then
        res = encode_array(v, opts, stack, depth, nl, pad, pad_in)
    elseif shape == "object" then
        res = encode_object(v, opts, stack, depth, nl, pad, pad_in)
    elseif rawget(v, 1) ~= nil then
        res = encode_array(v, opts, stack, depth, nl, pad, pad_in)
    else
        -- Untagged and with no [1]: an object. An untagged EMPTY table is
        -- therefore `{}`. Anything that must be `[]` when empty has to say so
        -- with json.array() — that is the whole point of the tag.
        res = encode_object(v, opts, stack, depth, nl, pad, pad_in)
    end

    stack[v] = nil
    return res
end

--- Encode a Lua value to JSON text.
-- @param opts optional table:
--   ascii   default true. Escape every byte >= 0x80 as \uXXXX. Keep this on
--           for anything Python reads: it makes the output unconditionally
--           UTF-8 decodable regardless of what the game DB handed us.
--   indent  nil (compact, default) or a space count for pretty output.
-- Raises on NaN/inf, circular references, and non-encodable types. Callers
-- writing to disk should use json.try_encode.
function json.encode(value, opts)
    local o = {ascii = true, indent = nil}
    if type(opts) == "table" then
        if opts.ascii ~= nil then o.ascii = opts.ascii and true or false end
        if opts.indent then o.indent = math.floor(opts.indent) end
    end
    return encode_value(value, o, {}, 0)
end

--- encode() that never raises.
-- @return text(string|nil), err(string|nil)
function json.try_encode(value, opts)
    local ok, res = pcall(json.encode, value, opts)
    if ok then return res, nil end
    return nil, tostring(res)
end

-------------------------------------------------------------------------------
-- Decode
-------------------------------------------------------------------------------

local parse_value

local space_chars = {[" "] = true, ["\t"] = true, ["\r"] = true, ["\n"] = true}
local delim_chars = {
    [" "] = true, ["\t"] = true, ["\r"] = true, ["\n"] = true,
    ["]"] = true, ["}"] = true, [","] = true,
}
local unescape_map = {
    ['"'] = '"', ["\\"] = "\\", ["/"] = "/",
    ["b"] = "\b", ["f"] = "\f", ["n"] = "\n", ["r"] = "\r", ["t"] = "\t",
}
local literal_map = {["true"] = true, ["false"] = false, ["null"] = json.null}

local function decode_error(str, idx, msg)
    local line, col = 1, 1
    for i = 1, idx - 1 do
        col = col + 1
        if str:sub(i, i) == "\n" then line = line + 1; col = 1 end
    end
    error(string.format("%s at line %d col %d", msg, line, col), 0)
end

local function skip_space(str, idx)
    for i = idx, #str do
        if not space_chars[str:sub(i, i)] then return i end
    end
    return #str + 1
end

local function next_delim(str, idx)
    for i = idx, #str do
        if delim_chars[str:sub(i, i)] then return i end
    end
    return #str + 1
end

local function codepoint_to_utf8(n)
    local f = math.floor
    if n <= 0x7F then
        return string.char(n)
    elseif n <= 0x7FF then
        return string.char(f(n / 64) + 192, n % 64 + 128)
    elseif n <= 0xFFFF then
        return string.char(f(n / 4096) + 224, f(n % 4096 / 64) + 128, n % 64 + 128)
    elseif n <= 0x10FFFF then
        return string.char(f(n / 262144) + 240, f(n % 262144 / 4096) + 128,
            f(n % 4096 / 64) + 128, n % 64 + 128)
    end
    return "\239\191\189"  -- U+FFFD
end

--- Single pass, so a literal backslash before "u0041" is not mistaken for an
--- escape (the original applied three sequential gsubs and could).
local function parse_string(str, i)
    local buf = {}
    local j = i + 1
    while true do
        local p = str:find('[%c"\\]', j)
        if not p then
            decode_error(str, i, "expected closing quote for string")
        end
        if p > j then
            buf[#buf + 1] = str:sub(j, p - 1)
        end
        local c = str:byte(p)
        if c == 34 then                                   -- '"'
            return table.concat(buf), p + 1
        elseif c < 32 then
            decode_error(str, p, "control character in string")
        else                                              -- '\'
            local e = str:sub(p + 1, p + 1)
            if e == "u" then
                local hex = str:sub(p + 2, p + 5)
                if not hex:match("^%x%x%x%x$") then
                    decode_error(str, p, "invalid unicode escape in string")
                end
                local cp = tonumber(hex, 16)
                j = p + 6
                if cp >= 0xD800 and cp <= 0xDBFF and str:sub(j, j + 1) == "\\u" then
                    local hex2 = str:sub(j + 2, j + 5)
                    if hex2:match("^%x%x%x%x$") then
                        local lo = tonumber(hex2, 16)
                        if lo >= 0xDC00 and lo <= 0xDFFF then
                            cp = (cp - 0xD800) * 0x400 + (lo - 0xDC00) + 0x10000
                            j = j + 6
                        end
                    end
                end
                buf[#buf + 1] = codepoint_to_utf8(cp)
            else
                local rep = unescape_map[e]
                if rep == nil then
                    decode_error(str, p, "invalid escape char '" .. e .. "' in string")
                end
                buf[#buf + 1] = rep
                j = p + 2
            end
        end
    end
end

local function parse_number(str, i)
    local x = next_delim(str, i)
    local s = str:sub(i, x - 1)
    local n = tonumber(s)
    if not n then decode_error(str, i, "invalid number '" .. s .. "'") end
    -- Integer-looking JSON stays a Lua integer, so playerids and timestamps
    -- survive a decode/encode round trip without acquiring a ".0".
    if not s:find("[%.eE]") then
        local as_int = math.tointeger and math.tointeger(n)
        if as_int then n = as_int end
    end
    return n, x
end

local function parse_literal(str, i)
    local x = next_delim(str, i)
    local word = str:sub(i, x - 1)
    if literal_map[word] == nil and word ~= "null" then
        decode_error(str, i, "invalid literal '" .. word .. "'")
    end
    return literal_map[word], x
end

local function parse_array(str, i, depth)
    local res = json.array({})
    local n = 1
    i = i + 1
    while true do
        i = skip_space(str, i)
        if str:sub(i, i) == "]" then return res, i + 1 end
        local x
        x, i = parse_value(str, i, depth + 1)
        res[n] = x
        n = n + 1
        i = skip_space(str, i)
        local chr = str:sub(i, i)
        i = i + 1
        if chr == "]" then return res, i end
        if chr ~= "," then decode_error(str, i, "expected ']' or ','") end
    end
end

local function parse_object(str, i, depth)
    local res = json.object({})
    i = i + 1
    while true do
        i = skip_space(str, i)
        if str:sub(i, i) == "}" then return res, i + 1 end
        if str:sub(i, i) ~= '"' then decode_error(str, i, "expected string for key") end
        local key, val
        key, i = parse_string(str, i)
        i = skip_space(str, i)
        if str:sub(i, i) ~= ":" then decode_error(str, i, "expected ':' after key") end
        i = skip_space(str, i + 1)
        val, i = parse_value(str, i, depth + 1)
        res[key] = val
        i = skip_space(str, i)
        local chr = str:sub(i, i)
        i = i + 1
        if chr == "}" then return res, i end
        if chr ~= "," then decode_error(str, i, "expected '}' or ','") end
    end
end

parse_value = function(str, idx, depth)
    depth = depth or 0
    if depth > MAX_DEPTH then
        decode_error(str, idx, "nesting deeper than " .. MAX_DEPTH)
    end
    local chr = str:sub(idx, idx)
    if chr == '"' then return parse_string(str, idx) end
    if chr == "{" then return parse_object(str, idx, depth) end
    if chr == "[" then return parse_array(str, idx, depth) end
    if chr == "t" or chr == "f" or chr == "n" then return parse_literal(str, idx) end
    if chr == "-" or (chr >= "0" and chr <= "9") then return parse_number(str, idx) end
    decode_error(str, idx, "unexpected character '" .. chr .. "'")
end

--- Decode JSON text. RAISES on malformed input.
-- Use try_decode for anything read off disk — a corrupted job file must become
-- a `poisoned` result, never an unhandled error on the game thread.
function json.decode(str)
    if type(str) ~= "string" then
        error("json: expected string, got " .. type(str), 0)
    end
    local res, idx = parse_value(str, skip_space(str, 1), 0)
    idx = skip_space(str, idx)
    if idx <= #str then decode_error(str, idx, "trailing garbage") end
    return res
end

--- decode() that never raises.
-- @return value(any|nil), err(string|nil)
function json.try_decode(str)
    if type(str) ~= "string" or str == "" then
        return nil, "empty input"
    end
    local ok, res = pcall(json.decode, str)
    if ok then return res, nil end
    return nil, tostring(res)
end

--- try_decode restricted to a top-level object, which is what every v3 file is.
-- @return table|nil, err
function json.try_decode_object(str)
    local v, err = json.try_decode(str)
    if err then return nil, err end
    if type(v) ~= "table" or json.shape(v) == "array" then
        return nil, "expected a JSON object at the top level"
    end
    return v, nil
end

return json
