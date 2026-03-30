"""MCP server for Cronometer nutrition data."""

import json
import logging
import os
import uuid
from datetime import date, datetime, timedelta
from pathlib import Path

from mcp.server.fastmcp import FastMCP

from .client import CronometerClient
from .markdown import generate_food_log_md

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

mcp = FastMCP(
    "cronometer",
    instructions=(
        "Cronometer MCP server for nutrition tracking. "
        "Provides access to detailed food logs, daily macro/micro summaries, "
        "exercise data, and biometrics from Cronometer Gold. "
        "Use get_food_log for individual food entries with full nutrition, "
        "get_daily_nutrition for daily macro totals, and get_micronutrients "
        "for detailed vitamin/mineral breakdowns."
    ),
)

_client: CronometerClient | None = None


def _get_client() -> CronometerClient:
    global _client
    if _client is None:
        _client = CronometerClient()
    return _client


def _parse_date(d: str | None) -> date | None:
    if d is None:
        return None
    return date.fromisoformat(d)


# Non-nutrient metadata columns to exclude from nutrient extraction
_META_COLS = {
    "Day",
    "Date",
    "Time",
    "Group",
    "Food Name",
    "Amount",
    "Unit",
    "Category",
    "Completed",
}

# Macro columns (energy + macronutrients)
_MACRO_KEYWORDS = {
    "Energy",
    "Protein",
    "Carbs",
    "Fat",
    "Fiber",
    "Net Carbs",
    "Sugars",
    "Sugar Alcohol",
    "Starch",
    "Saturated",
    "Monounsaturated",
    "Polyunsaturated",
    "Trans-Fats",
    "Cholesterol",
    "Sodium",
    "Potassium",
    "Water",
    "Alcohol",
    "Caffeine",
    "Omega-3",
    "Omega-6",
}

# Amino acid columns
_AMINO_KEYWORDS = {
    "Cystine",
    "Histidine",
    "Isoleucine",
    "Leucine",
    "Lysine",
    "Methionine",
    "Phenylalanine",
    "Threonine",
    "Tryptophan",
    "Tyrosine",
    "Valine",
}


def _classify_column(col: str) -> str:
    """Classify a column as 'meta', 'macro', 'amino', or 'micro'."""
    if col in _META_COLS:
        return "meta"
    base = col.split("(")[0].strip()
    if base in _MACRO_KEYWORDS:
        return "macro"
    if base in _AMINO_KEYWORDS:
        return "amino"
    return "micro"


def _extract_nutrients(row: dict, category: str | None = None) -> dict:
    """Extract nutrient values from a row, optionally filtered by category."""
    result = {}
    for col, val in row.items():
        if _classify_column(col) == "meta":
            continue
        if category and _classify_column(col) != category:
            continue
        val = str(val).strip()
        if val:
            try:
                num = float(val)
                if num != 0.0:
                    result[col] = round(num, 2)
            except ValueError:
                pass
    return result


def _format_servings(rows: list[dict]) -> list[dict]:
    """Format servings export into a cleaner structure."""
    formatted = []
    for row in rows:
        entry = {
            "date": row.get("Day", ""),
            "time": row.get("Time", ""),
            "meal": row.get("Group", ""),
            "food": row.get("Food Name", ""),
            "amount": row.get("Amount", ""),
            "category": row.get("Category", ""),
            "macros": _extract_nutrients(row, "macro"),
            "micros": _extract_nutrients(row, "micro"),
        }
        formatted.append(entry)
    return formatted


@mcp.tool()
def get_food_log(
    start_date: str | None = None,
    end_date: str | None = None,
) -> str:
    """Get detailed food log with individual food entries and full nutrition.

    Returns every food entry with macros and micronutrients.
    Great for analyzing what was eaten and spotting nutrient gaps.

    Args:
        start_date: Start date as YYYY-MM-DD (defaults to today).
        end_date: End date as YYYY-MM-DD (defaults to today).
    """
    try:
        client = _get_client()
        start = _parse_date(start_date)
        end = _parse_date(end_date)
        rows = client.get_food_log(start, end)
        formatted = _format_servings(rows)

        # Group by date
        by_date: dict[str, list] = {}
        for entry in formatted:
            d = entry["date"]
            by_date.setdefault(d, []).append(entry)

        return json.dumps(
            {
                "status": "success",
                "date_range": {
                    "start": start_date or str(date.today()),
                    "end": end_date or str(date.today()),
                },
                "total_entries": len(formatted),
                "days": {
                    d: {
                        "entries": entries,
                        "total_calories": round(
                            sum(e["macros"].get("Energy (kcal)", 0) for e in entries), 1
                        ),
                        "total_protein": round(
                            sum(e["macros"].get("Protein (g)", 0) for e in entries), 1
                        ),
                        "total_carbs": round(
                            sum(e["macros"].get("Carbs (g)", 0) for e in entries), 1
                        ),
                        "total_fat": round(
                            sum(e["macros"].get("Fat (g)", 0) for e in entries), 1
                        ),
                    }
                    for d, entries in by_date.items()
                },
            },
            indent=2,
        )
    except Exception as e:
        return json.dumps({"status": "error", "message": f"{type(e).__name__}: {e}"})


@mcp.tool()
def get_daily_nutrition(
    start_date: str | None = None,
    end_date: str | None = None,
) -> str:
    """Get daily nutrition summary with macro totals per day.

    Returns calorie, protein, carb, fat, and fiber totals for each day.
    Use this for quick daily overviews and trend analysis.

    Args:
        start_date: Start date as YYYY-MM-DD (defaults to 7 days ago).
        end_date: End date as YYYY-MM-DD (defaults to today).
    """
    try:
        client = _get_client()
        start = _parse_date(start_date) or (date.today() - timedelta(days=7))
        end = _parse_date(end_date)
        rows = client.get_daily_summary(start, end)

        summaries = []
        for row in rows:
            summaries.append(
                {
                    "date": row.get("Date", ""),
                    "macros": _extract_nutrients(row, "macro"),
                    "micros": _extract_nutrients(row, "micro"),
                }
            )

        return json.dumps(
            {
                "status": "success",
                "date_range": {
                    "start": str(start),
                    "end": str(end or date.today()),
                },
                "days": summaries,
            },
            indent=2,
        )
    except Exception as e:
        return json.dumps({"status": "error", "message": f"{type(e).__name__}: {e}"})


@mcp.tool()
def get_micronutrients(
    start_date: str | None = None,
    end_date: str | None = None,
) -> str:
    """Get detailed micronutrient breakdown for meal planning.

    Shows vitamins, minerals, and other micronutrients per day with
    period averages. Use this to identify nutrient gaps and plan meals.

    Args:
        start_date: Start date as YYYY-MM-DD (defaults to 7 days ago).
        end_date: End date as YYYY-MM-DD (defaults to today).
    """
    try:
        client = _get_client()
        start = _parse_date(start_date) or (date.today() - timedelta(days=7))
        end = _parse_date(end_date)
        rows = client.get_daily_summary(start, end)

        days = []
        for row in rows:
            micros = _extract_nutrients(row, "micro")
            if micros:
                days.append(
                    {
                        "date": row.get("Date", ""),
                        "micronutrients": micros,
                    }
                )

        # Compute averages across the range
        averages = {}
        if days:
            all_keys = set()
            for d in days:
                all_keys.update(d["micronutrients"].keys())
            for key in sorted(all_keys):
                vals = [
                    d["micronutrients"][key]
                    for d in days
                    if key in d["micronutrients"]
                    and isinstance(d["micronutrients"][key], (int, float))
                ]
                if vals:
                    averages[key] = round(sum(vals) / len(vals), 2)

        return json.dumps(
            {
                "status": "success",
                "date_range": {
                    "start": str(start),
                    "end": str(end or date.today()),
                },
                "daily_breakdown": days,
                "period_averages": averages,
            },
            indent=2,
        )
    except Exception as e:
        return json.dumps({"status": "error", "message": f"{type(e).__name__}: {e}"})


@mcp.tool()
def export_raw_csv(
    export_type: str,
    start_date: str | None = None,
    end_date: str | None = None,
) -> str:
    """Export raw CSV data from Cronometer for any data type.

    Useful when you need the full unprocessed export.

    Args:
        export_type: One of 'servings', 'daily_summary', 'exercises',
                    'biometrics', 'notes'.
        start_date: Start date as YYYY-MM-DD (defaults to today).
        end_date: End date as YYYY-MM-DD (defaults to today).
    """
    try:
        client = _get_client()
        start = _parse_date(start_date)
        end = _parse_date(end_date)
        raw = client.export_raw(export_type, start, end)
        if len(raw) > 50000:
            return json.dumps(
                {
                    "status": "success",
                    "truncated": True,
                    "total_chars": len(raw),
                    "data": raw[:50000] + "\n... (truncated)",
                }
            )
        return json.dumps({"status": "success", "data": raw})
    except Exception as e:
        return json.dumps({"status": "error", "message": f"{type(e).__name__}: {e}"})


_DIARY_GROUP_MAP: dict[str, int] = {
    "breakfast": 1,
    "lunch": 2,
    "dinner": 3,
    "snacks": 4,
}


@mcp.tool()
def search_foods(query: str) -> str:
    """Search Cronometer's food database by name.

    Returns matching foods with their IDs and source information needed
    to add a serving (food_id, food_source_id, measure_id).

    Args:
        query: Food name or keyword to search for (e.g. "eggs", "chicken breast").
    """
    try:
        client = _get_client()
        foods = client.find_foods(query)
        return json.dumps(
            {
                "status": "success",
                "query": query,
                "count": len(foods),
                "foods": foods,
            },
            indent=2,
        )
    except Exception as e:
        return json.dumps({"status": "error", "message": f"{type(e).__name__}: {e}"})


@mcp.tool()
def get_food_details(food_source_id: int) -> str:
    """Get detailed food information including available serving measures.

    Use this after search_foods to get the measure_id needed for add_food_entry.
    Returns all available serving sizes with their numeric IDs and gram weights.

    Args:
        food_source_id: Food source ID from search_foods results.
    """
    try:
        client = _get_client()
        result = client.get_food(food_source_id)
        # Remove raw_response from the output to keep it clean
        output = {
            "status": "success",
            "food_source_id": result["food_source_id"],
            "measures": result["measures"],
        }
        return json.dumps(output, indent=2)
    except Exception as e:
        return json.dumps({"status": "error", "message": f"{type(e).__name__}: {e}"})


@mcp.tool()
def add_food_entry(
    food_id: int,
    food_source_id: int,
    weight_grams: float,
    date: str,
    measure_id: int = 0,
    quantity: float = 0,
    diary_group: str = "Breakfast",
    time: str | None = None,
) -> str:
    """Add a food entry to the Cronometer diary.

    Use search_foods to find food_id and food_source_id, then
    get_food_details for measure_id and weight_grams.

    For CRDB/custom foods, you can omit measure_id (defaults to a
    universal NCCDB measure that works for all food sources).
    When measure_id is omitted, quantity is set to weight_grams.

    Args:
        food_id: Numeric food ID from search_foods results.
        food_source_id: Food source ID from search_foods results.
        weight_grams: Weight of the serving in grams.
        date: Date to log the entry as YYYY-MM-DD (e.g. "2026-03-04").
        measure_id: Measure/unit ID. Pass 0 (default) to use the universal
                    measure that works for all food sources.
        quantity: Number of servings. Defaults to weight_grams when
                  measure_id is 0 (universal gram-based measure).
        diary_group: Meal slot — one of "Breakfast", "Lunch", "Dinner", "Snacks"
                     (case-insensitive, defaults to "Breakfast").
        time: Time of day as HH:MM in 24-hour format (e.g. "08:30", "15:35").
              Defaults to a sensible time based on diary_group if not specified
              (Breakfast=08:00, Lunch=12:00, Dinner=18:00, Snacks=15:00).
    """
    try:
        group_key = diary_group.strip().lower()
        group_int = _DIARY_GROUP_MAP.get(group_key)
        if group_int is None:
            return json.dumps(
                {
                    "status": "error",
                    "message": (
                        f"Invalid diary_group '{diary_group}'. "
                        "Must be one of: Breakfast, Lunch, Dinner, Snacks."
                    ),
                }
            )

        if measure_id == 0 and quantity == 0:
            quantity = weight_grams

        # Parse optional time parameter
        entry_hour: int | None = None
        entry_minute: int | None = None
        if time is not None:
            try:
                parts = time.strip().split(":")
                entry_hour = int(parts[0])
                entry_minute = int(parts[1]) if len(parts) > 1 else 0
                if not (0 <= entry_hour <= 23 and 0 <= entry_minute <= 59):
                    return json.dumps(
                        {
                            "status": "error",
                            "message": (
                                f"Invalid time '{time}'. Hour must be 0-23, "
                                "minute must be 0-59."
                            ),
                        }
                    )
            except (ValueError, IndexError):
                return json.dumps(
                    {
                        "status": "error",
                        "message": (
                            f"Invalid time format '{time}'. "
                            "Use HH:MM in 24-hour format (e.g. '08:30', '15:35')."
                        ),
                    }
                )

        from datetime import date as date_type

        log_date = date_type.fromisoformat(date)

        client = _get_client()
        result = client.add_serving(
            food_id=food_id,
            food_source_id=food_source_id,
            measure_id=measure_id,
            quantity=quantity,
            weight_grams=weight_grams,
            day=log_date,
            diary_group=group_int,
            hour=entry_hour,
            minute=entry_minute,
        )
        return json.dumps(
            {
                "status": "success",
                "entry": result,
                "note": (
                    "Use the serving_id to remove this entry with remove_food_entry "
                    "if needed."
                ),
            },
            indent=2,
        )
    except Exception as e:
        return json.dumps({"status": "error", "message": f"{type(e).__name__}: {e}"})


@mcp.tool()
def remove_food_entry(serving_id: str) -> str:
    """Remove a food entry from the Cronometer diary.

    Args:
        serving_id: The serving ID returned by add_food_entry (e.g. "D80lp$").
    """
    try:
        client = _get_client()
        client.remove_serving(serving_id)
        return json.dumps(
            {
                "status": "success",
                "serving_id": serving_id,
                "message": "Serving removed from diary.",
            },
            indent=2,
        )
    except Exception as e:
        return json.dumps({"status": "error", "message": f"{type(e).__name__}: {e}"})


_DIARY_GROUP_NAMES = {
    0: "Uncategorized",
    1: "Breakfast",
    2: "Lunch",
    3: "Dinner",
    4: "Snacks",
}


@mcp.tool()
def get_diary_entries(
    start_date: str | None = None,
    end_date: str | None = None,
) -> str:
    """Get diary entries with serving IDs for a date range.

    Returns every food entry with its serving_id, food_source_id,
    food_category_id, measure_id, quantity, and diary group. The
    serving_id is required by remove_food_entry to delete entries.

    This is the primary tool for reading diary entries when you need
    to identify, modify, or replace specific entries.

    Args:
        start_date: Start date as YYYY-MM-DD (defaults to today).
        end_date: End date as YYYY-MM-DD (defaults to start_date).
    """
    try:
        client = _get_client()
        start = _parse_date(start_date) or date.today()
        end = _parse_date(end_date) or start

        if end < start:
            return json.dumps(
                {
                    "status": "error",
                    "message": "end_date must be >= start_date",
                }
            )

        all_entries: dict[str, list] = {}
        current = start
        while current <= end:
            servings = client.get_day_info(current)
            day_str = current.isoformat()
            entries = []
            for s in servings:
                entries.append(
                    {
                        "serving_id": s["serving_id"],
                        "food_source_id": s["food_source_id"],
                        "food_category_id": s["food_category_id"],
                        "measure_id": s["measure_id"],
                        "quantity": s["quantity"],
                        "diary_group": _DIARY_GROUP_NAMES.get(
                            s["diary_group"], f"Group {s['diary_group']}"
                        ),
                    }
                )
            all_entries[day_str] = entries
            current += timedelta(days=1)

        total = sum(len(v) for v in all_entries.values())
        return json.dumps(
            {
                "status": "success",
                "date_range": {
                    "start": start.isoformat(),
                    "end": end.isoformat(),
                },
                "total_entries": total,
                "days": all_entries,
            },
            indent=2,
        )
    except Exception as e:
        return json.dumps({"status": "error", "message": f"{type(e).__name__}: {e}"})


@mcp.tool()
def replace_food_entries(
    replacements: str,
    start_date: str | None = None,
    end_date: str | None = None,
    dry_run: bool = True,
    max_operations: int = 50,
    idempotency_key: str | None = None,
) -> str:
    """Replace diary entries by deleting old ones and adding new ones.

    Matches existing diary entries by food_source_id and replaces them
    with the specified new food. Operates in dry-run mode by default —
    call with dry_run=false to execute.

    Each replacement in the JSON array specifies which entries to match
    (by food_source_id) and what to replace them with. The new entry
    inherits the original's diary_group (meal slot) and date.

    All executed operations are saved to an audit journal for rollback.
    Use list_replacement_history to view past operations and
    rollback_replacement to undo them.

    Args:
        replacements: JSON array of replacement specs. Each element:
            {
                "match_food_source_id": 12345,
                "new_food_id": 67890,
                "new_food_source_id": 67890,
                "new_measure_id": 0,
                "new_quantity": 200,
                "new_weight_grams": 200
            }
            - match_food_source_id: food_source_id to match in existing entries.
            - new_food_id: food ID for the replacement entry.
            - new_food_source_id: food source ID for the replacement entry.
            - new_measure_id: measure ID (0 = universal gram-based measure).
            - new_quantity: quantity for the replacement entry.
            - new_weight_grams: weight in grams for the replacement entry.
        start_date: Start date as YYYY-MM-DD (defaults to today).
        end_date: End date as YYYY-MM-DD (defaults to start_date).
        dry_run: If true (default), only show the plan without executing.
                 Set to false to actually delete and re-add entries.
        max_operations: Maximum number of delete+add pairs allowed (default 50).
                        Aborts if the plan exceeds this limit.
        idempotency_key: Optional unique key to prevent duplicate executions.
                         If a journal with this key already exists, the previous
                         result is returned instead of re-executing.
    """
    try:
        # Idempotency check: if key was already used, return previous result
        if idempotency_key and not dry_run:
            existing = _find_journal_by_idempotency_key(idempotency_key)
            if existing:
                return json.dumps(
                    {
                        "status": "already_executed",
                        "message": (
                            f"Operation with idempotency_key='{idempotency_key}' "
                            f"was already executed."
                        ),
                        "journal_id": existing["journal_id"],
                        "previous_result": {
                            "operations_completed": existing.get(
                                "operations_completed", 0
                            ),
                            "operations_failed": existing.get("operations_failed", 0),
                            "timestamp": existing.get("timestamp"),
                        },
                    },
                    indent=2,
                )

        # Parse replacements JSON
        try:
            specs = json.loads(replacements)
        except json.JSONDecodeError as exc:
            return json.dumps(
                {
                    "status": "error",
                    "message": f"Invalid replacements JSON: {exc}",
                }
            )

        if not isinstance(specs, list) or not specs:
            return json.dumps(
                {
                    "status": "error",
                    "message": "replacements must be a non-empty JSON array.",
                }
            )

        # Validate each spec
        required_keys = {
            "match_food_source_id",
            "new_food_id",
            "new_food_source_id",
            "new_quantity",
            "new_weight_grams",
        }
        for i, spec in enumerate(specs):
            missing = required_keys - set(spec.keys())
            if missing:
                return json.dumps(
                    {
                        "status": "error",
                        "message": (
                            f"Replacement #{i} missing required keys: "
                            f"{', '.join(sorted(missing))}"
                        ),
                    }
                )

        # Build lookup: match_food_source_id → replacement spec
        match_map: dict[int, dict] = {}
        for spec in specs:
            match_map[int(spec["match_food_source_id"])] = spec

        client = _get_client()
        start = _parse_date(start_date) or date.today()
        end = _parse_date(end_date) or start

        if end < start:
            return json.dumps(
                {
                    "status": "error",
                    "message": "end_date must be >= start_date",
                }
            )

        # Collect matching entries across the date range
        plan: list[dict] = []
        current = start
        while current <= end:
            servings = client.get_day_info(current)
            for s in servings:
                if s["food_source_id"] in match_map:
                    spec = match_map[s["food_source_id"]]
                    plan.append(
                        {
                            "date": current.isoformat(),
                            "old_serving_id": s["serving_id"],
                            "old_food_source_id": s["food_source_id"],
                            "old_quantity": s["quantity"],
                            "old_diary_group": _DIARY_GROUP_NAMES.get(
                                s["diary_group"], f"Group {s['diary_group']}"
                            ),
                            "old_diary_group_int": s["diary_group"],
                            "new_food_id": int(spec["new_food_id"]),
                            "new_food_source_id": int(spec["new_food_source_id"]),
                            "new_measure_id": int(spec.get("new_measure_id", 0)),
                            "new_quantity": float(spec["new_quantity"]),
                            "new_weight_grams": float(spec["new_weight_grams"]),
                        }
                    )
            current += timedelta(days=1)

        if not plan:
            return json.dumps(
                {
                    "status": "success",
                    "dry_run": dry_run,
                    "message": "No matching entries found.",
                    "operations_planned": 0,
                },
                indent=2,
            )

        if len(plan) > max_operations:
            return json.dumps(
                {
                    "status": "error",
                    "message": (
                        f"Plan has {len(plan)} operations, exceeding "
                        f"max_operations={max_operations}. Increase the limit "
                        f"or narrow the date range."
                    ),
                    "operations_planned": len(plan),
                }
            )

        # Build a clean plan summary (without internal fields)
        plan_summary = [
            {
                "date": op["date"],
                "old_serving_id": op["old_serving_id"],
                "old_food_source_id": op["old_food_source_id"],
                "old_quantity": op["old_quantity"],
                "diary_group": op["old_diary_group"],
                "new_food_id": op["new_food_id"],
                "new_food_source_id": op["new_food_source_id"],
                "new_measure_id": op["new_measure_id"],
                "new_quantity": op["new_quantity"],
                "new_weight_grams": op["new_weight_grams"],
            }
            for op in plan
        ]

        if dry_run:
            return json.dumps(
                {
                    "status": "success",
                    "dry_run": True,
                    "message": (
                        f"Would replace {len(plan)} entries. "
                        f"Set dry_run=false to execute."
                    ),
                    "operations_planned": len(plan),
                    "plan": plan_summary,
                },
                indent=2,
            )

        # Execute: delete old, add new
        results: list[dict] = []
        errors: list[dict] = []

        # Before-snapshot for audit journal
        before_snapshot = [
            {
                "date": op["date"],
                "serving_id": op["old_serving_id"],
                "food_source_id": op["old_food_source_id"],
                "quantity": op["old_quantity"],
                "diary_group": op["old_diary_group"],
                "diary_group_int": op["old_diary_group_int"],
            }
            for op in plan
        ]

        for op in plan:
            op_result = {
                "date": op["date"],
                "old_serving_id": op["old_serving_id"],
                "diary_group": op["old_diary_group"],
            }

            # Step 1: Remove old entry
            try:
                client.remove_serving(op["old_serving_id"])
                op_result["remove_status"] = "success"
            except Exception as exc:
                op_result["remove_status"] = "failed"
                op_result["remove_error"] = str(exc)
                errors.append(op_result)
                continue  # Skip add if remove failed

            # Step 2: Add replacement entry
            try:
                from datetime import date as date_type

                log_date = date_type.fromisoformat(op["date"])
                new_entry = client.add_serving(
                    food_id=op["new_food_id"],
                    food_source_id=op["new_food_source_id"],
                    measure_id=op["new_measure_id"],
                    quantity=op["new_quantity"],
                    weight_grams=op["new_weight_grams"],
                    day=log_date,
                    diary_group=op["old_diary_group_int"],
                )
                op_result["add_status"] = "success"
                op_result["new_serving_id"] = new_entry.get("serving_id", "")
                op_result["new_food_id"] = op["new_food_id"]
                op_result["new_food_source_id"] = op["new_food_source_id"]
                op_result["new_measure_id"] = op["new_measure_id"]
                op_result["new_quantity"] = op["new_quantity"]
                op_result["new_weight_grams"] = op["new_weight_grams"]
            except Exception as exc:
                op_result["add_status"] = "failed"
                op_result["add_error"] = str(exc)
                errors.append(op_result)
                continue

            results.append(op_result)

        # Save audit journal
        journal_id = datetime.now().strftime("%Y%m%d_%H%M%S_") + uuid.uuid4().hex[:8]
        journal = {
            "journal_id": journal_id,
            "idempotency_key": idempotency_key,
            "timestamp": datetime.now().isoformat(),
            "status": "success" if not errors else "partial",
            "date_range": {
                "start": start.isoformat(),
                "end": end.isoformat(),
            },
            "operations_completed": len(results),
            "operations_failed": len(errors),
            "before": before_snapshot,
            "after": results,
            "errors": errors if errors else [],
            "replacements_spec": specs,
        }
        try:
            journal_path = _save_audit_journal(journal)
        except Exception as exc:
            logger.warning("Failed to save audit journal: %s", exc)
            journal_path = None

        return json.dumps(
            {
                "status": "success" if not errors else "partial",
                "dry_run": False,
                "message": (
                    f"Replaced {len(results)} of {len(plan)} entries."
                    + (f" {len(errors)} errors." if errors else "")
                ),
                "journal_id": journal_id,
                "journal_path": str(journal_path) if journal_path else None,
                "operations_completed": len(results),
                "operations_failed": len(errors),
                "results": results,
                "errors": errors if errors else [],
            },
            indent=2,
        )
    except Exception as e:
        return json.dumps({"status": "error", "message": f"{type(e).__name__}: {e}"})


@mcp.tool()
def list_replacement_history(
    limit: int = 20,
) -> str:
    """List recent food replacement operations from the audit journal.

    Shows metadata for past replace_food_entries executions, including
    journal_id (needed for rollback), status, timestamp, and operation counts.

    Args:
        limit: Maximum number of entries to return (default 20).
    """
    try:
        journals = _list_audit_journals(limit=limit)
        return json.dumps(
            {
                "status": "success",
                "count": len(journals),
                "journals": journals,
            },
            indent=2,
        )
    except Exception as e:
        return json.dumps({"status": "error", "message": f"{type(e).__name__}: {e}"})


@mcp.tool()
def rollback_replacement(
    journal_id: str,
    dry_run: bool = True,
) -> str:
    """Rollback a previous replace_food_entries operation.

    Reads the audit journal for the given journal_id and reverses the
    operations: removes the new entries that were added and re-adds the
    original entries that were deleted.

    Operates in dry-run mode by default — call with dry_run=false to execute.

    Args:
        journal_id: The journal_id from replace_food_entries or
                    list_replacement_history.
        dry_run: If true (default), show what would be rolled back without
                 executing. Set to false to actually perform the rollback.
    """
    try:
        journal = _load_audit_journal(journal_id)
        if journal is None:
            return json.dumps(
                {
                    "status": "error",
                    "message": f"Audit journal '{journal_id}' not found.",
                }
            )

        if journal.get("status") == "rolled_back":
            return json.dumps(
                {
                    "status": "error",
                    "message": (
                        f"Journal '{journal_id}' was already rolled back "
                        f"at {journal.get('rolled_back_at', 'unknown')}."
                    ),
                }
            )

        # Build rollback plan from the 'after' (successful operations)
        after_ops = journal.get("after", [])
        before_ops = journal.get("before", [])

        if not after_ops:
            return json.dumps(
                {
                    "status": "error",
                    "message": "No successful operations to roll back.",
                }
            )

        # Build a lookup from old_serving_id → before entry for re-adding
        before_map: dict[str, dict] = {}
        for entry in before_ops:
            before_map[entry["serving_id"]] = entry

        rollback_plan: list[dict] = []
        for op in after_ops:
            if op.get("add_status") != "success":
                continue
            old_sid = op.get("old_serving_id", "")
            before_entry = before_map.get(old_sid, {})
            rollback_plan.append(
                {
                    "date": op["date"],
                    "new_serving_id_to_remove": op.get("new_serving_id", ""),
                    "original_serving_id": old_sid,
                    "original_food_source_id": before_entry.get("food_source_id", 0),
                    "original_quantity": before_entry.get("quantity", 0),
                    "original_diary_group": before_entry.get("diary_group", "Unknown"),
                    "original_diary_group_int": before_entry.get("diary_group_int", 1),
                }
            )

        if not rollback_plan:
            return json.dumps(
                {
                    "status": "error",
                    "message": "No operations eligible for rollback.",
                }
            )

        if dry_run:
            return json.dumps(
                {
                    "status": "success",
                    "dry_run": True,
                    "journal_id": journal_id,
                    "message": (
                        f"Would roll back {len(rollback_plan)} operations. "
                        f"Set dry_run=false to execute."
                    ),
                    "operations_planned": len(rollback_plan),
                    "plan": [
                        {
                            "date": r["date"],
                            "remove_new_serving_id": r["new_serving_id_to_remove"],
                            "restore_food_source_id": r["original_food_source_id"],
                            "restore_quantity": r["original_quantity"],
                            "restore_diary_group": r["original_diary_group"],
                        }
                        for r in rollback_plan
                    ],
                },
                indent=2,
            )

        # Execute rollback
        client = _get_client()
        results: list[dict] = []
        errors: list[dict] = []

        for r in rollback_plan:
            rollback_result = {
                "date": r["date"],
                "new_serving_id": r["new_serving_id_to_remove"],
            }

            # Step 1: Remove the replacement entry
            try:
                client.remove_serving(r["new_serving_id_to_remove"])
                rollback_result["remove_status"] = "success"
            except Exception as exc:
                rollback_result["remove_status"] = "failed"
                rollback_result["remove_error"] = str(exc)
                errors.append(rollback_result)
                continue

            # Step 2: Re-add the original entry
            before_entry = before_map.get(r["original_serving_id"], {})
            original_spec = None
            for spec in journal.get("replacements_spec", []):
                if int(spec["match_food_source_id"]) == before_entry.get(
                    "food_source_id", -1
                ):
                    original_spec = spec
                    break

            if not original_spec:
                # Can't restore — we don't know the original food_id
                rollback_result["restore_status"] = "skipped"
                rollback_result["restore_note"] = (
                    "Original food details not available in journal. "
                    "Entry was removed but not restored."
                )
                results.append(rollback_result)
                continue

            try:
                from datetime import date as date_type

                log_date = date_type.fromisoformat(r["date"])
                restored = client.add_serving(
                    food_id=before_entry.get("food_source_id", 0),
                    food_source_id=before_entry.get("food_source_id", 0),
                    measure_id=0,
                    quantity=before_entry.get("quantity", 0),
                    weight_grams=before_entry.get("quantity", 0),
                    day=log_date,
                    diary_group=before_entry.get("diary_group_int", 1),
                )
                rollback_result["restore_status"] = "success"
                rollback_result["restored_serving_id"] = restored.get("serving_id", "")
            except Exception as exc:
                rollback_result["restore_status"] = "failed"
                rollback_result["restore_error"] = str(exc)
                errors.append(rollback_result)
                continue

            results.append(rollback_result)

        # Mark journal as rolled back
        journal["status"] = "rolled_back"
        journal["rolled_back_at"] = datetime.now().isoformat()
        journal["rollback_results"] = results
        journal["rollback_errors"] = errors
        try:
            _save_audit_journal(journal)
        except Exception as exc:
            logger.warning("Failed to update audit journal: %s", exc)

        return json.dumps(
            {
                "status": "success" if not errors else "partial",
                "dry_run": False,
                "journal_id": journal_id,
                "message": (
                    f"Rolled back {len(results)} of "
                    f"{len(rollback_plan)} operations."
                    + (f" {len(errors)} errors." if errors else "")
                ),
                "operations_completed": len(results),
                "operations_failed": len(errors),
                "results": results,
                "errors": errors if errors else [],
            },
            indent=2,
        )
    except Exception as e:
        return json.dumps({"status": "error", "message": f"{type(e).__name__}: {e}"})


@mcp.tool()
def get_macro_targets(
    target_date: str | None = None,
) -> str:
    """Get current daily macro targets from Cronometer.

    Returns the effective macro targets (protein, fat, carbs, calories)
    and the template name for a specific date or all days of the week.

    Args:
        target_date: Date as YYYY-MM-DD to get targets for (defaults to today).
                     Pass "all" to get the full weekly schedule.
    """
    try:
        client = _get_client()

        if target_date == "all":
            schedules = client.get_all_macro_schedules()
            return json.dumps(
                {
                    "status": "success",
                    "type": "weekly_schedule",
                    "schedules": schedules,
                },
                indent=2,
            )

        day = _parse_date(target_date)
        targets = client.get_daily_macro_targets(day)
        return json.dumps(
            {
                "status": "success",
                "type": "daily_targets",
                "date": target_date or str(date.today()),
                "targets": targets,
            },
            indent=2,
        )
    except Exception as e:
        return json.dumps({"status": "error", "message": f"{type(e).__name__}: {e}"})


@mcp.tool()
def set_macro_targets(
    protein_grams: float | None = None,
    fat_grams: float | None = None,
    carbs_grams: float | None = None,
    calories: float | None = None,
    target_date: str | None = None,
    template_name: str | None = None,
) -> str:
    """Update daily macro targets in Cronometer.

    Reads current targets first, then updates only the provided values.
    Omitted values remain unchanged.

    Args:
        protein_grams: Protein target in grams.
        fat_grams: Fat target in grams.
        carbs_grams: Net carbs target in grams.
        calories: Calorie target in kcal.
        target_date: Date as YYYY-MM-DD (defaults to today).
        template_name: Template name (defaults to "Custom Targets").
    """
    try:
        from datetime import date as date_type

        client = _get_client()
        day = date_type.fromisoformat(target_date) if target_date else date.today()

        # Read current targets to preserve unchanged values
        current = client.get_daily_macro_targets(day)

        new_protein = (
            protein_grams if protein_grams is not None else current["protein_g"]
        )
        new_fat = fat_grams if fat_grams is not None else current["fat_g"]
        new_carbs = carbs_grams if carbs_grams is not None else current["carbs_g"]
        new_calories = calories if calories is not None else current["calories"]
        name = template_name or "Custom Targets"

        client.update_daily_targets(
            day=day,
            protein_g=new_protein,
            fat_g=new_fat,
            carbs_g=new_carbs,
            calories=new_calories,
            template_name=name,
        )

        # Read back to confirm
        updated = client.get_daily_macro_targets(day)
        return json.dumps(
            {
                "status": "success",
                "date": str(day),
                "previous": current,
                "updated": updated,
            },
            indent=2,
        )
    except Exception as e:
        return json.dumps({"status": "error", "message": f"{type(e).__name__}: {e}"})


_DOW_NAMES = [
    "Sunday",
    "Monday",
    "Tuesday",
    "Wednesday",
    "Thursday",
    "Friday",
    "Saturday",
]


@mcp.tool()
def set_weekly_macro_schedule(
    template_name: str,
    days: str = "all",
) -> str:
    """Set the recurring weekly macro schedule by assigning a template to days.

    This updates the DEFAULT schedule that applies to all future dates,
    not just a specific date override.

    First finds the template by name (from existing saved templates or
    from a recently created per-date template), then assigns it to the
    specified days of the week.

    Args:
        template_name: Name of a saved macro target template
                       (e.g. "Retatrutide GI-Optimized", "Keto Rigorous").
        days: Comma-separated day names or "all" (default).
              E.g. "Monday,Wednesday,Friday" or "all".
    """
    try:
        client = _get_client()

        # Get available templates
        templates = client.get_macro_target_templates()
        template_map = {t["template_name"]: t for t in templates}

        if template_name not in template_map:
            return json.dumps(
                {
                    "status": "error",
                    "message": f"Template '{template_name}' not found.",
                    "available_templates": [t["template_name"] for t in templates],
                },
                indent=2,
            )

        template_id = template_map[template_name]["template_id"]

        # Parse which days to update
        if days.strip().lower() == "all":
            target_days = list(range(7))  # 0=Sun through 6=Sat (US ordering)
        else:
            day_name_map = {name.lower(): i for i, name in enumerate(_DOW_NAMES)}
            target_days = []
            for d in days.split(","):
                d = d.strip().lower()
                if d in day_name_map:
                    target_days.append(day_name_map[d])
                else:
                    return json.dumps(
                        {
                            "status": "error",
                            "message": f"Invalid day name: '{d}'",
                            "valid_days": _DOW_NAMES,
                        },
                        indent=2,
                    )

        # Apply template to each day
        results = []
        for dow in target_days:
            client.save_macro_schedule(dow, template_id)
            results.append(
                {
                    "day": _DOW_NAMES[dow],
                    "template_name": template_name,
                    "template_id": template_id,
                }
            )

        # Read back the full schedule to confirm
        updated_schedule = client.get_all_macro_schedules()

        return json.dumps(
            {
                "status": "success",
                "days_updated": results,
                "current_schedule": updated_schedule,
            },
            indent=2,
        )
    except Exception as e:
        return json.dumps({"status": "error", "message": f"{type(e).__name__}: {e}"})


@mcp.tool()
def list_macro_templates() -> str:
    """List all saved macro target templates in Cronometer.

    Returns template names, IDs, and their macro values.
    Use this to find the template_name for set_weekly_macro_schedule.
    """
    try:
        client = _get_client()
        templates = client.get_macro_target_templates()
        return json.dumps(
            {
                "status": "success",
                "count": len(templates),
                "templates": templates,
            },
            indent=2,
        )
    except Exception as e:
        return json.dumps({"status": "error", "message": f"{type(e).__name__}: {e}"})


@mcp.tool()
def create_macro_template(
    template_name: str,
    protein_grams: float,
    fat_grams: float,
    carbs_grams: float,
    calories: float,
    assign_to_all_days: bool = False,
) -> str:
    """Create a new saved macro target template in Cronometer.

    Optionally assigns it to all days of the week as the recurring default.

    Args:
        template_name: Name for the new template (e.g. "Retatrutide GI-Optimized").
        protein_grams: Protein target in grams.
        fat_grams: Fat target in grams.
        carbs_grams: Net carbs target in grams.
        calories: Calorie target in kcal.
        assign_to_all_days: If True, also set this as the recurring weekly
                            schedule for all 7 days (default False).
    """
    try:
        client = _get_client()

        # Check if template already exists
        existing = client.get_macro_target_templates()
        for t in existing:
            if t["template_name"] == template_name:
                return json.dumps(
                    {
                        "status": "error",
                        "message": (
                            f"Template '{template_name}' already exists "
                            f"(id={t['template_id']}). Use set_weekly_macro_schedule "
                            "to assign it to days."
                        ),
                        "existing_template": t,
                    },
                    indent=2,
                )

        # Create the template
        template_id = client.save_macro_target_template(
            template_name=template_name,
            protein_g=protein_grams,
            fat_g=fat_grams,
            carbs_g=carbs_grams,
            calories=calories,
        )

        result = {
            "status": "success",
            "template_name": template_name,
            "template_id": template_id,
            "macros": {
                "protein_g": protein_grams,
                "fat_g": fat_grams,
                "carbs_g": carbs_grams,
                "calories": calories,
            },
        }

        # Optionally assign to all days
        if assign_to_all_days and template_id:
            for dow in range(7):
                client.save_macro_schedule(dow, template_id)
            schedule = client.get_all_macro_schedules()
            result["weekly_schedule_updated"] = True
            result["current_schedule"] = schedule

        return json.dumps(result, indent=2)
    except Exception as e:
        return json.dumps({"status": "error", "message": f"{type(e).__name__}: {e}"})


@mcp.tool()
def get_fasting_history(
    start_date: str | None = None,
    end_date: str | None = None,
) -> str:
    """Get fasting history from Cronometer.

    Returns all fasts (or fasts within a date range) with their status,
    names, recurrence rules, and timestamps.

    Args:
        start_date: Start date as YYYY-MM-DD (omit for all history).
        end_date: End date as YYYY-MM-DD (omit for all history).
    """
    try:
        client = _get_client()

        if start_date and end_date:
            start = _parse_date(start_date)
            end = _parse_date(end_date)
            fasts = client.get_user_fasts_for_range(start, end)
        else:
            fasts = client.get_user_fasts()

        active = [f for f in fasts if f.get("is_active")]
        completed = [f for f in fasts if not f.get("is_active")]

        return json.dumps(
            {
                "status": "success",
                "total_fasts": len(fasts),
                "active_fasts": len(active),
                "completed_fasts": len(completed),
                "fasts": fasts,
            },
            indent=2,
        )
    except Exception as e:
        return json.dumps({"status": "error", "message": f"{type(e).__name__}: {e}"})


@mcp.tool()
def get_fasting_stats() -> str:
    """Get aggregate fasting statistics from Cronometer.

    Returns total fasting hours, longest fast, 7-fast average,
    and completed fast count.
    """
    try:
        client = _get_client()
        stats = client.get_fasting_stats()
        return json.dumps(
            {
                "status": "success",
                "stats": stats,
            },
            indent=2,
        )
    except Exception as e:
        return json.dumps({"status": "error", "message": f"{type(e).__name__}: {e}"})


@mcp.tool()
def delete_fast(fast_id: int) -> str:
    """Delete a fast entry from Cronometer.

    Use get_fasting_history first to find the fast_id.

    Args:
        fast_id: The fast ID to delete.
    """
    try:
        client = _get_client()
        client.delete_fast(fast_id)
        return json.dumps(
            {
                "status": "success",
                "fast_id": fast_id,
                "message": "Fast deleted.",
            },
            indent=2,
        )
    except Exception as e:
        return json.dumps({"status": "error", "message": f"{type(e).__name__}: {e}"})


@mcp.tool()
def cancel_active_fast(fast_id: int) -> str:
    """Cancel an active (in-progress) fast while preserving the recurring schedule.

    Use get_fasting_history to find active fasts (is_active=true).

    Args:
        fast_id: The fast ID of the active fast to cancel.
    """
    try:
        client = _get_client()
        client.cancel_fast_keep_series(fast_id)
        return json.dumps(
            {
                "status": "success",
                "fast_id": fast_id,
                "message": "Active fast cancelled. Recurring schedule preserved.",
            },
            indent=2,
        )
    except Exception as e:
        return json.dumps({"status": "error", "message": f"{type(e).__name__}: {e}"})


@mcp.tool()
def get_recent_biometrics() -> str:
    """Get the most recently logged biometric entries from Cronometer.

    Returns recent values for weight, blood glucose, blood pressure,
    heart rate, body fat, and other tracked biometrics.
    """
    try:
        client = _get_client()
        biometrics = client.get_recent_biometrics()
        return json.dumps(
            {
                "status": "success",
                "count": len(biometrics),
                "biometrics": biometrics,
            },
            indent=2,
        )
    except Exception as e:
        return json.dumps({"status": "error", "message": f"{type(e).__name__}: {e}"})


@mcp.tool()
def add_biometric(
    metric_type: str,
    value: float,
    entry_date: str,
) -> str:
    """Add a biometric entry to Cronometer.

    Supported metric types: weight (lbs), blood_glucose (mg/dL),
    heart_rate (bpm), body_fat (%).

    Args:
        metric_type: One of 'weight', 'blood_glucose', 'heart_rate', 'body_fat'.
        value: The value in display units (lbs, mg/dL, bpm, %).
        entry_date: Date as YYYY-MM-DD.
    """
    try:
        from datetime import date as date_type

        client = _get_client()
        day = date_type.fromisoformat(entry_date)
        biometric_id = client.add_biometric(
            metric_type=metric_type,
            value=value,
            day=day,
        )
        return json.dumps(
            {
                "status": "success",
                "metric_type": metric_type,
                "value": value,
                "date": entry_date,
                "biometric_id": biometric_id,
                "note": "Use biometric_id with remove_biometric to delete this entry.",
            },
            indent=2,
        )
    except Exception as e:
        return json.dumps({"status": "error", "message": f"{type(e).__name__}: {e}"})


@mcp.tool()
def remove_biometric(biometric_id: str) -> str:
    """Remove a biometric entry from Cronometer.

    Use get_recent_biometrics to find biometric_id values.

    Args:
        biometric_id: The biometric entry ID (e.g. "BXW0DA").
    """
    try:
        client = _get_client()
        client.remove_biometric(biometric_id)
        return json.dumps(
            {
                "status": "success",
                "biometric_id": biometric_id,
                "message": "Biometric entry removed.",
            },
            indent=2,
        )
    except Exception as e:
        return json.dumps({"status": "error", "message": f"{type(e).__name__}: {e}"})


def _get_data_dir() -> Path:
    """Get the data directory for sync output.

    Uses CRONOMETER_DATA_DIR env var if set, otherwise defaults to
    ~/.local/share/cronometer-mcp/.
    """
    env_dir = os.environ.get("CRONOMETER_DATA_DIR")
    if env_dir:
        return Path(env_dir)
    return Path.home() / ".local" / "share" / "cronometer-mcp"


def _get_audit_dir() -> Path:
    """Get the audit journal directory, creating it if needed."""
    audit_dir = _get_data_dir() / "audit"
    audit_dir.mkdir(parents=True, exist_ok=True)
    return audit_dir


def _save_audit_journal(journal: dict) -> Path:
    """Save an audit journal to disk and return the file path."""
    audit_dir = _get_audit_dir()
    journal_id = journal["journal_id"]
    path = audit_dir / f"{journal_id}.json"
    path.write_text(json.dumps(journal, indent=2))
    logger.info("Saved audit journal: %s", path)
    return path


def _load_audit_journal(journal_id: str) -> dict | None:
    """Load an audit journal by ID. Returns None if not found."""
    path = _get_audit_dir() / f"{journal_id}.json"
    if not path.exists():
        return None
    return json.loads(path.read_text())


def _find_journal_by_idempotency_key(key: str) -> dict | None:
    """Find an existing journal with the given idempotency_key."""
    audit_dir = _get_audit_dir()
    for path in audit_dir.glob("*.json"):
        try:
            journal = json.loads(path.read_text())
            if journal.get("idempotency_key") == key:
                return journal
        except (json.JSONDecodeError, OSError):
            continue
    return None


def _list_audit_journals(limit: int = 20) -> list[dict]:
    """List recent audit journals (metadata only), newest first."""
    audit_dir = _get_audit_dir()
    journals = []
    for path in sorted(audit_dir.glob("*.json"), reverse=True):
        try:
            journal = json.loads(path.read_text())
            journals.append(
                {
                    "journal_id": journal.get("journal_id", path.stem),
                    "idempotency_key": journal.get("idempotency_key"),
                    "timestamp": journal.get("timestamp"),
                    "status": journal.get("status"),
                    "operations_completed": journal.get("operations_completed", 0),
                    "operations_failed": journal.get("operations_failed", 0),
                    "date_range": journal.get("date_range"),
                }
            )
            if len(journals) >= limit:
                break
        except (json.JSONDecodeError, OSError):
            continue
    return journals


@mcp.tool()
def sync_cronometer(
    start_date: str | None = None,
    end_date: str | None = None,
    days: int = 14,
    diet_label: str | None = None,
) -> str:
    """Download Cronometer data and save locally as JSON + food-log.md.

    Downloads servings and daily summary data, saves JSON exports,
    and regenerates food-log.md.

    Output directory defaults to ~/.local/share/cronometer-mcp/ but can
    be overridden with the CRONOMETER_DATA_DIR environment variable.

    Args:
        start_date: Start date as YYYY-MM-DD (defaults to `days` ago).
        end_date: End date as YYYY-MM-DD (defaults to today).
        days: Number of days to look back if start_date not specified (default 14).
        diet_label: Optional diet label for the markdown header (e.g., "Keto Rigorous").
    """
    try:
        client = _get_client()

        end = _parse_date(end_date) or date.today()
        start = _parse_date(start_date) or (end - timedelta(days=days))

        # Download both exports
        servings = client.get_food_log(start, end)
        daily_summary = client.get_daily_summary(start, end)

        # Save to data directory
        data_dir = _get_data_dir()
        exports_dir = data_dir / "exports"
        exports_dir.mkdir(parents=True, exist_ok=True)

        servings_path = exports_dir / f"servings_{start}_{end}.json"
        servings_path.write_text(json.dumps(servings, indent=2))

        summary_path = exports_dir / f"daily_summary_{start}_{end}.json"
        summary_path.write_text(json.dumps(daily_summary, indent=2))

        # Also save a "latest" copy for easy access
        latest_servings = exports_dir / "servings_latest.json"
        latest_servings.write_text(json.dumps(servings, indent=2))

        latest_summary = exports_dir / "daily_summary_latest.json"
        latest_summary.write_text(json.dumps(daily_summary, indent=2))

        # Generate food-log.md
        food_log_path = data_dir / "food-log.md"
        md_content = generate_food_log_md(
            servings,
            daily_summary,
            start,
            end,
            diet_label=diet_label,
        )
        food_log_path.write_text(md_content)

        return json.dumps(
            {
                "status": "success",
                "date_range": {"start": str(start), "end": str(end)},
                "servings_count": len(servings),
                "days_count": len(daily_summary),
                "files_saved": [
                    str(servings_path),
                    str(summary_path),
                    str(latest_servings),
                    str(latest_summary),
                    str(food_log_path),
                ],
            },
            indent=2,
        )
    except Exception as e:
        return json.dumps({"status": "error", "message": f"{type(e).__name__}: {e}"})


@mcp.tool()
def copy_day(source_date: str, destination_date: str) -> str:
    """Copy all diary entries from one date to another.

    Server-side operation that copies ALL entries (food, exercise,
    notes, biometrics) from source to destination. Additive — does
    not remove existing entries on the destination date.

    Args:
        source_date: Date to copy FROM as YYYY-MM-DD.
        destination_date: Date to copy TO as YYYY-MM-DD.
    """
    try:
        from datetime import date as date_type

        src = date_type.fromisoformat(source_date)
        dst = date_type.fromisoformat(destination_date)
        client = _get_client()
        client.copy_day(src, dst)
        return json.dumps(
            {
                "status": "success",
                "message": f"Copied all entries from {source_date} to {destination_date}.",
                "source_date": source_date,
                "destination_date": destination_date,
            },
            indent=2,
        )
    except Exception as e:
        return json.dumps({"status": "error", "message": f"{type(e).__name__}: {e}"})


@mcp.tool()
def set_day_complete(date: str, complete: bool = True) -> str:
    """Mark a diary day as complete or incomplete.

    Args:
        date: Date to mark as YYYY-MM-DD.
        complete: True to mark complete, False to mark incomplete.
    """
    try:
        from datetime import date as date_type

        day = date_type.fromisoformat(date)
        client = _get_client()
        client.set_day_complete(day, complete)
        status = "complete" if complete else "incomplete"
        return json.dumps(
            {
                "status": "success",
                "message": f"Marked {date} as {status}.",
                "date": date,
                "complete": complete,
            },
            indent=2,
        )
    except Exception as e:
        return json.dumps({"status": "error", "message": f"{type(e).__name__}: {e}"})


@mcp.tool()
def get_repeated_items() -> str:
    """List all recurring food entries.

    Returns all repeat items configured in Cronometer, including
    their food name, quantity, measure, diary group, and which
    days of the week they repeat on.
    """
    try:
        client = _get_client()
        items = client.get_repeated_items()
        return json.dumps(
            {
                "status": "success",
                "count": len(items),
                "items": items,
            },
            indent=2,
        )
    except Exception as e:
        return json.dumps({"status": "error", "message": f"{type(e).__name__}: {e}"})


@mcp.tool()
def add_repeat_item(
    food_id: int,
    food_source_id: int,
    quantity: float,
    food_name: str,
    diary_group: str = "Breakfast",
    days_of_week: str = "all",
) -> str:
    """Add a recurring food entry that auto-logs on selected days.

    Quantity is in default servings for the food (e.g., for coffee where
    the default serving is 1 cup, quantity=12 means 12 cups).

    Use search_foods to find food_id and food_source_id.

    Args:
        food_id: Numeric food ID from search_foods results.
        food_source_id: Food source ID from search_foods results.
        quantity: Number of default servings.
        food_name: Display name for the food.
        diary_group: Meal slot — "Breakfast", "Lunch", "Dinner", or "Snacks".
        days_of_week: Comma-separated day numbers (0=Sun, 1=Mon, ..., 6=Sat),
                      or "all" for every day (default), or "weekdays", or "weekends".
    """
    try:
        group_key = diary_group.strip().lower()
        group_int = _DIARY_GROUP_MAP.get(group_key)
        if group_int is None:
            return json.dumps(
                {
                    "status": "error",
                    "message": (
                        f"Invalid diary_group '{diary_group}'. "
                        "Must be one of: Breakfast, Lunch, Dinner, Snacks."
                    ),
                }
            )

        # Parse days_of_week
        days_str = days_of_week.strip().lower()
        if days_str == "all":
            days = [0, 1, 2, 3, 4, 5, 6]
        elif days_str == "weekdays":
            days = [1, 2, 3, 4, 5]
        elif days_str == "weekends":
            days = [0, 6]
        else:
            days = [int(d.strip()) for d in days_of_week.split(",")]

        client = _get_client()
        client.add_repeat_item(
            food_source_id=food_source_id,
            food_id=food_id,
            quantity=quantity,
            food_name=food_name,
            diary_group=group_int,
            days_of_week=days,
        )
        day_names = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"]
        day_labels = [day_names[d] for d in days]
        return json.dumps(
            {
                "status": "success",
                "message": f"Added '{food_name}' as recurring entry.",
                "food_name": food_name,
                "diary_group": diary_group,
                "days": day_labels,
                "quantity": quantity,
            },
            indent=2,
        )
    except Exception as e:
        return json.dumps({"status": "error", "message": f"{type(e).__name__}: {e}"})


@mcp.tool()
def delete_repeat_item(repeat_item_id: int) -> str:
    """Delete a recurring food entry.

    Use get_repeated_items to find the repeat_item_id.

    Args:
        repeat_item_id: The ID of the repeat item to delete.
    """
    try:
        client = _get_client()
        client.delete_repeat_item(repeat_item_id)
        return json.dumps(
            {
                "status": "success",
                "message": f"Deleted repeat item {repeat_item_id}.",
                "repeat_item_id": repeat_item_id,
            },
            indent=2,
        )
    except Exception as e:
        return json.dumps({"status": "error", "message": f"{type(e).__name__}: {e}"})


def main():
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
