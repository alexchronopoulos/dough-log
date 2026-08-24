from __future__ import annotations

import pytest

from doughlog.blend import BlendError, solve_flour_blend
from doughlog.calculator import DEFAULT_FORMULA, FormulaError, calculate_formula


def test_requested_defaults_and_gram_pound_units():
    assert DEFAULT_FORMULA["ball_weight_g"] == 700
    assert DEFAULT_FORMULA["yeast_pct"] == pytest.approx(0.07)

    result = calculate_formula(
        {
            **DEFAULT_FORMULA,
            "residue_pct": 0,
            "flours": [
                {"name": "Bread Flour", "pct": 100, "protein_pct": 12.7, "ash_pct": 0.55}
            ],
        }
    )
    assert result["target_weight_g"] == pytest.approx(14_000)
    assert sum(item["weight_g"] for item in result["ingredients"]) == pytest.approx(14_000)
    assert all(set(item["units"]) == {"g", "lb"} for item in result["ingredients"])


def test_reference_recipe_uses_preferment_weight_as_percent_of_total_flour():
    formula = {
        "ball_count": 180,
        "ball_weight_g": 708.7375035792078,
        "hydration_pct": 70,
        "salt_pct": 3,
        "yeast_type": "IDY",
        "yeast_pct": 0.07,
        "residue_pct": 1,
        "flours": [
            {"name": "00 Normal", "pct": 50, "protein_pct": 12.5, "ash_pct": 0.55},
            {"name": "00 Reinforced", "pct": 50, "protein_pct": 13.0, "ash_pct": 0.60},
        ],
        "ingredients": [{"name": "Canola Oil", "pct": 3.4}],
        "preferments": [
            {
                "name": "Poolish",
                "type": "Poolish",
                "amount_pct": 55,
                "water_pct": 50,
                "leavening_type": "IDY",
                "leavening_pct": 0.01,
                "flours": [
                    {"name": "High Mountain", "pct": 47.5, "protein_pct": 13.5, "ash_pct": 0.65},
                    {"name": "Glacier", "pct": 47.5, "protein_pct": 12.8, "ash_pct": 0.58},
                    {"name": "Einkorn", "pct": 5, "protein_pct": 14.0, "ash_pct": 1.80},
                ],
            }
        ],
    }
    result = calculate_formula(formula)
    poolish = result["preferments"][0]

    assert result["total_flour_g"] == pytest.approx(73_014.381, abs=0.002)
    assert poolish["total_g"] == pytest.approx(40_157.910, abs=0.002)
    assert poolish["water_g"] == pytest.approx(20_078.955, abs=0.002)
    assert poolish["leavening_g"] == pytest.approx(4.016, abs=0.002)
    assert poolish["flour_g"] == pytest.approx(20_074.939, abs=0.002)
    assert result["preferment_total_pct"] == pytest.approx(55)
    assert result["prefermented_flour_pct"] == pytest.approx(27.4945)

    final_mix = {item["name"]: item["weight_g"] for item in result["final_mix"]}
    assert final_mix["Water"] == pytest.approx(31_031.112, abs=0.002)
    assert final_mix["IDY"] == pytest.approx(47.094, abs=0.002)
    assert result["overall_protein_pct"] == pytest.approx(12.8716631625)
    assert result["overall_ash_pct"] == pytest.approx(0.60228829125)


def test_multiple_preferments_are_deducted_from_complete_formula():
    formula = {
        **DEFAULT_FORMULA,
        "ball_count": 10,
        "residue_pct": 0,
        "flours": [
            {"name": "Final Flour", "pct": 100, "protein_pct": 12.5, "ash_pct": 0.55}
        ],
        "preferments": [
            {
                "name": "Poolish",
                "type": "Poolish",
                "amount_pct": 30,
                "water_pct": 50,
                "leavening_type": "IDY",
                "leavening_pct": 0.01,
                "flours": [
                    {"name": "Bread Flour", "pct": 70, "protein_pct": 12.8, "ash_pct": 0.57},
                    {"name": "Whole Wheat", "pct": 30, "protein_pct": 14.0, "ash_pct": 1.50},
                ],
            },
            {
                "name": "Levain",
                "type": "Levain",
                "amount_pct": 20,
                "water_pct": 40,
                "leavening_type": "Mature Starter",
                "leavening_pct": 5,
                "flours": [
                    {"name": "Rye", "pct": 100, "protein_pct": 11.0, "ash_pct": 1.70}
                ],
            },
        ],
    }
    result = calculate_formula(formula)

    assert len(result["preferments"]) == 2
    assert result["preferment_total_pct"] == pytest.approx(50)
    assert result["prefermented_flour_pct"] == pytest.approx(25.997)
    assert len(result["preferments"][0]["allocated_flours"]) == 2
    total_pref_water = sum(item["water_g"] for item in result["preferments"])
    final_water = next(item["weight_g"] for item in result["final_mix"] if item["name"] == "Water")
    assert total_pref_water + final_water == pytest.approx(result["total_water_g"])
    assert sum(item["weight_g"] for item in result["ingredients"]) == pytest.approx(
        result["scaled_target_g"]
    )


def test_rejects_preferment_components_above_100_percent():
    formula = {
        **DEFAULT_FORMULA,
        "preferments": [
            {
                "name": "Poolish",
                "amount_pct": 55,
                "water_pct": 60,
                "leavening_type": "IDY",
                "leavening_pct": 41,
                "flours": [{"name": "Bread Flour", "pct": 100}],
            }
        ],
    }
    with pytest.raises(FormulaError, match="cannot exceed 100%"):
        calculate_formula(formula)


def test_flour_blend_solver_hits_protein_and_ash_targets():
    flours = [
        {"mill": "North Mill", "name": "Flour A", "protein_pct": 14, "ash_pct": 0.6},
        {"name": "Flour B", "protein_pct": 10, "ash_pct": 1.0},
        {"name": "Flour C", "protein_pct": 12, "ash_pct": 1.4},
        {"name": "Flour D", "protein_pct": 16, "ash_pct": 0.4},
    ]
    result = solve_flour_blend(
        flours,
        target_protein_pct=13,
        target_ash_pct=0.85,
    )

    assert result["is_exact"] is True
    assert result["rows"][0]["mill"] == "North Mill"
    assert [row["blend_pct"] for row in result["rows"]] == pytest.approx([25, 25, 25, 25])
    assert result["achieved_protein_pct"] == pytest.approx(13)
    assert result["achieved_ash_pct"] == pytest.approx(0.85)
    assert sum(row["blend_pct"] for row in result["rows"]) == pytest.approx(100)


def test_flour_blend_solver_favors_near_equal_exact_solution():
    result = solve_flour_blend(
        [
            {"name": "Flour A", "protein_pct": 14, "ash_pct": 0.6},
            {"name": "Flour B", "protein_pct": 10, "ash_pct": 1.0},
            {"name": "Flour C", "protein_pct": 12, "ash_pct": 1.4},
            {"name": "Flour D", "protein_pct": 16, "ash_pct": 0.4},
        ],
        target_protein_pct=12.6,
        target_ash_pct=0.86,
    )

    assert result["is_exact"] is True
    assert result["achieved_protein_pct"] == pytest.approx(12.6)
    assert result["achieved_ash_pct"] == pytest.approx(0.86)
    assert [row["blend_pct"] for row in result["rows"]] == pytest.approx([25, 35, 20, 20])


def test_flour_blend_solver_returns_closest_bounded_blend_when_needed():
    result = solve_flour_blend(
        [
            {"name": "Flour A", "protein_pct": 14, "ash_pct": 0.6},
            {"name": "Flour B", "protein_pct": 10, "ash_pct": 1.0},
            {"name": "Flour C", "protein_pct": 12, "ash_pct": 1.4},
            {"name": "Flour D", "protein_pct": 16, "ash_pct": 0.4},
        ],
        target_protein_pct=20,
        target_ash_pct=3,
    )

    assert result["is_exact"] is False
    assert sum(row["blend_pct"] for row in result["rows"]) == pytest.approx(100)
    for row in result["rows"]:
        assert 1 <= row["blend_pct"] <= 97


def test_flour_blend_solver_respects_custom_minimum_for_every_flour():
    result = solve_flour_blend(
        [
            {"name": "Flour A", "protein_pct": 14, "ash_pct": 0.6},
            {"name": "Flour B", "protein_pct": 10, "ash_pct": 1.0},
            {"name": "Flour C", "protein_pct": 12, "ash_pct": 1.4},
            {"name": "Flour D", "protein_pct": 16, "ash_pct": 0.4},
        ],
        target_protein_pct=20,
        target_ash_pct=3,
        minimum_flour_pct=5,
    )

    assert result["minimum_flour_pct"] == 5
    assert sum(row["blend_pct"] for row in result["rows"]) == pytest.approx(100)
    assert all(row["blend_pct"] >= 5 for row in result["rows"])


def test_flour_blend_solver_returns_only_whole_number_shares():
    result = solve_flour_blend(
        [
            {"name": "Flour A", "protein_pct": 14, "ash_pct": 0.6},
            {"name": "Flour B", "protein_pct": 10, "ash_pct": 1.0},
            {"name": "Flour C", "protein_pct": 12, "ash_pct": 1.4},
            {"name": "Flour D", "protein_pct": 16, "ash_pct": 0.4},
        ],
        target_protein_pct=13.123,
        target_ash_pct=0.877,
    )

    shares = [row["blend_pct"] for row in result["rows"]]
    assert all(isinstance(share, int) for share in shares)
    assert sum(shares) == 100


def test_flour_blend_solver_allows_zero_minimum_for_smaller_blends():
    result = solve_flour_blend(
        [
            {"name": "Flour A", "protein_pct": 14, "ash_pct": 0.6},
            {"name": "Flour B", "protein_pct": 10, "ash_pct": 1.0},
            {"name": "Flour C", "protein_pct": 12, "ash_pct": 1.4},
            {"name": "Flour D", "protein_pct": 16, "ash_pct": 0.4},
        ],
        target_protein_pct=12,
        target_ash_pct=1.4,
        minimum_flour_pct=0,
    )

    shares = [row["blend_pct"] for row in result["rows"]]
    assert result["is_exact"] is True
    assert shares == [0, 0, 100, 0]


def test_flour_blend_solver_rejects_negative_minimum():
    with pytest.raises(BlendError, match="between 0% and 25%"):
        solve_flour_blend(
            [
                {"name": "Flour A", "protein_pct": 14, "ash_pct": 0.6},
                {"name": "Flour B", "protein_pct": 10, "ash_pct": 1.0},
                {"name": "Flour C", "protein_pct": 12, "ash_pct": 1.4},
                {"name": "Flour D", "protein_pct": 16, "ash_pct": 0.4},
            ],
            target_protein_pct=13,
            target_ash_pct=0.85,
            minimum_flour_pct=-1,
        )


def test_flour_blend_solver_rejects_fractional_minimum():
    with pytest.raises(BlendError, match="whole-number"):
        solve_flour_blend(
            [
                {"name": "Flour A", "protein_pct": 14, "ash_pct": 0.6},
                {"name": "Flour B", "protein_pct": 10, "ash_pct": 1.0},
                {"name": "Flour C", "protein_pct": 12, "ash_pct": 1.4},
                {"name": "Flour D", "protein_pct": 16, "ash_pct": 0.4},
            ],
            target_protein_pct=13,
            target_ash_pct=0.85,
            minimum_flour_pct=1.5,
        )


def test_flour_blend_solver_requires_four_flours():
    with pytest.raises(BlendError, match="exactly four"):
        solve_flour_blend(
            [
                {"name": "Flour A", "protein_pct": 14, "ash_pct": 0.8},
                {"name": "Flour B", "protein_pct": 12, "ash_pct": 0.6},
                {"name": "Flour C", "protein_pct": 13, "ash_pct": 0.7},
            ],
            target_protein_pct=12.5,
            target_ash_pct=0.65,
        )
