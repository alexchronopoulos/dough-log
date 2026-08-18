from __future__ import annotations

import math
from copy import deepcopy
from typing import Any


GRAMS_PER_POUND = 453.59237


class FormulaError(ValueError):
    pass


DEFAULT_FORMULA: dict[str, Any] = {
    "ball_count": 20,
    "ball_weight_g": 700,
    "hydration_pct": 70,
    "salt_pct": 3,
    "yeast_type": "IDY",
    "yeast_pct": 0.07,
    "residue_pct": 1,
    "flours": [{"name": "Final Mix Flour", "pct": 100, "protein_pct": None, "ash_pct": None}],
    "ingredients": [{"name": "Canola Oil", "pct": 3.4}],
    "preferments": [],
}


def _number(value: Any, default: float = 0) -> float:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return default
    return result if math.isfinite(result) else default


def _positive(value: Any, default: float = 0) -> float:
    return max(0.0, _number(value, default))


def _optional_percentage(value: Any) -> float | None:
    if value in (None, ""):
        return None
    return _positive(value)


def _flour(value: dict[str, Any]) -> dict[str, Any]:
    return {
        "name": str(value.get("name", "")).strip(),
        "pct": _positive(value.get("pct")),
        "protein_pct": _optional_percentage(value.get("protein_pct")),
        "ash_pct": _optional_percentage(value.get("ash_pct")),
    }


def _migrate_preferment(item: dict[str, Any]) -> tuple[float, float, float]:
    """Translate proof-of-concept PFF/hydration fields when old logs are opened."""
    if item.get("amount_pct") is not None:
        return (
            _positive(item.get("amount_pct")),
            _positive(item.get("water_pct"), 50),
            _positive(item.get("leavening_pct")),
        )

    old_flour_pct = _positive(item.get("flour_pct"))
    old_hydration = _positive(item.get("hydration_pct"), 100)
    old_leavening_of_flour = _positive(item.get("leavening_pct"))
    amount_pct = old_flour_pct * (1 + old_hydration / 100 + old_leavening_of_flour / 100)
    if amount_pct <= 0:
        return 0, 50, 0
    water_pct = (old_flour_pct * old_hydration / 100) / amount_pct * 100
    leavening_pct = (old_flour_pct * old_leavening_of_flour / 100) / amount_pct * 100
    return amount_pct, water_pct, leavening_pct


def normalize_formula(value: dict[str, Any] | None) -> dict[str, Any]:
    formula = deepcopy(DEFAULT_FORMULA)
    value = value or {}
    for field in (
        "ball_count",
        "ball_weight_g",
        "hydration_pct",
        "salt_pct",
        "yeast_type",
        "yeast_pct",
        "residue_pct",
    ):
        if field in value:
            formula[field] = value[field]
    for collection in ("flours", "ingredients", "preferments"):
        if isinstance(value.get(collection), list):
            formula[collection] = deepcopy(value[collection])

    for field in ("ball_count", "ball_weight_g", "hydration_pct", "salt_pct", "yeast_pct", "residue_pct"):
        formula[field] = _positive(formula.get(field), DEFAULT_FORMULA[field])
    formula["ball_count"] = max(1, int(round(formula["ball_count"])))
    formula["yeast_type"] = str(formula.get("yeast_type", "IDY")).strip() or "IDY"
    if formula["yeast_type"] == "None":
        formula["yeast_pct"] = 0

    flours = []
    for item in formula.get("flours", []):
        if isinstance(item, dict):
            normalized = _flour(item)
            if normalized["name"]:
                flours.append(normalized)
    formula["flours"] = flours or deepcopy(DEFAULT_FORMULA["flours"])

    ingredients = []
    for item in formula.get("ingredients", []):
        if isinstance(item, dict) and str(item.get("name", "")).strip():
            ingredients.append({"name": str(item["name"]).strip(), "pct": _positive(item.get("pct"))})
    formula["ingredients"] = ingredients

    preferments = []
    for item in formula.get("preferments", []):
        if not isinstance(item, dict) or not str(item.get("name", "")).strip():
            continue
        amount_pct, water_pct, leavening_pct = _migrate_preferment(item)
        pref_flours = []
        for flour_item in item.get("flours", []):
            if isinstance(flour_item, dict):
                normalized = _flour(flour_item)
                if normalized["name"]:
                    pref_flours.append(normalized)
        if not pref_flours:
            pref_flours = [deepcopy(formula["flours"][0])]
        leavening_type = (
            str(item.get("leavening_type", formula["yeast_type"])).strip()
            or formula["yeast_type"]
        )
        if leavening_type == "None":
            leavening_pct = 0
        preferments.append(
            {
                "name": str(item["name"]).strip(),
                "type": str(item.get("type", "Preferment")).strip() or "Preferment",
                "amount_pct": amount_pct,
                "water_pct": water_pct,
                "leavening_type": leavening_type,
                "leavening_pct": leavening_pct,
                "flours": pref_flours,
                "notes": str(item.get("notes", "")).strip(),
            }
        )
    formula["preferments"] = preferments
    return formula


def _normalize_blend(
    blend: list[dict[str, Any]], label: str, warnings: list[str]
) -> list[dict[str, Any]]:
    total = sum(_positive(item.get("pct")) for item in blend)
    if total <= 0:
        raise FormulaError(f"{label} percentages must add up to more than zero.")
    if abs(total - 100) > 0.05:
        warnings.append(f"{label} totals {total:.2f}%; it was normalized to 100% for calculation.")
    return [{**item, "pct": _positive(item.get("pct")) * 100 / total} for item in blend]


def units(weight_g: float) -> dict[str, float]:
    return {"g": weight_g, "lb": weight_g / GRAMS_PER_POUND}


def _weighted_flour_attribute(
    flour_rows: list[dict[str, Any]], field: str, total_flour_g: float
) -> float | None:
    if not flour_rows or any(row.get(field) is None for row in flour_rows):
        return None
    return sum(row["weight_g"] * row[field] for row in flour_rows) / total_flour_g


def calculate_formula(value: dict[str, Any]) -> dict[str, Any]:
    formula = normalize_formula(value)
    warnings: list[str] = []
    final_flour_blend = _normalize_blend(formula["flours"], "Final mix flour blend", warnings)

    target_weight_g = formula["ball_count"] * formula["ball_weight_g"]
    if target_weight_g <= 0:
        raise FormulaError("Target dough weight must be greater than zero.")

    mature_starter_pct_of_flour = sum(
        pref["amount_pct"] * pref["leavening_pct"] / 100
        for pref in formula["preferments"]
        if pref["leavening_type"] == "Mature Starter"
    )
    ingredient_pct = sum(item["pct"] for item in formula["ingredients"])
    total_bakers_pct = (
        100
        + formula["hydration_pct"]
        + formula["salt_pct"]
        + formula["yeast_pct"]
        + ingredient_pct
        + mature_starter_pct_of_flour
    )
    scaled_target_g = target_weight_g * (1 + formula["residue_pct"] / 100)
    total_flour_g = scaled_target_g / (total_bakers_pct / 100)
    total_water_g = total_flour_g * formula["hydration_pct"] / 100
    total_yeast_g = total_flour_g * formula["yeast_pct"] / 100

    preferment_results: list[dict[str, Any]] = []
    preferment_flour_rows: list[dict[str, Any]] = []
    total_preferment_flour = 0.0
    total_preferment_water = 0.0
    total_preferment_formula_yeast = 0.0
    preferment_total_pct = 0.0

    for pref in formula["preferments"]:
        component_total = pref["water_pct"] + pref["leavening_pct"]
        if component_total > 100.0001:
            raise FormulaError(
                f"{pref['name']} water and leavening percentages cannot exceed 100% of the preferment."
            )
        pref_total_g = total_flour_g * pref["amount_pct"] / 100
        pref_water_g = pref_total_g * pref["water_pct"] / 100
        pref_leavening_g = pref_total_g * pref["leavening_pct"] / 100
        pref_flour_g = max(0.0, pref_total_g - pref_water_g - pref_leavening_g)
        pref_flour_pct = pref_flour_g / total_flour_g * 100
        pref_blend = _normalize_blend(pref["flours"], f"{pref['name']} flour blend", warnings)
        allocated_flours = []
        for flour in pref_blend:
            amount = pref_flour_g * flour["pct"] / 100
            row = {
                "name": flour["name"],
                "pct": amount / total_flour_g * 100,
                "blend_pct": flour["pct"],
                "protein_pct": flour.get("protein_pct"),
                "ash_pct": flour.get("ash_pct"),
                "weight_g": amount,
                "source": pref["name"],
            }
            allocated_flours.append(row)
            preferment_flour_rows.append(row)

        uses_formula_yeast = pref["leavening_type"] not in {"None", "Mature Starter"}
        if uses_formula_yeast:
            total_preferment_formula_yeast += pref_leavening_g
            if pref["leavening_type"] not in {"None", formula["yeast_type"]}:
                warnings.append(
                    f"{pref['name']} uses {pref['leavening_type']} while the formula yeast is {formula['yeast_type']}."
                )
        total_preferment_flour += pref_flour_g
        total_preferment_water += pref_water_g
        preferment_total_pct += pref["amount_pct"]
        preferment_results.append(
            {
                **pref,
                "flour_pct_of_preferment": 100 - component_total,
                "flour_pct_of_total": pref_flour_pct,
                "flour_g": pref_flour_g,
                "water_g": pref_water_g,
                "leavening_g": pref_leavening_g,
                "total_g": pref_total_g,
                "allocated_flours": allocated_flours,
            }
        )

    final_flour_g = total_flour_g - total_preferment_flour
    final_water_g = total_water_g - total_preferment_water
    final_yeast_g = total_yeast_g - total_preferment_formula_yeast
    if final_flour_g < -0.01:
        raise FormulaError("Preferments contain more flour than the complete formula allows.")
    if final_water_g < -0.01:
        raise FormulaError("Preferments contain more water than the complete formula allows.")
    if final_yeast_g < -0.01:
        raise FormulaError("Preferments contain more yeast than the complete formula allows.")
    final_flour_g = max(0.0, final_flour_g)
    final_water_g = max(0.0, final_water_g)
    final_yeast_g = max(0.0, final_yeast_g)

    final_flour_rows = []
    for flour in final_flour_blend:
        amount = final_flour_g * flour["pct"] / 100
        final_flour_rows.append(
            {
                "name": flour["name"],
                "pct": amount / total_flour_g * 100,
                "blend_pct": flour["pct"],
                "protein_pct": flour.get("protein_pct"),
                "ash_pct": flour.get("ash_pct"),
                "weight_g": amount,
                "source": "Final Mix",
            }
        )

    all_flour_rows = final_flour_rows + preferment_flour_rows
    overall_protein_pct = _weighted_flour_attribute(all_flour_rows, "protein_pct", total_flour_g)
    overall_ash_pct = _weighted_flour_attribute(all_flour_rows, "ash_pct", total_flour_g)
    if overall_protein_pct is None:
        warnings.append("Enter protein content for every flour to calculate overall protein.")
    if overall_ash_pct is None:
        warnings.append("Enter ash content for every flour to calculate overall ash.")

    ingredient_rows = [
        {
            "name": row["name"],
            "source": row["source"],
            "category": "Flour",
            "pct": row["pct"],
            "protein_pct": row["protein_pct"],
            "ash_pct": row["ash_pct"],
            "weight_g": row["weight_g"],
            "units": units(row["weight_g"]),
        }
        for row in all_flour_rows
    ]
    ingredient_rows.append(
        {
            "name": "Water",
            "source": "Complete Formula",
            "category": "Water",
            "pct": formula["hydration_pct"],
            "weight_g": total_water_g,
            "units": units(total_water_g),
        }
    )
    if total_yeast_g > 0:
        ingredient_rows.append(
            {
                "name": formula["yeast_type"],
                "source": "Complete Formula",
                "category": "Yeast",
                "pct": formula["yeast_pct"],
                "weight_g": total_yeast_g,
                "units": units(total_yeast_g),
            }
        )
    salt_g = total_flour_g * formula["salt_pct"] / 100
    ingredient_rows.append(
        {
            "name": "Salt",
            "source": "Final Mix",
            "category": "Salt",
            "pct": formula["salt_pct"],
            "weight_g": salt_g,
            "units": units(salt_g),
        }
    )
    for ingredient in formula["ingredients"]:
        weight = total_flour_g * ingredient["pct"] / 100
        ingredient_rows.append(
            {
                "name": ingredient["name"],
                "source": "Final Mix",
                "category": "Ingredient",
                "pct": ingredient["pct"],
                "weight_g": weight,
                "units": units(weight),
            }
        )
    for pref in preferment_results:
        if pref["leavening_type"] == "Mature Starter" and pref["leavening_g"] > 0:
            ingredient_rows.append(
                {
                    "name": f"{pref['name']} Mature Starter",
                    "source": pref["name"],
                    "category": "Preferment Leavening",
                    "pct": pref["amount_pct"] * pref["leavening_pct"] / 100,
                    "weight_g": pref["leavening_g"],
                    "units": units(pref["leavening_g"]),
                }
            )

    final_mix_rows = [
        {"name": row["name"], "weight_g": row["weight_g"], "category": "Flour"}
        for row in final_flour_rows
        if row["weight_g"] > 0.005
    ]
    final_mix_rows.append({"name": "Water", "weight_g": final_water_g, "category": "Water"})
    if final_yeast_g > 0.005:
        final_mix_rows.append(
            {"name": formula["yeast_type"], "weight_g": final_yeast_g, "category": "Yeast"}
        )
    final_mix_rows.append({"name": "Salt", "weight_g": salt_g, "category": "Salt"})
    for ingredient in formula["ingredients"]:
        final_mix_rows.append(
            {
                "name": ingredient["name"],
                "weight_g": total_flour_g * ingredient["pct"] / 100,
                "category": "Ingredient",
            }
        )

    return {
        "formula": formula,
        "target_weight_g": target_weight_g,
        "scaled_target_g": scaled_target_g,
        "single_ball_g": formula["ball_weight_g"],
        "ball_count": formula["ball_count"],
        "total_flour_g": total_flour_g,
        "total_water_g": total_water_g,
        "total_yeast_g": total_yeast_g,
        "total_bakers_pct": total_bakers_pct,
        "preferment_total_pct": preferment_total_pct,
        "prefermented_flour_pct": total_preferment_flour / total_flour_g * 100,
        "overall_protein_pct": overall_protein_pct,
        "overall_ash_pct": overall_ash_pct,
        "ingredients": ingredient_rows,
        "preferments": preferment_results,
        "final_mix": final_mix_rows,
        "warnings": warnings,
    }
