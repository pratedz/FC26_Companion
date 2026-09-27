"""Emergency restore of LE live_editor.lua to stock (no companion auto-arm)."""
from __future__ import annotations

import re
from pathlib import Path

CORE = Path(__file__).resolve().parents[2] / "lua" / "libs" / "v2" / "imports" / "core"
CUR = CORE / "live_editor.lua"
BAK = CORE / "live_editor.lua.companion_bak"

STOCK = r"""MEMORY = require 'imports/core/memory'
LOGGER = require 'imports/core/logger'
HTTP = require 'imports/http/http'
REQUEST = require 'imports/http/request'

local DB = require 'imports/t3db/db'
local GameplayAttribulatorManager = require 'imports/gameplay/gp_attribulator_manager'
local PlayerDevelopmentManager = require 'imports/player_development/player_development_manager'
local AardvarkManager = require 'imports/aardvark/aardvark_manager'
local GameLocalizationManager = require 'imports/localization/game_localization'
local PLAYERS_MANAGER = require 'imports/core/managers/players_manager'

local LIVE_EDITOR = {}

function LIVE_EDITOR:new()
    local o = setmetatable({}, self)

    -- lua metatable
    self.__index = self
    self.__name = "LIVE_EDITOR"

    -- 
    self.version = ""
    self.data_path = ""
    self.game_base = 0
    self.game_size = 0
    self.game_name = ""

    -- DB
    self.db = DB:new()

    -- Gameplay Attribulator Manager
    self.gameplay_attribulator_manager = GameplayAttribulatorManager:new()

    -- Player Development Manager
    self.player_development_manager = PlayerDevelopmentManager:new()

    -- AardvarkManager
    self.aardvark_manager = AardvarkManager:new()

    -- GameLocalizationManager
    self.game_localization_manager = GameLocalizationManager:new()

    -- Players Manager
    self.players_manager = PLAYERS_MANAGER:new()

    -- config.json
    self.config = {}

    self:Init()

    return o
end

function LIVE_EDITOR:Init()
    LOGGER:LogInfo("Init LIVE EDITOR LUA API V2")
    self.version = LE_VERSION
    self.data = LE_DATA_PATH
    self.game_base = LE_GAME_MODULE_BASE
    self.game_size = LE_GAME_MODULE_SIZE
    self.game_name = LE_GAME_MODULE_NAME

    self.players_manager:Init(self.db)
end

function LIVE_EDITOR:Load()
    LOGGER:LogInfo(string.format("Load LIVE EDITOR %s LUA API V2", self.version))
end

return LIVE_EDITOR;
"""


def main() -> int:
    CORE.mkdir(parents=True, exist_ok=True)
    text = None
    if BAK.is_file():
        raw = BAK.read_text(encoding="utf-8")
        if "AUTOARM" not in raw and "LE_Companion" not in raw and "__LE_COMPANION" not in raw:
            text = raw
        else:
            text = re.sub(
                r"-- LE_COMPANION_AUTOARM_BEGIN.*?-- LE_COMPANION_AUTOARM_END\n?",
                "",
                raw,
                flags=re.S,
            )
    if text is None or "AUTOARM" in text or "__LE_COMPANION" in text:
        text = STOCK

    # Always enforce clean Load body
    text = re.sub(
        r"function\s+LIVE_EDITOR:Load\s*\(\s*\).*?^end",
        'function LIVE_EDITOR:Load()\n    LOGGER:LogInfo(string.format("Load LIVE EDITOR %s LUA API V2", self.version))\nend',
        text,
        count=1,
        flags=re.S | re.M,
    )

    CUR.write_text(text, encoding="utf-8", newline="\n")
    # Keep backup as clean stock for next time
    BAK.write_text(text, encoding="utf-8", newline="\n")

    got = CUR.read_text(encoding="utf-8")
    print("wrote", CUR)
    print("AUTOARM", "AUTOARM" in got)
    print("Companion", "LE_Companion" in got or "__LE_COMPANION" in got)
    print(got[got.find("function LIVE_EDITOR:Load") :])
    if "AUTOARM" in got or "__LE_COMPANION" in got:
        print("FAIL still patched")
        return 1
    print("OK stock live_editor restored")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
