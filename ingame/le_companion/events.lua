--[[ le_companion/events.lua — CM event ids, blackout, pump classification.

Keys off ENUM_CM_EVENT_MSG_* (enums.lua), never CONST_CM_EVENTS_NAMES.
]]

local events = {}

events.FALLBACK_IDS = {
    ABOUT_TO_INIT_MODE = 5,
    CAREER_TYPE_SELECTED = 6,
    HOURS_PASSED = 14,
    DAY_PASSED = 15,
    WEEK_PASSED = 16,
    MONTH_PASSED = 17,
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
    PLAYER_CONTRACT_NEGOTIATION = 64,
    SCREEN_HAS_DONE_LOADING = 68,
    PLAYER_MADE_AVAILABLE_FOR_TRANSFER = 80,
    PLAYER_MADE_AVAILABLE_FOR_LOAN = 81,
    ENTERING_TEAM_MANAGEMENT = 82,
    LEAVING_TEAM_MANAGEMENT = 83,
    TRANSFER_MOVE_ABOUT_TO_COMPLETE = 85,
    TRANSFER_MOVE_COMPLETE = 86,
    USER_PRESIGNED_CONTRACT_CREATED = 87,
    YOUTH_PLAYER_ADDED_TO_YOUTH_ACADEMY = 109,
    PLAYER_ADDED_TO_TEAM = 110,
    PLAYER_REMOVED_FROM_TEAM = 111,
    YOUTH_PLAYER_PROMOTION = 112,
    YOUTH_PLAYERS_RETIREMENT = 115,
    USER_SHORTLISTED = 136,
    USER_TRANSFERLISTED = 137,
    USER_LOANLISTED = 138,
    USER_PLAYER_POSITION_CHANGED = 148,
    USER_PLAYER_TRAINING_PLAN_CHANGED = 149,
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
    "ENTERED_HUB_FIRST_TIME", "ENTERING_TEAM_MANAGEMENT",
}
-- IsInCM is already true on the career menus, before GetUserTeamID is safe.
-- Only these two mean the hub (or team management) is actually open.
events.HUB_READY_NAMES = {
    "ENTERED_HUB_FIRST_TIME", "ENTERING_TEAM_MANAGEMENT",
}
-- 2.6.42: screen and notification events that fire while the user stays in
-- the Career hub (DATA_READY alone fired ~2,000 times in one 2026-10-17
-- session). They keep hub readiness instead of revoking it, so a settled hub
-- no longer needs a fresh Team Management visit after every click. They never
-- *establish* readiness: only HUB_READY_NAMES do. Every id not listed here or
-- there, including unknown ones, still revokes readiness (fail safe), which
-- covers simulation, saving, loading, matches and season changes.
events.BENIGN_NAMES = {
    "DATA_READY", "SCREEN_HAS_DONE_LOADING", "LEAVING_TEAM_MANAGEMENT",
    "TRANSFER_MOVE_ABOUT_TO_COMPLETE", "TRANSFER_MOVE_COMPLETE",
    "PLAYER_ADDED_TO_TEAM", "PLAYER_REMOVED_FROM_TEAM",
    "PLAYER_CONTRACT_TERMINATION", "PLAYER_CONTRACT_ACCEPTED",
    "PLAYER_CONTRACT_NEGOTIATION", "USER_PRESIGNED_CONTRACT_CREATED",
    "PLAYER_INSERTED_INTO_PLAYERS_TABLE", "PLAYER_DELETED_FROM_PLAYERS_TABLE",
    "PLAYER_MADE_AVAILABLE_FOR_TRANSFER", "PLAYER_MADE_AVAILABLE_FOR_LOAN",
    "USER_SHORTLISTED", "USER_TRANSFERLISTED", "USER_LOANLISTED",
    "USER_PLAYER_POSITION_CHANGED", "USER_PLAYER_TRAINING_PLAN_CHANGED",
}
-- Benign events after which the squad may differ: the live mirror rebuilds.
events.SQUAD_CHANGE_NAMES = {
    "TRANSFER_MOVE_ABOUT_TO_COMPLETE", "TRANSFER_MOVE_COMPLETE",
    "PLAYER_ADDED_TO_TEAM", "PLAYER_REMOVED_FROM_TEAM",
    "PLAYER_CONTRACT_TERMINATION", "PLAYER_CONTRACT_ACCEPTED",
    "USER_PRESIGNED_CONTRACT_CREATED",
    "PLAYER_INSERTED_INTO_PLAYERS_TABLE", "PLAYER_DELETED_FROM_PLAYERS_TABLE",
}
-- Calendar simulate and match entry fire these in a tight loop on the game
-- thread. The companion must not touch disk or the player DB while they do.
events.BUSY_NAMES = {
    "HOURS_PASSED", "DAY_PASSED", "WEEK_PASSED", "MONTH_PASSED",
    "ABOUT_TO_ENTER_PREMATCH", "ABOUT_TO_ENTER_A_MATCH",
    "ABOUT_TO_SIM_OVER_A_MATCH", "ABOUT_TO_ENTER_QUICK_SIM",
    "ABOUT_TO_ENTER_INTERACTIVE_SIM", "ABOUT_TO_ENTER_PLAY_HIGHLIGHTS",
    "ABOUT_TO_ENTER_PLAY_FULL_MATCH_SIM",
}

events.id_of = {}
events.name_of = {}
events.hard_reset = {}
events.invalidate_players = {}
events.invalidate_squads = {}
events.blackout = {}
events.pump = {}
events.busy = {}
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
    events.busy = {}
    events.benign = {}
    events.squad_change = {}
    fill(events.hard_reset, events.HARD_RESET_NAMES)
    fill(events.invalidate_players, events.INVALIDATE_PLAYERS_NAMES)
    fill(events.invalidate_squads, events.INVALIDATE_SQUADS_NAMES)
    fill(events.blackout, events.BLACKOUT_NAMES)
    fill(events.pump, events.PUMP_NAMES)
    fill(events.hub, events.HUB_READY_NAMES)
    fill(events.busy, events.BUSY_NAMES)
    fill(events.benign, events.BENIGN_NAMES)
    fill(events.squad_change, events.SQUAD_CHANGE_NAMES)
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

function events.is_busy(id)
    return type(id) == "number" and events.busy[id] ~= nil
end

--- A hub screen/notification event: keeps readiness, never establishes it.
function events.is_benign(id)
    return type(id) == "number" and events.benign ~= nil and events.benign[id] ~= nil
end

function events.changes_squad(id)
    return type(id) == "number" and events.squad_change ~= nil and events.squad_change[id] ~= nil
end

events.load()

return events
