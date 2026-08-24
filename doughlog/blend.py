from __future__ import annotations

import math
from typing import Any


class BlendError(ValueError):
    pass


def _attribute(flour: dict[str, Any], field: str) -> float:
    try:
        value = float(flour[field])
    except (KeyError, TypeError, ValueError) as error:
        raise BlendError(
            f"{flour.get('name') or 'Each flour'} needs a valid {field.replace('_pct', '')} percentage."
        ) from error
    if not math.isfinite(value) or not 0 <= value <= 100:
        raise BlendError(
            f"{flour.get('name') or 'Each flour'} needs a valid {field.replace('_pct', '')} percentage."
        )
    return value


def _whole_number_solution(
    protein: list[float],
    ash: list[float],
    target_protein: float,
    target_ash: float,
    minimum: int,
) -> list[int]:
    """Return the closest integer blend, preferring portions near 25% each."""
    best_weights: list[int] | None = None
    best_error = math.inf
    best_equal_parts_distance = math.inf
    protein_first, protein_second, protein_third, protein_fourth = protein
    ash_first, ash_second, ash_third, ash_fourth = ash

    for first in range(minimum, 100 - 3 * minimum + 1):
        for second in range(minimum, 100 - first - 2 * minimum + 1):
            for third in range(minimum, 100 - first - second - minimum + 1):
                fourth = 100 - first - second - third
                achieved_protein = (
                    first * protein_first
                    + second * protein_second
                    + third * protein_third
                    + fourth * protein_fourth
                ) / 100
                achieved_ash = (
                    first * ash_first
                    + second * ash_second
                    + third * ash_third
                    + fourth * ash_fourth
                ) / 100
                # Treat 0.10 ash points as comparable to 1.00 protein point.
                error = (
                    (achieved_protein - target_protein) ** 2
                    + ((achieved_ash - target_ash) / 0.1) ** 2
                )
                equal_parts_distance = (
                    (first - 25) ** 2
                    + (second - 25) ** 2
                    + (third - 25) ** 2
                    + (fourth - 25) ** 2
                )
                if (
                    error < best_error - 1e-12
                    or (
                        abs(error - best_error) <= 1e-12
                        and equal_parts_distance < best_equal_parts_distance
                    )
                ):
                    best_weights = [first, second, third, fourth]
                    best_error = error
                    best_equal_parts_distance = equal_parts_distance

    if best_weights is None:
        raise BlendError("The selected minimum leaves no valid four-flour blend.")
    return best_weights


def solve_flour_blend(
    flours: list[dict[str, Any]],
    *,
    target_protein_pct: float,
    target_ash_pct: float,
    minimum_flour_pct: float = 1.0,
) -> dict[str, Any]:
    """Find the closest whole-number four-flour blend.

    Every flour share is a whole-number percentage, is at least
    ``minimum_flour_pct``, and the four shares always total 100%.
    """
    if len(flours) != 4:
        raise BlendError("Choose exactly four flours for the blend.")
    for label, value in (
        ("Desired protein", target_protein_pct),
        ("Desired ash", target_ash_pct),
    ):
        if not math.isfinite(value) or not 0 <= value <= 100:
            raise BlendError(f"{label} must be between 0% and 100%.")
    if not math.isfinite(minimum_flour_pct) or not 0 <= minimum_flour_pct <= 25:
        raise BlendError(
            "Minimum each flour must be between 0% and 25%."
        )
    minimum = round(minimum_flour_pct)
    if abs(minimum_flour_pct - minimum) > 1e-9:
        raise BlendError("Minimum each flour must be a whole-number percentage.")

    protein = [_attribute(flour, "protein_pct") for flour in flours]
    ash = [_attribute(flour, "ash_pct") for flour in flours]
    weights = _whole_number_solution(
        protein,
        ash,
        target_protein_pct,
        target_ash_pct,
        minimum,
    )
    achieved_protein = sum(
        weight * value for weight, value in zip(weights, protein)
    ) / 100
    achieved_ash = sum(weight * value for weight, value in zip(weights, ash)) / 100
    protein_delta = achieved_protein - target_protein_pct
    ash_delta = achieved_ash - target_ash_pct
    rows = [
        {
            "name": flour["name"],
            "mill": str(flour.get("mill") or ""),
            "protein_pct": protein[index],
            "ash_pct": ash[index],
            "blend_pct": weights[index],
        }
        for index, flour in enumerate(flours)
    ]
    return {
        "is_exact": abs(protein_delta) <= 1e-9 and abs(ash_delta) <= 1e-9,
        "target_protein_pct": target_protein_pct,
        "target_ash_pct": target_ash_pct,
        "achieved_protein_pct": achieved_protein,
        "achieved_ash_pct": achieved_ash,
        "protein_delta": protein_delta,
        "ash_delta": ash_delta,
        "minimum_flour_pct": minimum,
        "rows": rows,
    }
