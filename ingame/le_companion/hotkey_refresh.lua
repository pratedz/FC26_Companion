-- Bind a Live Editor hotkey (Create Hotkey -> Lua script) to this file for an
-- on-demand read-only refresh. LE runs it immediately inside the game's
-- message handling; the runner still refuses to read outside a settled hub.
local lec = rawget(_G, "LEC")
if type(lec) == "table" and type(lec.native) == "table" and lec.native.hotkey then
    pcall(lec.native.hotkey)
end
