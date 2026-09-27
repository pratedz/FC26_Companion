"""Career ops Lua builders — shipped career_ops generators."""

from __future__ import annotations

import re

import pytest

from src import career_ops
from src import product


def test_transfer_lua_contains_api_and_ids():
    lua = career_ops.generate_transfer_lua(
        playerid=20801, to_teamid=11, transfersum=500, wage=600, contract_months=60
    )
    assert "TransferPlayer" in lua
    assert "IsInCM" in lua
    assert "20801" in lua
    assert "11" in lua
    assert "500" in lua
    assert "600" in lua
    assert re.search(r"\b60\b", lua)


def test_loan_lua_contains_api():
    lua = career_ops.generate_loan_lua(playerid=158023, to_teamid=243, length_months=12)
    assert "LoanPlayer" in lua
    assert "158023" in lua
    assert "243" in lua
    assert "12" in lua
    assert "IsInCM" in lua


def test_release_lua_contains_api():
    lua = career_ops.generate_release_lua(playerid=158023)
    assert "ReleasePlayerFromTeam" in lua
    assert "158023" in lua


def test_budget_lua_set_and_get():
    s = career_ops.generate_set_transfer_budget_lua(amount=50_000_000)
    assert "SetTransferBudget" in s
    assert "50000000" in s
    g = career_ops.generate_get_transfer_budget_lua()
    assert "GetTransferBudget" in g


def test_transfer_rejects_bad_ids():
    with pytest.raises(ValueError):
        career_ops.generate_transfer_lua(playerid=0, to_teamid=11)
    with pytest.raises(ValueError):
        career_ops.generate_loan_lua(playerid=1, to_teamid=0)


def test_list_career_ops_inventory():
    ops = career_ops.list_career_ops()
    ids = {o["id"] for o in ops}
    for need in ("transfer", "loan", "release", "set_budget", "get_budget"):
        assert need in ids


def test_product_career_functions_exist():
    assert callable(product.career_transfer)
    assert callable(product.career_loan)
    assert callable(product.career_release)
    assert callable(product.career_set_budget)
    assert callable(product.career_get_budget)


def test_terminate_loan_lua():
    lua = career_ops.generate_terminate_loan_lua(playerid=99)
    assert "TerminateLoan" in lua
    assert "99" in lua
