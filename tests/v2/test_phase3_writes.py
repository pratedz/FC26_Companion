"""Phase 3 contract tests for single-record resident-core writes.

The Lua host itself is only available inside FC/Live Editor, so these tests
lock down the Python wire contract and the safety/read-back primitives that
must be present in the shipped worker source.
"""

from pathlib import Path

from companion.domain.job import Job, op_budget, op_snapshot, op_transfer


ROOT = Path(__file__).resolve().parents[2]
OPS = (ROOT / "ingame" / "le_companion" / "ops.lua").read_text(encoding="utf-8")
DB = (ROOT / "ingame" / "le_companion" / "db.lua").read_text(encoding="utf-8")


def _wire(op):
    return Job(ops=(op,), dry_run=True).to_wire(now=1)["ops"][0]


def test_transfer_builder_uses_documented_host_contract_names():
    wire = _wire(
        op_transfer(
            "move",
            action="transfer",
            playerid=158023,
            teamid=2,
            months=60,
            fee=123,
            wage=5000,
            from_teamid=1,
            release_clause=-1,
        )
    )
    assert wire["to_teamid"] == 2
    assert wire["transfersum"] == 123
    assert wire["from_teamid"] == 1
    assert wire["release_clause"] == -1
    assert wire["verify"] is True
    assert "teamid" not in wire
    assert "fee" not in wire


def test_loan_and_list_contract_fields_are_explicit():
    loan = _wire(
        op_transfer(
            "loan",
            action="loan",
            playerid=9,
            teamid=10,
            months=12,
            loan_to_buy=-1,
        )
    )
    listed = _wire(
        op_transfer(
            "list",
            action="list_player",
            playerid=9,
            list_kind="loan",
        )
    )
    assert loan["to_teamid"] == 10
    assert loan["loan_to_buy"] == -1
    assert listed["list"] == "loan"


def test_budget_builder_matches_phase3_amount_target_contract():
    wire = _wire(op_budget("money", action="set", transfer=50_000_000, scope="user"))
    assert wire["action"] == "set"
    assert wire["target"] == "user"
    assert wire["amount"] == 50_000_000
    assert "scope" not in wire
    assert "transfer" not in wire


def test_snapshot_builder_and_core_support_star_and_output():
    wire = _wire(
        op_snapshot(
            "before",
            playerids=[158023],
            fields="*",
            out_path="C:/safe/snapshot.json",
        )
    )
    assert wire["fields"] == "*"
    assert wire["out_path"].endswith("snapshot.json")
    assert 'if fields == "*"' in OPS
    assert "snapshot_path_denied" in OPS
    assert "util.write_atomic(out_path" in OPS


def test_set_fields_upsert_refuses_duplicates_and_verifies_insert():
    assert "function db.scan_matches" in DB
    assert '"duplicate_rows"' in OPS
    assert '"insert_unverified"' in OPS
    assert "db.set_many(h, rec, fields" in OPS


def test_transfer_worker_uses_fc26_apis_and_readback():
    for api in (
        "TransferPlayer",
        "LoanPlayer",
        "ReleasePlayerFromTeam",
        "TerminateLoan",
        "AddPlayerToTransferList",
        "AddPlayerToLoanList",
        "RemovePlayerFromLists",
        "GetTeamIdFromPlayerId",
    ):
        assert api in OPS
    # These guessed aliases were the old non-functional implementation.
    for wrong in ("PlayerTransfer", "PlayerLoan", "PlayerRelease", "PlayerTerminateLoan"):
        assert f'"{wrong}"' not in OPS
    assert '"verify_mismatch"' in OPS
    assert '"unverified_side_effect"' in OPS


def test_budget_worker_never_falls_back_to_deprecated_stub():
    assert '"SetUserTransferBudget"' in OPS
    assert '"SetCPUTransferBudget"' in OPS
    assert '"GetUserTransferBudget"' in OPS
    assert '"GetCPUTransferBudget"' in OPS
    assert '"SetTransferBudget"' not in OPS
    assert '"GetTransferBudget"' not in OPS
