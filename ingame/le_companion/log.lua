--[[ le_companion/log.lua — ring buffer + host Log() passthrough. ]]

local util = require("util")

local log = {
    ring = {},
    cap = 500,
    head = 0,
    capture = nil,
}

function log.write(msg)
    local line = string.format("%.3f %s", util.clock(), tostring(msg))
    log.head = log.head + 1
    log.ring[(log.head % log.cap) + 1] = line
    if type(_G.Log) == "function" then
        pcall(_G.Log, "[LEC] " .. tostring(msg))
    end
    if type(log.capture) == "table" and #log.capture < 500 then
        log.capture[#log.capture + 1] = line
    end
    return line
end

function log.capture_open()
    log.capture = {}
end

function log.capture_close()
    local c = log.capture or {}
    log.capture = nil
    return c
end

function log.job(msg)
    return log.write(msg)
end

function log.tail(n)
    local out = {}
    local count = math.min(n or 50, log.cap)
    for i = math.max(1, log.head - count + 1), log.head do
        local v = log.ring[(i % log.cap) + 1]
        if v then out[#out + 1] = v end
    end
    return out
end

return log
