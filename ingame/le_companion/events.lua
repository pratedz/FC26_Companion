--[[ le_companion/events.lua — CM event ids, blackout, pump classification.

Keys off ENUM_CM_EVENT_MSG_* (enums.lua), never CONST_CM_EVENTS_NAMES.
]]

local events = {}

events.FALLBACK_IDS = {
    ABOUT_TO_INIT_MODE = 5,
    CAREER_TYPE_SELECTED = 6,
    DAY_PASSED = 15,
    WEEK_PASSED = 16,
    SEASON_RESET = 23,
    SEASON_ENDED = 24,
    ENTERED_HUB_FIRST_TIME = 26,
    DATA_READY = 27,
    PREPARE_FOR_SAVE = 28,
    POST_LOAD_PREPARE = 29,
    PREPARE_FOR_FIRST_SAVE = 30,
    ABOUT_TO_ENTER_PREMATCH = 37,
    POST_MATCH_REPORTS_DISPLAYED = 39,
    ABOUT_TO_ENTER_A_MATCH = 42,
    FE_TO_BE = 43,
    ABOUT_TO_SIM_OVER_A_MATCH = 44,
    ABOUT_TO_ENTER_QUICK_SIM = 45,
    ABOUT_TO_ENTER_INTERACTIVE_SIM = 46,
    ABOUT_TO_ENTER_PLAY_HIGHLIGHTS = 47,
    ABOUT_TO_ENTER_PLAY_FULL_MATCH_SIM = 48,
    PLAYER_INSERTED_INTO_PLAYERS_TABLE = 58,
    PLAYER_DELETED_FROM_PLAYERS_TABLE = 59,
    PLAYERS_RETIRED = 60,
    ALL_RETIRED_PLAYERS_COMPLETE = 61,
    PLAYER_CONTRACT_TERMINATION = 62,
    PLAYER_CONTRACT_ACCEPTED = 63,
    SCREEN_HAS_DONE_LOADING = 68,
    ENTERING_TEAM_MANAGEMENT = 82,
    TRANSFER_MOVE_COMPLETE = 86,
    YOUTH_PLAYER_ADDED_TO_YOUTH_ACADEMY = 109,
    PLAYER_ADDED_TO_TEAM = 110,
    PLAYER_REMOVED_FROM_TEAM = 111,
    YOUTH_PLAYER_PROMOTION = 112,
    YOUTH_PLAYERS_RETIREMENT = 115,
}

events.HARD_RESET_NAMES = {
    "DATA_READY", "POST_LOAD_PREPARE", "ABOUT_TO_INIT_MODE", "CAREER_TYPE_SELECTED",
}
events.INVALIDATE_PLAYERS_NAMES = {
    "PLAYER_INSERTED_INTO_PLAYERS_TABLE", "PLAYER_DELETED_FROM_PLAYERS_TABLE",
    "PLAYERS_RETIRED", "ALL_RETIRED_PLAYERS_COMPLETE",
    "YOUTH_PLAYER_ADDED_TO_YOUTH_ACADEMY", "YOUTH_PLAYER_PROMOTION",
    "YOUTH_PLAYERS_RETIREMENT", "SEASON_RESET", "SEASON_ENDED",
}
events.INVALIDATE_SQUADS_NAMES = {
    "TRANSFER_MOVE_COMPLETE", "PLAYER_ADDED_TO_TEAM", "PLAYER_REMOVED_FROM_TEAM",
    "PLAYER_CONTRACT_TERMINATION", "PLAYER_CONTRACT_ACCEPTED",
}
-- Writing the player DB while the game serialises a save is a freeze risk.
events.BLACKOUT_NAMES = {
    "PREPARE_FOR_SAVE", "PREPARE_FOR_FIRST_SAVE", "POST_LOAD_PREPARE",
    "ABOUT_TO_INIT_MODE", "DATA_READY", "SEASON_RESET",
    "ABOUT_TO_ENTER_PREMATCH", "ABOUT_TO_ENTER_A_MATCH", "FE_TO_BE",
    "ABOUT_TO_SIM_OVER_A_MATCH", "ABOUT_TO_ENTER_QUICK_SIM",
    "ABOUT_TO_ENTER_INTERACTIVE_SIM", "ABOUT_TO_ENTER_PLAY_HIGHLIGHTS",
    "ABOUT_TO_ENTER_PLAY_FULL_MATCH_SIM",
}
events.PUMP_NAMES = {
    -- Only front-end states where the Career DB is settled. DAY_PASSED,
    -- WEEK_PASSED and match/finance events are deliberately excluded.
    "ENTERED_HUB_FIRST_TIME", "SCREEN_HAS_DONE_LOADING", "ENTERING_TEAM_MANAGEMENT",
}
-- IsInCM is already true on the career menus, before GetUserTeamID is safe.
-- Only these two mean the hub (or team management) is actually open.
events.HUB_READY_NAMES = {
    "ENTERED_HUB_FIRST_TIME", "ENTERING_TEAM_MANAGEMENT",
}

events.id_of = {}
events.name_of = {}
events.hard_reset = {}
events.invalidate_players = {}
events.invalidate_squads = {}
events.blackout = {}
events.pump = {}
events.resolved_from = "fallback"

local function resolve_id(name)
    local v = rawget(_G, "ENUM_CM_EVENT_MSG_" .. name)
    if type(v) == "number" then
        events.resolved_from = "enums"
        return v
    end
    return events.FALLBACK_IDS[name]
end

local function fill(set, names)
    for i = 1, #names do
        local name = names[i]
        local id = events.id_of[name]
        if type(id) == "number" then set[id] = name end
    end
end

function events.load()
    pcall(function() require("imports/career_mode/enums") end)
    events.id_of = {}
    events.name_of = {}
    for name in pairs(events.FALLBACK_IDS) do
        local id = resolve_id(name)
        if type(id) == "number" then
            events.id_of[name] = id
            events.name_of[id] = name
        end
    end
    events.hard_reset = {}
    events.invalidate_players = {}
    events.invalidate_squads = {}
    events.blackout = {}
    events.pump = {}
    events.hub = {}
    fill(events.hard_reset, events.HARD_RESET_NAMES)
    fill(events.invalidate_players, events.INVALIDATE_PLAYERS_NAMES)
    fill(events.invalidate_squads, events.INVALIDATE_SQUADS_NAMES)
    fill(events.blackout, events.BLACKOUT_NAMES)
    fill(events.pump, events.PUMP_NAMES)
    fill(events.hub, events.HUB_READY_NAMES)
    return true
end

function events.name(id)
    if type(id) ~= "number" then return "unknown" end
    return events.name_of[id] or ("event_" .. tostring(id))
end

function events.classify(id)
    if type(id) ~= "number" then return "ignore" end
    if events.blackout[id] then return "blackout" end
    if events.hard_reset[id] then return "hard_reset" end
    if events.invalidate_players[id] then return "invalidate_players" end
    if events.invalidate_squads[id] then return "invalidate_squads" end
    if events.pump[id] then return "pump" end
    return "ignore"
end

function events.cache_action(id)
    if type(id) ~= "number" then return "none" end
    if events.hard_reset[id] then return "hard_reset" end
    if events.invalidate_players[id] then return "invalidate_players" end
    if events.invalidate_squads[id] then return "invalidate_squads" end
    return "none"
end

function events.is_hub(id)
    return type(id) == "number" and events.hub[id] ~= nil
end

function events.is_blackout(id)
    return type(id) == "number" and events.blackout[id] ~= nil
end

events.load()

return events
