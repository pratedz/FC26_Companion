-- Bounded reads from LE's game asset namespace. Never patches game memory.
local util = require("util")
local module = {}

local function failed(reason, detail)
    return {ok=false, reason=reason, detail=detail, data={}, counts={},
        failures={{reason=reason, detail=detail}}}
end

local function valid_path(path)
    if type(path) ~= "string" or #path > 240 or not path:match("^data/[^/]+/[^/]+") then return false end
    if path:find("//", 1, true) or path:sub(-1) == "/" then return false end
    for part in path:gmatch("[^/]+") do
        if not part:match("^[%w_-][%w_.-]*$") or part:sub(-1) == "." then return false end
        local stem = part:match("^[^.]+") or part
        stem = stem:upper()
        if stem == "CON" or stem == "PRN" or stem == "AUX" or stem == "NUL"
            or stem:match("^COM[1-9]$") or stem:match("^LPT[1-9]$") then return false end
    end
    return true
end

local function filesize(path)
    local f = io.open(path, "rb")
    if not f then return nil end
    local size = f:seek("end")
    f:close()
    return size
end

function module.run(op, job, ctx)
    local export = op.action == "export" and not job.dry_run
    if op.action ~= "check" and op.action ~= "export" then return failed("bad_asset_request", "Unknown asset action") end
    local limit = op.action == "export" and 2 or 8
    if type(op.paths) ~= "table" or #op.paths < 1 or #op.paths > limit then
        return failed("bad_asset_request", "Asset request exceeds its bounded file limit")
    end
    if not util.safe_name(tostring(job.job_id or "") .. ".json") then
        return failed("bad_asset_request", "Invalid asset job ID")
    end
    local seen = {}
    -- Validate the entire request before performing any native export.
    for _, path in ipairs(op.paths) do
        if not valid_path(path) or seen[path:lower()] then
            return failed("bad_asset_path", "Invalid or duplicate legacy path")
        end
        seen[path:lower()] = true
    end
    local exists = rawget(_G, "LegacyFileExist")
    local exporter = rawget(_G, "LegacyFileExport")
    if type(exists) ~= "function" or (export and type(exporter) ~= "function") then
        return failed("unsupported_host", "This LE session lacks the legacy file API; restart with LE v26.3.6 or later")
    end
    local rows, failures = {}, {}
    local counts = {found=0, missing=0, side_effects=0}
    for index, path in ipairs(op.paths) do
        local row = {path=path, exists=false, exported=false}
        local checked, present = pcall(exists, path)
        if not checked or type(present) ~= "boolean" then
            row.error = "Asset existence check failed: " .. tostring(present)
        elseif not present then
            counts.missing = counts.missing + 1
            if export and op.required == true then row.error = "Required asset is missing" end
        else
            row.exists = true
            counts.found = counts.found + 1
            if export then
                local directory = util.join(ctx.queue_dir, "assets", job.job_id, tostring(index))
                local basename = path:match("([^/]+)$")
                local flat = util.join(directory, basename)
                local nested = util.join(directory, path)
                -- Never reuse an existing file as evidence of a new export.
                if filesize(flat) ~= nil or filesize(nested) ~= nil then
                    row.error = "Export destination already exists; submit a fresh request"
                else
                    -- Documentation calls outpath an output location. Support
                    -- hosts interpreting it as a folder or an explicit filename.
                    local sent, value = pcall(exporter, path, directory .. "/")
                    local output = filesize(flat) and flat or (filesize(nested) and nested or nil)
                    if not output then
                        sent, value = pcall(exporter, path, flat)
                        output = filesize(flat) and flat or (filesize(nested) and nested or nil)
                    end
                    local size = output and filesize(output) or 0
                    if sent and value == true and size and size > 0 then
                        row.exported = true
                        row.output = output
                        row.bytes = size
                        counts.side_effects = counts.side_effects + 1
                    else
                        row.error = "Export did not produce a verified nonempty file: " .. tostring(value)
                    end
                end
            end
        end
        if row.error then failures[#failures+1] = {reason="asset_read_failed", detail=path .. ": " .. row.error} end
        rows[#rows+1] = row
    end
    return {ok=#failures == 0, data={action=op.action, dry_run=job.dry_run == true,
        assets=rows, le_version=tostring(rawget(_G, "LE_VERSION") or "unknown")},
        counts=counts, failures=failures, reason=#failures > 0 and "asset_read_failed" or nil}
end

return module
