"""Tests for MCP server tools (mocked client, no credentials needed)."""

import json
import os
import tempfile
from datetime import date
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

from cronometer_mcp.server import (
    replace_food_entries,
    list_replacement_history,
    rollback_replacement,
)


@pytest.fixture()
def audit_tmp(tmp_path):
    """Redirect audit journal writes to a temp directory."""
    audit_dir = tmp_path / "audit"
    audit_dir.mkdir()
    with patch("cronometer_mcp.server._get_audit_dir", return_value=audit_dir):
        yield audit_dir


def _mock_get_day_info(entries_by_date: dict):
    """Return a side_effect function for get_day_info that looks up by date."""

    def _side_effect(day: date) -> list[dict]:
        return entries_by_date.get(day.isoformat(), [])

    return _side_effect


def _serving(
    serving_id: str,
    food_source_id: int,
    diary_group: int = 1,
    quantity: float = 100.0,
    food_category_id: int = 0,
    measure_id: int = 0,
) -> dict:
    """Build a serving dict matching get_day_info output."""
    return {
        "serving_id": serving_id,
        "food_source_id": food_source_id,
        "food_category_id": food_category_id,
        "measure_id": measure_id,
        "quantity": quantity,
        "diary_group": diary_group,
    }


def _replacement(
    match_food_source_id: int,
    new_food_id: int = 99999,
    new_food_source_id: int = 99999,
    new_measure_id: int = 0,
    new_quantity: float = 200.0,
    new_weight_grams: float = 200.0,
) -> dict:
    return {
        "match_food_source_id": match_food_source_id,
        "new_food_id": new_food_id,
        "new_food_source_id": new_food_source_id,
        "new_measure_id": new_measure_id,
        "new_quantity": new_quantity,
        "new_weight_grams": new_weight_grams,
    }


class TestReplaceFoodEntries:
    """Tests for the replace_food_entries MCP tool."""

    @patch("cronometer_mcp.server._get_client")
    def test_dry_run_shows_plan(self, mock_get_client):
        """Dry run returns a plan without executing any changes."""
        client = MagicMock()
        mock_get_client.return_value = client
        client.get_day_info.side_effect = _mock_get_day_info(
            {
                "2026-03-08": [
                    _serving("D001", food_source_id=1000, diary_group=1),
                    _serving("D002", food_source_id=2000, diary_group=2),
                ],
            }
        )

        result = json.loads(
            replace_food_entries(
                replacements=json.dumps([_replacement(match_food_source_id=1000)]),
                start_date="2026-03-08",
                dry_run=True,
            )
        )

        assert result["status"] == "success"
        assert result["dry_run"] is True
        assert result["operations_planned"] == 1
        assert len(result["plan"]) == 1
        assert result["plan"][0]["old_serving_id"] == "D001"
        assert result["plan"][0]["old_food_source_id"] == 1000
        assert result["plan"][0]["new_food_id"] == 99999

        # Verify no mutations happened
        client.remove_serving.assert_not_called()
        client.add_serving.assert_not_called()

    @patch("cronometer_mcp.server._get_client")
    def test_dry_run_is_default(self, mock_get_client):
        """dry_run defaults to True when not specified."""
        client = MagicMock()
        mock_get_client.return_value = client
        client.get_day_info.side_effect = _mock_get_day_info(
            {
                "2026-03-08": [_serving("D001", food_source_id=1000)],
            }
        )

        # Call without explicit dry_run
        result = json.loads(
            replace_food_entries(
                replacements=json.dumps([_replacement(match_food_source_id=1000)]),
                start_date="2026-03-08",
            )
        )

        assert result["dry_run"] is True
        client.remove_serving.assert_not_called()
        client.add_serving.assert_not_called()

    @patch("cronometer_mcp.server._get_client")
    def test_apply_executes_replacements(self, mock_get_client, audit_tmp):
        """With dry_run=False, entries are deleted and re-added."""
        client = MagicMock()
        mock_get_client.return_value = client
        client.get_day_info.side_effect = _mock_get_day_info(
            {
                "2026-03-08": [
                    _serving("D001", food_source_id=1000, diary_group=1),
                ],
            }
        )
        client.remove_serving.return_value = True
        client.add_serving.return_value = {
            "serving_id": "Dnew01",
            "food_id": 99999,
            "food_source_id": 99999,
        }

        result = json.loads(
            replace_food_entries(
                replacements=json.dumps([_replacement(match_food_source_id=1000)]),
                start_date="2026-03-08",
                dry_run=False,
            )
        )

        assert result["status"] == "success"
        assert result["dry_run"] is False
        assert result["operations_completed"] == 1
        assert result["operations_failed"] == 0
        assert result["results"][0]["new_serving_id"] == "Dnew01"
        assert result["journal_id"]  # journal_id is present

        # Verify the correct calls were made
        client.remove_serving.assert_called_once_with("D001")
        client.add_serving.assert_called_once()
        call_kwargs = client.add_serving.call_args[1]
        assert call_kwargs["food_id"] == 99999
        assert call_kwargs["food_source_id"] == 99999
        assert call_kwargs["diary_group"] == 1
        assert call_kwargs["day"] == date(2026, 3, 8)

    @patch("cronometer_mcp.server._get_client")
    def test_no_matches_returns_empty(self, mock_get_client):
        """When no entries match, returns success with 0 operations."""
        client = MagicMock()
        mock_get_client.return_value = client
        client.get_day_info.side_effect = _mock_get_day_info(
            {
                "2026-03-08": [
                    _serving("D001", food_source_id=9999),
                ],
            }
        )

        result = json.loads(
            replace_food_entries(
                replacements=json.dumps([_replacement(match_food_source_id=1000)]),
                start_date="2026-03-08",
                dry_run=True,
            )
        )

        assert result["status"] == "success"
        assert result["operations_planned"] == 0

    @patch("cronometer_mcp.server._get_client")
    def test_max_operations_exceeded(self, mock_get_client):
        """Exceeding max_operations aborts before execution."""
        client = MagicMock()
        mock_get_client.return_value = client

        # 5 entries matching, but max_operations=3
        client.get_day_info.side_effect = _mock_get_day_info(
            {
                "2026-03-08": [
                    _serving(f"D{i:03d}", food_source_id=1000) for i in range(5)
                ],
            }
        )

        result = json.loads(
            replace_food_entries(
                replacements=json.dumps([_replacement(match_food_source_id=1000)]),
                start_date="2026-03-08",
                dry_run=False,
                max_operations=3,
            )
        )

        assert result["status"] == "error"
        assert result["operations_planned"] == 5
        assert "max_operations=3" in result["message"]

        # No mutations should have happened
        client.remove_serving.assert_not_called()

    @patch("cronometer_mcp.server._get_client")
    def test_multi_day_range(self, mock_get_client):
        """Replacements work across a multi-day date range."""
        client = MagicMock()
        mock_get_client.return_value = client
        client.get_day_info.side_effect = _mock_get_day_info(
            {
                "2026-03-08": [
                    _serving("D001", food_source_id=1000, diary_group=1),
                ],
                "2026-03-09": [
                    _serving("D002", food_source_id=1000, diary_group=3),
                ],
                "2026-03-10": [
                    _serving("D003", food_source_id=2000, diary_group=2),
                ],
            }
        )

        result = json.loads(
            replace_food_entries(
                replacements=json.dumps([_replacement(match_food_source_id=1000)]),
                start_date="2026-03-08",
                end_date="2026-03-10",
                dry_run=True,
            )
        )

        assert result["operations_planned"] == 2
        dates = [op["date"] for op in result["plan"]]
        assert "2026-03-08" in dates
        assert "2026-03-09" in dates
        # D003 has food_source_id=2000, not matched
        assert "2026-03-10" not in dates

    @patch("cronometer_mcp.server._get_client")
    def test_multiple_replacement_specs(self, mock_get_client):
        """Multiple replacement specs each match different entries."""
        client = MagicMock()
        mock_get_client.return_value = client
        client.get_day_info.side_effect = _mock_get_day_info(
            {
                "2026-03-08": [
                    _serving("D001", food_source_id=1000, diary_group=1),
                    _serving("D002", food_source_id=2000, diary_group=2),
                    _serving("D003", food_source_id=3000, diary_group=3),
                ],
            }
        )

        result = json.loads(
            replace_food_entries(
                replacements=json.dumps(
                    [
                        _replacement(match_food_source_id=1000, new_food_id=11111),
                        _replacement(match_food_source_id=3000, new_food_id=33333),
                    ]
                ),
                start_date="2026-03-08",
                dry_run=True,
            )
        )

        assert result["operations_planned"] == 2
        food_ids = {op["new_food_id"] for op in result["plan"]}
        assert food_ids == {11111, 33333}

    @patch("cronometer_mcp.server._get_client")
    def test_preserves_diary_group(self, mock_get_client, audit_tmp):
        """The replacement entry inherits the original's diary_group."""
        client = MagicMock()
        mock_get_client.return_value = client
        client.get_day_info.side_effect = _mock_get_day_info(
            {
                "2026-03-08": [
                    _serving("D001", food_source_id=1000, diary_group=4),
                ],
            }
        )
        client.remove_serving.return_value = True
        client.add_serving.return_value = {
            "serving_id": "Dnew01",
            "food_id": 99999,
            "food_source_id": 99999,
        }

        result = json.loads(
            replace_food_entries(
                replacements=json.dumps([_replacement(match_food_source_id=1000)]),
                start_date="2026-03-08",
                dry_run=False,
            )
        )

        assert result["status"] == "success"
        call_kwargs = client.add_serving.call_args[1]
        assert call_kwargs["diary_group"] == 4  # Snacks

    @patch("cronometer_mcp.server._get_client")
    def test_partial_failure_on_remove(self, mock_get_client, audit_tmp):
        """If remove_serving fails, the entry is skipped and reported as error."""
        client = MagicMock()
        mock_get_client.return_value = client
        client.get_day_info.side_effect = _mock_get_day_info(
            {
                "2026-03-08": [
                    _serving("D001", food_source_id=1000),
                    _serving("D002", food_source_id=1000),
                ],
            }
        )
        # First remove succeeds, second fails
        client.remove_serving.side_effect = [True, RuntimeError("Network error")]
        client.add_serving.return_value = {
            "serving_id": "Dnew01",
            "food_id": 99999,
            "food_source_id": 99999,
        }

        result = json.loads(
            replace_food_entries(
                replacements=json.dumps([_replacement(match_food_source_id=1000)]),
                start_date="2026-03-08",
                dry_run=False,
            )
        )

        assert result["status"] == "partial"
        assert result["operations_completed"] == 1
        assert result["operations_failed"] == 1
        assert result["errors"][0]["remove_status"] == "failed"

    def test_invalid_json_replacements(self):
        """Invalid JSON in replacements returns error."""
        result = json.loads(
            replace_food_entries(
                replacements="not valid json",
                start_date="2026-03-08",
            )
        )
        assert result["status"] == "error"
        assert "Invalid replacements JSON" in result["message"]

    def test_empty_replacements_array(self):
        """Empty replacements array returns error."""
        result = json.loads(
            replace_food_entries(
                replacements="[]",
                start_date="2026-03-08",
            )
        )
        assert result["status"] == "error"
        assert "non-empty" in result["message"]

    def test_missing_required_keys(self):
        """Replacement spec missing required keys returns error."""
        result = json.loads(
            replace_food_entries(
                replacements=json.dumps([{"match_food_source_id": 1000}]),
                start_date="2026-03-08",
            )
        )
        assert result["status"] == "error"
        assert "missing required keys" in result["message"]

    @patch("cronometer_mcp.server._get_client")
    def test_end_before_start_returns_error(self, mock_get_client):
        """end_date < start_date returns error."""
        mock_get_client.return_value = MagicMock()

        result = json.loads(
            replace_food_entries(
                replacements=json.dumps([_replacement(match_food_source_id=1000)]),
                start_date="2026-03-10",
                end_date="2026-03-08",
            )
        )
        assert result["status"] == "error"
        assert "end_date" in result["message"]


class TestAuditJournal:
    """Tests for Step 3: audit journal, idempotency, and rollback."""

    @patch("cronometer_mcp.server._get_client")
    def test_apply_saves_audit_journal(self, mock_get_client, audit_tmp):
        """Executing replacements creates a journal file in the audit dir."""
        client = MagicMock()
        mock_get_client.return_value = client
        client.get_day_info.side_effect = _mock_get_day_info(
            {
                "2026-03-08": [
                    _serving("D001", food_source_id=1000, diary_group=1),
                ],
            }
        )
        client.remove_serving.return_value = True
        client.add_serving.return_value = {
            "serving_id": "Dnew01",
            "food_id": 99999,
            "food_source_id": 99999,
        }

        result = json.loads(
            replace_food_entries(
                replacements=json.dumps([_replacement(match_food_source_id=1000)]),
                start_date="2026-03-08",
                dry_run=False,
            )
        )

        assert result["status"] == "success"
        assert result["journal_id"]
        assert result["journal_path"]

        # Verify journal file exists and has correct content
        journal_files = list(audit_tmp.glob("*.json"))
        assert len(journal_files) == 1
        journal = json.loads(journal_files[0].read_text())
        assert journal["journal_id"] == result["journal_id"]
        assert journal["status"] == "success"
        assert journal["operations_completed"] == 1
        assert len(journal["before"]) == 1
        assert journal["before"][0]["serving_id"] == "D001"
        assert len(journal["after"]) == 1
        assert journal["after"][0]["new_serving_id"] == "Dnew01"

    @patch("cronometer_mcp.server._get_client")
    def test_idempotency_key_prevents_duplicate(self, mock_get_client, audit_tmp):
        """Second call with same idempotency_key returns already_executed."""
        client = MagicMock()
        mock_get_client.return_value = client
        client.get_day_info.side_effect = _mock_get_day_info(
            {
                "2026-03-08": [
                    _serving("D001", food_source_id=1000, diary_group=1),
                ],
            }
        )
        client.remove_serving.return_value = True
        client.add_serving.return_value = {
            "serving_id": "Dnew01",
            "food_id": 99999,
            "food_source_id": 99999,
        }

        # First call — should execute
        result1 = json.loads(
            replace_food_entries(
                replacements=json.dumps([_replacement(match_food_source_id=1000)]),
                start_date="2026-03-08",
                dry_run=False,
                idempotency_key="test-key-001",
            )
        )
        assert result1["status"] == "success"
        assert result1["journal_id"]

        # Second call — same key, should return already_executed
        result2 = json.loads(
            replace_food_entries(
                replacements=json.dumps([_replacement(match_food_source_id=1000)]),
                start_date="2026-03-08",
                dry_run=False,
                idempotency_key="test-key-001",
            )
        )
        assert result2["status"] == "already_executed"
        assert result2["journal_id"] == result1["journal_id"]
        assert result2["previous_result"]["operations_completed"] == 1

        # Verify client was only called once (for the first execution)
        assert client.remove_serving.call_count == 1

    @patch("cronometer_mcp.server._get_client")
    def test_idempotency_key_ignored_on_dry_run(self, mock_get_client, audit_tmp):
        """Idempotency key check is skipped for dry-run calls."""
        client = MagicMock()
        mock_get_client.return_value = client
        client.get_day_info.side_effect = _mock_get_day_info(
            {
                "2026-03-08": [
                    _serving("D001", food_source_id=1000, diary_group=1),
                ],
            }
        )

        # Dry-run with idempotency key should always succeed (never "already_executed")
        result = json.loads(
            replace_food_entries(
                replacements=json.dumps([_replacement(match_food_source_id=1000)]),
                start_date="2026-03-08",
                dry_run=True,
                idempotency_key="test-key-dry",
            )
        )
        assert result["status"] == "success"
        assert result["dry_run"] is True

    @patch("cronometer_mcp.server._get_client")
    def test_list_replacement_history(self, mock_get_client, audit_tmp):
        """list_replacement_history returns recent journals."""
        client = MagicMock()
        mock_get_client.return_value = client
        client.get_day_info.side_effect = _mock_get_day_info(
            {
                "2026-03-08": [
                    _serving("D001", food_source_id=1000, diary_group=1),
                ],
            }
        )
        client.remove_serving.return_value = True
        client.add_serving.return_value = {
            "serving_id": "Dnew01",
            "food_id": 99999,
            "food_source_id": 99999,
        }

        # Execute a replacement to create a journal
        exec_result = json.loads(
            replace_food_entries(
                replacements=json.dumps([_replacement(match_food_source_id=1000)]),
                start_date="2026-03-08",
                dry_run=False,
            )
        )
        assert exec_result["status"] == "success"

        # List history
        history_result = json.loads(list_replacement_history(limit=10))
        assert history_result["status"] == "success"
        assert history_result["count"] == 1
        assert history_result["journals"][0]["journal_id"] == exec_result["journal_id"]
        assert history_result["journals"][0]["operations_completed"] == 1

    def test_list_replacement_history_empty(self, audit_tmp):
        """list_replacement_history returns empty list when no journals exist."""
        result = json.loads(list_replacement_history(limit=10))
        assert result["status"] == "success"
        assert result["count"] == 0
        assert result["journals"] == []

    @patch("cronometer_mcp.server._get_client")
    def test_rollback_dry_run(self, mock_get_client, audit_tmp):
        """rollback_replacement in dry-run mode shows the rollback plan."""
        client = MagicMock()
        mock_get_client.return_value = client
        client.get_day_info.side_effect = _mock_get_day_info(
            {
                "2026-03-08": [
                    _serving("D001", food_source_id=1000, diary_group=1),
                ],
            }
        )
        client.remove_serving.return_value = True
        client.add_serving.return_value = {
            "serving_id": "Dnew01",
            "food_id": 99999,
            "food_source_id": 99999,
        }

        # Execute first
        exec_result = json.loads(
            replace_food_entries(
                replacements=json.dumps([_replacement(match_food_source_id=1000)]),
                start_date="2026-03-08",
                dry_run=False,
            )
        )
        journal_id = exec_result["journal_id"]

        # Rollback dry-run
        rollback_result = json.loads(
            rollback_replacement(journal_id=journal_id, dry_run=True)
        )
        assert rollback_result["status"] == "success"
        assert rollback_result["dry_run"] is True
        assert rollback_result["operations_planned"] == 1
        assert rollback_result["plan"][0]["remove_new_serving_id"] == "Dnew01"
        assert rollback_result["plan"][0]["restore_food_source_id"] == 1000

        # Verify no mutations happened during dry-run rollback
        # (remove_serving was called once during the execute, not again)
        assert client.remove_serving.call_count == 1

    @patch("cronometer_mcp.server._get_client")
    def test_rollback_executes(self, mock_get_client, audit_tmp):
        """rollback_replacement with dry_run=False removes new entries and restores originals."""
        client = MagicMock()
        mock_get_client.return_value = client
        client.get_day_info.side_effect = _mock_get_day_info(
            {
                "2026-03-08": [
                    _serving("D001", food_source_id=1000, diary_group=1),
                ],
            }
        )
        client.remove_serving.return_value = True
        client.add_serving.return_value = {
            "serving_id": "Dnew01",
            "food_id": 99999,
            "food_source_id": 99999,
        }

        # Execute replacement
        exec_result = json.loads(
            replace_food_entries(
                replacements=json.dumps([_replacement(match_food_source_id=1000)]),
                start_date="2026-03-08",
                dry_run=False,
            )
        )
        journal_id = exec_result["journal_id"]

        # Reset mock call tracking
        client.remove_serving.reset_mock()
        client.add_serving.reset_mock()

        # Set up add_serving return for the rollback restore
        client.add_serving.return_value = {
            "serving_id": "Drestored01",
            "food_id": 1000,
            "food_source_id": 1000,
        }

        # Execute rollback
        rollback_result = json.loads(
            rollback_replacement(journal_id=journal_id, dry_run=False)
        )
        assert rollback_result["status"] == "success"
        assert rollback_result["dry_run"] is False
        assert rollback_result["operations_completed"] == 1
        assert rollback_result["operations_failed"] == 0

        # Verify: removed the new entry
        client.remove_serving.assert_called_once_with("Dnew01")

        # Verify: re-added original entry
        client.add_serving.assert_called_once()
        call_kwargs = client.add_serving.call_args[1]
        assert call_kwargs["food_source_id"] == 1000
        assert call_kwargs["diary_group"] == 1

        # Verify journal is marked as rolled_back
        journal = json.loads((audit_tmp / f"{journal_id}.json").read_text())
        assert journal["status"] == "rolled_back"
        assert "rolled_back_at" in journal

    @patch("cronometer_mcp.server._get_client")
    def test_rollback_already_rolled_back(self, mock_get_client, audit_tmp):
        """Attempting to rollback an already rolled-back journal returns error."""
        client = MagicMock()
        mock_get_client.return_value = client
        client.get_day_info.side_effect = _mock_get_day_info(
            {
                "2026-03-08": [
                    _serving("D001", food_source_id=1000, diary_group=1),
                ],
            }
        )
        client.remove_serving.return_value = True
        client.add_serving.return_value = {
            "serving_id": "Dnew01",
            "food_id": 99999,
            "food_source_id": 99999,
        }

        # Execute
        exec_result = json.loads(
            replace_food_entries(
                replacements=json.dumps([_replacement(match_food_source_id=1000)]),
                start_date="2026-03-08",
                dry_run=False,
            )
        )
        journal_id = exec_result["journal_id"]

        # First rollback
        rollback_replacement(journal_id=journal_id, dry_run=False)

        # Second rollback — should error
        result = json.loads(rollback_replacement(journal_id=journal_id, dry_run=False))
        assert result["status"] == "error"
        assert "already rolled back" in result["message"]

    def test_rollback_not_found(self, audit_tmp):
        """Rollback with non-existent journal_id returns error."""
        result = json.loads(
            rollback_replacement(journal_id="nonexistent_12345", dry_run=True)
        )
        assert result["status"] == "error"
        assert "not found" in result["message"]
