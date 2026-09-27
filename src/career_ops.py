"""LE-safe career operations — pure Lua job generators (no CE memory).

Emits queue-ready Lua using Live Editor DOC APIs:
TransferPlayer, LoanPlayer, ReleasePlayerFromTeam, Get/SetTransferBudget.
"""

from __future__ import annotations

from typing import Any, Dict, Optional


def _i(v: Any, default: int = 0) -> int:
    try:
        return int(v)
    except (TypeError, ValueError):
        return int(default)


def generate_transfer_lua(
    *,
    playerid: int,
    to_teamid: int,
    transfersum: int = 0,
    wage: int = 5000,
    contract_months: int = 60,
    from_teamid: int = 0,
    release_clause: int = -1,
) -> str:
    """Immediate transfer (DOC TransferPlayer). Fails clearly when not in CM."""
    pid = _i(playerid)
    to_t = _i(to_teamid)
    if pid <= 0:
        raise ValueError("playerid must be > 0")
    if to_t <= 0:
        raise ValueError("to_teamid must be > 0")
    fee = max(0, _i(transfersum))
    wag = max(0, _i(wage))
    months = max(1, min(120, _i(contract_months, 60)))
    from_t = max(0, _i(from_teamid))
    rel = _i(release_clause, -1)
    return f"""--[[ LE Companion career TransferPlayer | id={pid} -> team={to_t} ]]
pcall(function()
  if type(IsInCM) == "function" then
    local ok, incm = pcall(IsInCM)
    if ok and incm == false then
      if Log then Log("[LE Companion] transfer abort: not in career mode") end
      return
    end
  end
  local playerid = {pid}
  local to_teamid = {to_t}
  local from_teamid = {from_t}
  local transfersum = {fee}
  local wage = {wag}
  local contract_months = {months}
  local release_clause = {rel}
  if type(IsPlayerPresigned) == "function" then
    local ok, pre = pcall(IsPlayerPresigned, playerid)
    if ok and pre and type(DeletePresignedContract) == "function" then
      pcall(DeletePresignedContract, playerid)
    end
  end
  if type(IsPlayerLoanedOut) == "function" then
    local ok, loaned = pcall(IsPlayerLoanedOut, playerid)
    if ok and loaned and type(TerminateLoan) == "function" then
      pcall(TerminateLoan, playerid)
    end
  end
  if type(TransferPlayer) ~= "function" then
    if Log then Log("[LE Companion] transfer abort: no TransferPlayer API") end
    return
  end
  local ok, err = pcall(TransferPlayer, playerid, to_teamid, transfersum, wage, contract_months, from_teamid, release_clause)
  if Log then
    if ok then
      Log(string.format("[LE Companion] TransferPlayer ok id=%d team=%d fee=%d", playerid, to_teamid, transfersum))
    else
      Log("[LE Companion] TransferPlayer err " .. tostring(err))
    end
  end
end)
"""


def generate_loan_lua(
    *,
    playerid: int,
    to_teamid: int,
    length_months: int = 12,
    loantobuy: int = -1,
    from_teamid: int = 0,
) -> str:
    """Immediate loan (DOC LoanPlayer)."""
    pid = _i(playerid)
    to_t = _i(to_teamid)
    if pid <= 0:
        raise ValueError("playerid must be > 0")
    if to_t <= 0:
        raise ValueError("to_teamid must be > 0")
    months = max(1, min(48, _i(length_months, 12)))
    ltb = _i(loantobuy, -1)
    from_t = max(0, _i(from_teamid))
    return f"""--[[ LE Companion career LoanPlayer | id={pid} -> team={to_t} months={months} ]]
pcall(function()
  if type(IsInCM) == "function" then
    local ok, incm = pcall(IsInCM)
    if ok and incm == false then
      if Log then Log("[LE Companion] loan abort: not in career mode") end
      return
    end
  end
  local playerid = {pid}
  local to_teamid = {to_t}
  local from_teamid = {from_t}
  local length_in_months = {months}
  local loantobuy = {ltb}
  if type(IsPlayerPresigned) == "function" then
    local ok, pre = pcall(IsPlayerPresigned, playerid)
    if ok and pre and type(DeletePresignedContract) == "function" then
      pcall(DeletePresignedContract, playerid)
    end
  end
  if type(LoanPlayer) ~= "function" then
    if Log then Log("[LE Companion] loan abort: no LoanPlayer API") end
    return
  end
  local ok, err = pcall(LoanPlayer, playerid, to_teamid, length_in_months, loantobuy, from_teamid)
  if Log then
    if ok then
      Log(string.format("[LE Companion] LoanPlayer ok id=%d team=%d months=%d", playerid, to_teamid, length_in_months))
    else
      Log("[LE Companion] LoanPlayer err " .. tostring(err))
    end
  end
end)
"""


def generate_release_lua(*, playerid: int) -> str:
    """Release to free agents (DOC ReleasePlayerFromTeam)."""
    pid = _i(playerid)
    if pid <= 0:
        raise ValueError("playerid must be > 0")
    return f"""--[[ LE Companion career ReleasePlayerFromTeam | id={pid} ]]
pcall(function()
  if type(IsInCM) == "function" then
    local ok, incm = pcall(IsInCM)
    if ok and incm == false then
      if Log then Log("[LE Companion] release abort: not in career mode") end
      return
    end
  end
  local playerid = {pid}
  if type(ReleasePlayerFromTeam) ~= "function" then
    if Log then Log("[LE Companion] release abort: no ReleasePlayerFromTeam API") end
    return
  end
  local ok, err = pcall(ReleasePlayerFromTeam, playerid)
  if Log then
    if ok then
      Log(string.format("[LE Companion] ReleasePlayerFromTeam ok id=%d", playerid))
    else
      Log("[LE Companion] ReleasePlayerFromTeam err " .. tostring(err))
    end
  end
end)
"""


def generate_set_transfer_budget_lua(*, amount: int) -> str:
    """Set club transfer budget (DOC SetTransferBudget)."""
    amt = max(0, _i(amount))
    return f"""--[[ LE Companion career SetTransferBudget | amount={amt} ]]
pcall(function()
  if type(IsInCM) == "function" then
    local ok, incm = pcall(IsInCM)
    if ok and incm == false then
      if Log then Log("[LE Companion] budget abort: not in career mode") end
      return
    end
  end
  -- FC 26 deprecated SetTransferBudget into a stub that only prints a
  -- deprecation notice. The real APIs are SetUserTransferBudget /
  -- SetCPUTransferBudget. Prefer them and fall back only if absent.
  local setter, setter_name
  if type(SetUserTransferBudget) == "function" then
    setter, setter_name = SetUserTransferBudget, "SetUserTransferBudget"
  elseif type(SetTransferBudget) == "function" then
    setter, setter_name = SetTransferBudget, "SetTransferBudget(deprecated)"
  else
    if Log then Log("[LE Companion] budget abort: no transfer budget API") end
    return
  end
  local amount = {amt}
  local ok, err = pcall(setter, amount)
  if Log then
    if ok then
      local cur = amount
      if type(GetUserTransferBudget) == "function" then
        local okg, g = pcall(GetUserTransferBudget)
        if okg then cur = g end
      end
      Log(string.format("[LE Companion] %s ok amount=%s now=%s", setter_name, tostring(amount), tostring(cur)))
    else
      Log("[LE Companion] " .. setter_name .. " err " .. tostring(err))
    end
  end
end)
"""


def generate_get_transfer_budget_lua() -> str:
    """Log current transfer budget (DOC GetTransferBudget)."""
    return """--[[ LE Companion career GetTransferBudget ]]
pcall(function()
  if type(IsInCM) == "function" then
    local ok, incm = pcall(IsInCM)
    if ok and incm == false then
      if Log then Log("[LE Companion] budget abort: not in career mode") end
      return
    end
  end
  -- GetTransferBudget is a deprecated no-op stub on FC 26.
  local getter, getter_name
  if type(GetUserTransferBudget) == "function" then
    getter, getter_name = GetUserTransferBudget, "GetUserTransferBudget"
  elseif type(GetTransferBudget) == "function" then
    getter, getter_name = GetTransferBudget, "GetTransferBudget(deprecated)"
  else
    if Log then Log("[LE Companion] budget abort: no transfer budget API") end
    return
  end
  local ok, budget = pcall(getter)
  if ok and Log then
    Log(string.format("[LE Companion] %s = %s", getter_name, tostring(budget)))
  elseif Log then
    Log("[LE Companion] " .. getter_name .. " err " .. tostring(budget))
  end
  if type(GetCPUTransferBudget) == "function" then
    local okc, cpu = pcall(GetCPUTransferBudget)
    if okc and Log then
      Log(string.format("[LE Companion] GetCPUTransferBudget = %s", tostring(cpu)))
    end
  end
end)
"""


def generate_terminate_loan_lua(*, playerid: int) -> str:
    pid = _i(playerid)
    if pid <= 0:
        raise ValueError("playerid must be > 0")
    return f"""--[[ LE Companion career TerminateLoan | id={pid} ]]
pcall(function()
  if type(IsInCM) == "function" then
    local ok, incm = pcall(IsInCM)
    if ok and incm == false then
      if Log then Log("[LE Companion] terminate loan abort: not in career mode") end
      return
    end
  end
  local playerid = {pid}
  if type(TerminateLoan) ~= "function" then
    if Log then Log("[LE Companion] terminate loan abort: no TerminateLoan API") end
    return
  end
  local ok, err = pcall(TerminateLoan, playerid)
  if Log then
    if ok then Log(string.format("[LE Companion] TerminateLoan ok id=%d", playerid))
    else Log("[LE Companion] TerminateLoan err " .. tostring(err)) end
  end
end)
"""


def generate_list_transfer_lists_lua(*, playerid: int, action: str = "transfer") -> str:
    """Add player to transfer or loan list (DOC helpers)."""
    pid = _i(playerid)
    if pid <= 0:
        raise ValueError("playerid must be > 0")
    action = (action or "transfer").strip().lower()
    if action == "loan":
        api = "AddPlayerToLoanList"
    else:
        api = "AddPlayerToTransferList"
    return f"""--[[ LE Companion career {api} | id={pid} ]]
pcall(function()
  if type(IsInCM) == "function" then
    local ok, incm = pcall(IsInCM)
    if ok and incm == false then
      if Log then Log("[LE Companion] list abort: not in career mode") end
      return
    end
  end
  local playerid = {pid}
  if type({api}) ~= "function" then
    if Log then Log("[LE Companion] list abort: no {api}") end
    return
  end
  local ok, err = pcall({api}, playerid)
  if Log then
    if ok then Log(string.format("[LE Companion] {api} ok id=%d", playerid))
    else Log("[LE Companion] {api} err " .. tostring(err)) end
  end
end)
"""


CAREER_OPS: Dict[str, Dict[str, Any]] = {
    "transfer": {
        "id": "transfer",
        "label": "Transfer Player",
        "description": "TransferPlayer to any club (CM).",
        "params": ["playerid", "to_teamid", "transfersum", "wage", "contract_months"],
    },
    "loan": {
        "id": "loan",
        "label": "Loan Player",
        "description": "LoanPlayer to any club (CM).",
        "params": ["playerid", "to_teamid", "length_months"],
    },
    "release": {
        "id": "release",
        "label": "Release Player",
        "description": "ReleasePlayerFromTeam (free agents).",
        "params": ["playerid"],
    },
    "set_budget": {
        "id": "set_budget",
        "label": "Set Transfer Budget",
        "description": "SetTransferBudget for user club.",
        "params": ["amount"],
    },
    "get_budget": {
        "id": "get_budget",
        "label": "Log Transfer Budget",
        "description": "GetTransferBudget and log.",
        "params": [],
    },
    "terminate_loan": {
        "id": "terminate_loan",
        "label": "Terminate Loan",
        "description": "TerminateLoan for playerid.",
        "params": ["playerid"],
    },
}


def list_career_ops() -> list:
    return [dict(v) for v in CAREER_OPS.values()]
