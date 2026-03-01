"""Generate food-log.md from Cronometer export data."""

from datetime import date


DAY_NAMES = {0: "Mon", 1: "Tue", 2: "Wed", 3: "Thu", 4: "Fri", 5: "Sat", 6: "Sun"}
MONTH_NAMES = {
    1: "Jan", 2: "Feb", 3: "Mar", 4: "Apr", 5: "May", 6: "Jun",
    7: "Jul", 8: "Aug", 9: "Sep", 10: "Oct", 11: "Nov", 12: "Dec",
}
FULL_DAY_NAMES = {
    0: "Monday", 1: "Tuesday", 2: "Wednesday", 3: "Thursday",
    4: "Friday", 5: "Saturday", 6: "Sunday",
}


def _parse_amount(amount_str: str) -> tuple[str, str]:
    """Split '820.00 g' into ('820', 'g').

    Handles multi-word units like 'fl oz', 'fried slice', etc.
    Finds the numeric prefix and treats the rest as the unit.
    """
    import re
    s = amount_str.strip()
    # Match leading number (possibly with decimals), rest is unit
    m = re.match(r"^([\d.]+)\s+(.+)$", s)
    if m:
        num_s, unit = m.group(1), m.group(2)
        try:
            num_f = float(num_s)
            if num_f == int(num_f):
                num_s = str(int(num_f))
            else:
                num_s = f"{num_f:.2f}".rstrip("0").rstrip(".")
            return num_s, unit
        except ValueError:
            pass
    return s, ""


def _fmt_cal(val: str) -> str:
    """Format calorie value with comma separators."""
    try:
        n = float(val)
        if n >= 1000:
            return f"{n:,.0f}"
        return f"{n:.0f}"
    except (ValueError, TypeError):
        return val


def _fmt_g(val: str) -> str:
    """Format gram value as integer."""
    try:
        return f"{float(val):.0f}g"
    except (ValueError, TypeError):
        return val


def _safe_float(val: str, default: float = 0.0) -> float:
    try:
        return float(val)
    except (ValueError, TypeError):
        return default


def generate_food_log_md(
    servings: list[dict],
    daily_summary: list[dict],
    start_date: date,
    end_date: date,
    diet_label: str | None = None,
) -> str:
    """Generate food-log.md content from Cronometer export data.

    Args:
        servings: Parsed servings CSV rows.
        daily_summary: Parsed daily summary CSV rows.
        start_date: Start of date range.
        end_date: End of date range.
        diet_label: Optional diet description (e.g., "Keto Rigorous").
            If provided, appears in the header.

    Returns:
        Markdown string for the food log.
    """
    lines: list[str] = []

    # Format date range for title
    s_month = MONTH_NAMES[start_date.month]
    e_month = MONTH_NAMES[end_date.month]
    if start_date.month == end_date.month:
        title_range = f"{s_month} {start_date.day}\u2013{end_date.day}, {end_date.year}"
    else:
        title_range = f"{s_month} {start_date.day} \u2013 {e_month} {end_date.day}, {end_date.year}"

    lines.append(f"# Cronometer Food Log \u2014 {title_range}")
    lines.append("")
    header = "Auto-generated from Cronometer API export."
    if diet_label:
        header += f" Diet target: {diet_label}."
    lines.append(header)
    lines.append("")
    lines.append("---")
    lines.append("")

    # === Daily Summary Table ===
    lines.append("## Daily Summary")
    lines.append("")
    lines.append("| Date | Day | Calories | Protein | Carbs | Fat | Fiber |")
    lines.append("|------|-----|----------|---------|-------|-----|-------|")

    total_cals = 0.0
    total_prot = 0.0
    total_carbs = 0.0
    total_fat = 0.0
    day_count = 0

    for row in daily_summary:
        d_str = row.get("Date", "")
        try:
            d = date.fromisoformat(d_str)
        except ValueError:
            continue

        day_name = DAY_NAMES[d.weekday()]
        month_name = MONTH_NAMES[d.month]
        date_label = f"{month_name} {d.day}"

        cals = _safe_float(row.get("Energy (kcal)", "0"))
        prot = _safe_float(row.get("Protein (g)", "0"))
        carbs = _safe_float(row.get("Carbs (g)", "0"))
        fat = _safe_float(row.get("Fat (g)", "0"))
        fiber = _safe_float(row.get("Fiber (g)", "0"))

        total_cals += cals
        total_prot += prot
        total_carbs += carbs
        total_fat += fat
        day_count += 1

        lines.append(
            f"| {date_label} | {day_name} | {_fmt_cal(str(cals))} "
            f"| {_fmt_g(str(prot))} | {_fmt_g(str(carbs))} "
            f"| {_fmt_g(str(fat))} | {_fmt_g(str(fiber))} |"
        )

    # Period averages
    if day_count > 0:
        lines.append("")
        lines.append(f"**{day_count}-day averages:**")
        lines.append(f"- Calories: ~{total_cals / day_count:,.0f}")
        lines.append(f"- Protein: ~{total_prot / day_count:.0f}g")
        lines.append(f"- Carbs: ~{total_carbs / day_count:.0f}g")
        lines.append(f"- Fat: ~{total_fat / day_count:.0f}g")

    lines.append("")
    lines.append("---")

    # === Per-Day Sections ===
    # Group servings by date
    by_date: dict[str, list[dict]] = {}
    for row in servings:
        d = row.get("Day", "")
        by_date.setdefault(d, []).append(row)

    for d_str in sorted(by_date.keys()):
        try:
            d = date.fromisoformat(d_str)
        except ValueError:
            continue

        day_name = FULL_DAY_NAMES[d.weekday()]
        month_name = MONTH_NAMES[d.month]
        lines.append("")
        lines.append(f"## {month_name} {d.day} \u2014 {day_name}")

        # Group by meal (Group column)
        day_entries = by_date[d_str]
        by_meal: dict[str, list[dict]] = {}
        for entry in day_entries:
            meal = entry.get("Group", "Other")
            by_meal.setdefault(meal, []).append(entry)

        for meal, entries in by_meal.items():
            meal_cals = sum(_safe_float(e.get("Energy (kcal)", "0")) for e in entries)
            meal_prot = sum(_safe_float(e.get("Protein (g)", "0")) for e in entries)
            meal_carbs = sum(_safe_float(e.get("Carbs (g)", "0")) for e in entries)
            meal_fat = sum(_safe_float(e.get("Fat (g)", "0")) for e in entries)

            lines.append("")
            lines.append(
                f"### {meal} \u2014 {_fmt_cal(str(meal_cals))} kcal "
                f"| {meal_prot:.0f}g protein | {meal_carbs:.0f}g carbs "
                f"| {meal_fat:.0f}g fat"
            )
            lines.append("| Food | Amount | Unit | Calories |")
            lines.append("|------|--------|------|----------|")

            for entry in entries:
                food = entry.get("Food Name", "")
                amount_raw = entry.get("Amount", "")
                amount, unit = _parse_amount(amount_raw)
                cals = _safe_float(entry.get("Energy (kcal)", "0"))
                lines.append(f"| {food} | {amount} | {unit} | {cals:.2f} |")

        lines.append("")
        lines.append("---")

    # === Frequently Appearing Foods ===
    food_counts: dict[str, int] = {}
    food_amounts: dict[str, list[str]] = {}
    for row in servings:
        name = row.get("Food Name", "")
        if not name:
            continue
        food_counts[name] = food_counts.get(name, 0) + 1
        amount_raw = row.get("Amount", "")
        food_amounts.setdefault(name, []).append(amount_raw)

    # Only show foods appearing 2+ times
    frequent = sorted(
        [(name, count) for name, count in food_counts.items() if count >= 2],
        key=lambda x: -x[1],
    )

    if frequent:
        lines.append("")
        lines.append("## Frequently Appearing Foods")
        lines.append("")
        lines.append("| Food | Occurrences | Typical Amount |")
        lines.append("|------|-------------|----------------|")
        for name, count in frequent:
            amounts = food_amounts[name]
            if len(amounts) == 1:
                typical = amounts[0]
            else:
                typical = f"{amounts[0]} \u2013 {amounts[-1]}"
            lines.append(f"| {name} | {count} | {typical} |")

    lines.append("")
    return "\n".join(lines)
