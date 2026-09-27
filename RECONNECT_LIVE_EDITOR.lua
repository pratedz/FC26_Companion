-- LE Companion: reconnect this already-open Live Editor session
local ok, result = pcall(function()
  local c = _G.LEC
  if type(c) ~= "table" or type(c.configure) ~= "function" then
    error("LE Companion v2 is not loaded")
  end
  c.configure({ queue_dir = [==[C:/Users/prated/Desktop/FC 26 LE v26.3.5/LE_Profile_Executor/queue]==] })
  local function host_true(value)
    return value == true or tonumber(value) == 1
  end
  local in_career = rawget(_G, "LEC_CAREER_SEEN") == true
  if not in_career then
    for _, name in ipairs({ "IsInCM", "IsInCareerMode" }) do
      local fn = rawget(_G, name)
      if type(fn) == "function" then
        local probe_ok, value = pcall(fn)
        if probe_ok and host_true(value) then
          in_career = true
          break
        end
      end
    end
  end
  if in_career and type(c.runner) == "table" and type(c.runner.on_career) == "function" then
    c.runner.on_career(nil, "reconnect")
  end
  local wrote, why = c.status.write_session({
    phase = "manual_reconfigure",
    capabilities = { in_career = in_career },
  })
  if not wrote then error(tostring(why or "could not write session")) end
  return c.core_info().queue_dir
end)
if Log then
  Log("[LEC] queue reconnect " .. (ok and "OK: " .. tostring(result) or "FAILED: " .. tostring(result)))
end
