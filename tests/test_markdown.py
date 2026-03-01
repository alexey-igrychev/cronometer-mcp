"""Tests for the markdown food log generator."""

from datetime import date

from cronometer_mcp.markdown import (
    generate_food_log_md,
    _parse_amount,
    _fmt_cal,
    _fmt_g,
    _safe_float,
)


class TestParseAmount:
    def test_simple_grams(self):
        assert _parse_amount("820.00 g") == ("820", "g")

    def test_fractional(self):
        assert _parse_amount("1.50 cup") == ("1.5", "cup")

    def test_multi_word_unit(self):
        assert _parse_amount("96.00 fl oz") == ("96", "fl oz")

    def test_whole_number(self):
        assert _parse_amount("2.00 large") == ("2", "large")

    def test_no_unit(self):
        assert _parse_amount("something") == ("something", "")

    def test_empty(self):
        assert _parse_amount("") == ("", "")


class TestFormatters:
    def test_fmt_cal_large(self):
        assert _fmt_cal("1500") == "1,500"

    def test_fmt_cal_small(self):
        assert _fmt_cal("500") == "500"

    def test_fmt_cal_invalid(self):
        assert _fmt_cal("n/a") == "n/a"

    def test_fmt_g(self):
        assert _fmt_g("123.45") == "123g"

    def test_fmt_g_invalid(self):
        assert _fmt_g("") == ""

    def test_safe_float(self):
        assert _safe_float("1.5") == 1.5
        assert _safe_float("bad") == 0.0
        assert _safe_float("", 99.0) == 99.0


class TestGenerateFoodLogMd:
    def test_basic_output(self):
        servings = [
            {
                "Day": "2026-01-15",
                "Group": "Breakfast",
                "Food Name": "Eggs",
                "Amount": "3.00 large",
                "Energy (kcal)": "210",
                "Protein (g)": "18",
                "Carbs (g)": "1",
                "Fat (g)": "15",
            },
        ]
        daily_summary = [
            {
                "Date": "2026-01-15",
                "Energy (kcal)": "2100",
                "Protein (g)": "180",
                "Carbs (g)": "30",
                "Fat (g)": "140",
                "Fiber (g)": "20",
            },
        ]
        md = generate_food_log_md(
            servings, daily_summary,
            date(2026, 1, 15), date(2026, 1, 15),
        )

        assert "# Cronometer Food Log" in md
        assert "Jan 15" in md
        assert "## Daily Summary" in md
        assert "2,100" in md  # formatted calories
        assert "## Jan 15" in md
        assert "### Breakfast" in md
        assert "Eggs" in md
        assert "Auto-generated from Cronometer API export." in md
        # No diet label by default
        assert "Diet target:" not in md

    def test_diet_label(self):
        md = generate_food_log_md([], [], date(2026, 1, 1), date(2026, 1, 7),
                                  diet_label="Keto Rigorous")
        assert "Diet target: Keto Rigorous." in md

    def test_multi_day_range(self):
        daily = [
            {"Date": "2026-01-01", "Energy (kcal)": "2000", "Protein (g)": "150",
             "Carbs (g)": "30", "Fat (g)": "130", "Fiber (g)": "18"},
            {"Date": "2026-01-02", "Energy (kcal)": "2200", "Protein (g)": "170",
             "Carbs (g)": "35", "Fat (g)": "140", "Fiber (g)": "22"},
        ]
        md = generate_food_log_md([], daily, date(2026, 1, 1), date(2026, 1, 2))
        assert "2-day averages" in md
        assert "Jan 1\u20132" in md

    def test_cross_month_range(self):
        md = generate_food_log_md([], [], date(2026, 1, 28), date(2026, 2, 3))
        assert "Jan 28 \u2013 Feb 3" in md

    def test_frequent_foods(self):
        servings = [
            {"Day": "2026-01-01", "Group": "Breakfast", "Food Name": "Coffee",
             "Amount": "1.00 cup", "Energy (kcal)": "2"},
            {"Day": "2026-01-02", "Group": "Breakfast", "Food Name": "Coffee",
             "Amount": "1.00 cup", "Energy (kcal)": "2"},
        ]
        md = generate_food_log_md(servings, [], date(2026, 1, 1), date(2026, 1, 2))
        assert "Frequently Appearing Foods" in md
        assert "Coffee" in md
        assert "| 2 |" in md
