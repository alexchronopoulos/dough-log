from __future__ import annotations

import json
import os
import sqlite3
import uuid
from datetime import date, datetime
from io import BytesIO
from pathlib import Path
from typing import Any

from flask import (
    Blueprint,
    abort,
    current_app,
    flash,
    jsonify,
    redirect,
    render_template,
    request,
    send_from_directory,
    url_for,
)
from PIL import Image, ImageOps, UnidentifiedImageError
from werkzeug.utils import secure_filename

from .calculator import DEFAULT_FORMULA, FormulaError, calculate_formula, normalize_formula
from .db import get_db


try:
    from pillow_heif import register_heif_opener

    register_heif_opener()
except ImportError:  # pragma: no cover - Pillow still supports common web formats.
    pass


bp = Blueprint("main", __name__)


def _now() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def _json(value: Any) -> str:
    return json.dumps(value, separators=(",", ":"), ensure_ascii=False)


def _loads(value: str | None, default: Any) -> Any:
    if not value:
        return default
    try:
        return json.loads(value)
    except (TypeError, json.JSONDecodeError):
        return default


def _number(value: Any, default: float = 0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _float(name: str, default: float = 0) -> float:
    try:
        return float(request.form.get(name, default))
    except (TypeError, ValueError):
        return default


def _optional_float(name: str) -> float | None:
    value = request.form.get(name, "").strip()
    if not value:
        return None
    try:
        return float(value)
    except ValueError:
        return None


def _optional_int(name: str) -> int | None:
    value = request.form.get(name, "").strip()
    if not value:
        return None
    try:
        return int(value)
    except ValueError:
        return None


def _formula_from_form() -> dict[str, Any]:
    yeast_type = request.form.get("yeast_type", "IDY").strip() or "IDY"
    formula = {
        "ball_count": max(1, int(_float("ball_count", 1))),
        "ball_weight_g": _float("ball_weight_g", 700),
        "hydration_pct": _float("hydration_pct", 70),
        "salt_pct": _float("salt_pct", 3),
        "yeast_type": yeast_type,
        "yeast_pct": 0 if yeast_type == "None" else _float("yeast_pct", 0.07),
        "residue_pct": _float("residue_pct", 1),
        "flours": _loads(request.form.get("flours_json"), []),
        "ingredients": _loads(request.form.get("ingredients_json"), []),
        "preferments": _loads(request.form.get("preferments_json"), []),
    }
    return normalize_formula(formula)


def _flour_library() -> list[dict[str, Any]]:
    return [
        dict(row)
        for row in get_db()
        .execute("SELECT * FROM flour_library ORDER BY name COLLATE NOCASE")
        .fetchall()
    ]


def _flour_values() -> tuple[str, float, float]:
    name = request.form.get("name", "").strip()
    protein_pct = _optional_float("protein_pct")
    ash_pct = _optional_float("ash_pct")
    if not name:
        raise FormulaError("Flour name is required.")
    if protein_pct is None or not 0 <= protein_pct <= 100:
        raise FormulaError("Protein must be a percentage between 0 and 100.")
    if ash_pct is None or not 0 <= ash_pct <= 100:
        raise FormulaError("Ash must be a percentage between 0 and 100.")
    return name, protein_pct, ash_pct


def _recipe_choices() -> list[dict[str, Any]]:
    return [
        {
            "id": row["id"],
            "name": row["name"],
            "formula": normalize_formula(_loads(row["formula_json"], DEFAULT_FORMULA)),
        }
        for row in get_db()
        .execute(
            "SELECT id, name, formula_json FROM recipe_templates "
            "WHERE is_archived = 0 ORDER BY name"
        )
        .fetchall()
    ]


def _template(template_id: int):
    row = get_db().execute("SELECT * FROM recipe_templates WHERE id = ?", (template_id,)).fetchone()
    if row is None:
        abort(404)
    return row


def _log(log_id: int):
    row = get_db().execute("SELECT * FROM dough_logs WHERE id = ?", (log_id,)).fetchone()
    if row is None:
        abort(404)
    return row


def _log_view(row) -> dict[str, Any]:
    value = dict(row)
    value["formula"] = normalize_formula(_loads(value.pop("formula_json"), DEFAULT_FORMULA))
    value["calculated"] = _loads(value.pop("calculated_json"), {})
    if (
        "preferment_total_pct" not in value["calculated"]
        or "overall_protein_pct" not in value["calculated"]
    ):
        value["calculated"] = calculate_formula(value["formula"])
    value["mix_stages"] = _loads(value.pop("mix_stages_json"), [])
    value["total_mix_minutes"] = sum(
        _number(stage.get("minutes"))
        for stage in value["mix_stages"]
        if isinstance(stage, dict)
    )
    value.pop("performance_tags_json", None)
    value.pop("actual_ball_count", None)
    value["photos"] = [
        dict(photo)
        for photo in get_db()
        .execute("SELECT * FROM photos WHERE dough_log_id = ? ORDER BY created_at, id", (row["id"],))
        .fetchall()
    ]
    return value


def _history_view(row) -> dict[str, Any]:
    value = dict(row)
    formula = normalize_formula(_loads(value.get("formula_json"), DEFAULT_FORMULA))
    calculated = _loads(value.get("calculated_json"), {})
    if "overall_protein_pct" not in calculated:
        calculated = calculate_formula(formula)

    flour_names: list[str] = []
    for flour in formula["flours"]:
        if flour["name"] not in flour_names:
            flour_names.append(flour["name"])
    for preferment in formula["preferments"]:
        for flour in preferment["flours"]:
            if flour["name"] not in flour_names:
                flour_names.append(flour["name"])

    value["hydration_pct"] = formula["hydration_pct"]
    value["flour_names"] = flour_names
    value["idy_pct"] = formula["yeast_pct"] if formula["yeast_type"] == "IDY" else None
    value["overall_protein_pct"] = calculated.get("overall_protein_pct")
    value["overall_ash_pct"] = calculated.get("overall_ash_pct")
    return value


def _comparison_metric(value: Any, digits: int = 2, suffix: str = "") -> str:
    if value is None or value == "":
        return "—"
    try:
        return f"{float(value):.{digits}f}{suffix}"
    except (TypeError, ValueError):
        return str(value)


def _comparison_flours(items: list[dict[str, Any]]) -> str:
    if not items:
        return "—"
    values = []
    for item in items:
        details = [f"{_comparison_metric(item.get('pct'), 2, '%')} blend"]
        if item.get("protein_pct") is not None:
            details.append(f"protein {_comparison_metric(item.get('protein_pct'), 2, '%')}")
        if item.get("ash_pct") is not None:
            details.append(f"ash {_comparison_metric(item.get('ash_pct'), 2, '%')}")
        values.append(f"{item.get('name') or 'Unnamed flour'} · {', '.join(details)}")
    return "; ".join(values)


def _comparison_ingredients(items: list[dict[str, Any]]) -> str:
    if not items:
        return "—"
    return "; ".join(
        f"{item.get('name') or 'Unnamed ingredient'} · "
        f"{_comparison_metric(item.get('pct'), 3, '%')}"
        for item in items
    )


def _comparison_stages(items: list[dict[str, Any]]) -> str:
    if not items:
        return "—"
    values = []
    for index, item in enumerate(items, 1):
        value = (
            f"{index}. {item.get('speed') or 'Unspecified'} · "
            f"{_comparison_metric(item.get('minutes'), 1, ' min')}"
        )
        if item.get("notes"):
            value += f" · {item['notes']}"
        values.append(value)
    return "\n".join(values)


def _ordered_union(left: list[Any], right: list[Any]) -> list[Any]:
    values: list[Any] = []
    for item in [*left, *right]:
        if item not in values:
            values.append(item)
    return values


def _comparison_sections(left: dict[str, Any], right: dict[str, Any]) -> list[dict[str, Any]]:
    sections: list[dict[str, Any]] = []

    def section(title: str, values: list[tuple[str, str, str]]) -> None:
        sections.append(
            {
                "title": title,
                "rows": [
                    {
                        "label": label,
                        "left": left_value,
                        "right": right_value,
                        "changed": left_value != right_value,
                    }
                    for label, left_value, right_value in values
                ],
            }
        )

    section(
        "Service Record",
        [
            ("Service date", pretty_date(left["service_date"]), pretty_date(right["service_date"])),
            ("Log title", left["title"] or "—", right["title"] or "—"),
            ("Mixed on", pretty_datetime(left["mix_datetime"]), pretty_datetime(right["mix_datetime"])),
        ],
    )

    left_formula = left["formula"]
    right_formula = right["formula"]
    left_calculated = left["calculated"]
    right_calculated = right["calculated"]
    section(
        "Batch & Formula",
        [
            ("Dough balls", _comparison_metric(left_formula["ball_count"], 0), _comparison_metric(right_formula["ball_count"], 0)),
            ("Ball weight", _comparison_metric(left_formula["ball_weight_g"], 0, " g"), _comparison_metric(right_formula["ball_weight_g"], 0, " g")),
            ("Target dough", _comparison_metric(left_calculated.get("target_weight_g"), 0, " g"), _comparison_metric(right_calculated.get("target_weight_g"), 0, " g")),
            ("Hydration", _comparison_metric(left_formula["hydration_pct"], 2, "%"), _comparison_metric(right_formula["hydration_pct"], 2, "%")),
            ("Salt", _comparison_metric(left_formula["salt_pct"], 2, "%"), _comparison_metric(right_formula["salt_pct"], 2, "%")),
            ("Yeast type", left_formula["yeast_type"] or "—", right_formula["yeast_type"] or "—"),
            ("Total yeast", _comparison_metric(left_formula["yeast_pct"], 3, "%"), _comparison_metric(right_formula["yeast_pct"], 3, "%")),
            ("Bowl residue compensation", _comparison_metric(left_formula["residue_pct"], 2, "%"), _comparison_metric(right_formula["residue_pct"], 2, "%")),
            ("Final-mix flour blend", _comparison_flours(left_formula["flours"]), _comparison_flours(right_formula["flours"])),
            ("Additional ingredients", _comparison_ingredients(left_formula["ingredients"]), _comparison_ingredients(right_formula["ingredients"])),
            ("Preferment / total flour", _comparison_metric(left_calculated.get("preferment_total_pct"), 2, "%"), _comparison_metric(right_calculated.get("preferment_total_pct"), 2, "%")),
            ("Prefermented flour", _comparison_metric(left_calculated.get("prefermented_flour_pct"), 2, "%"), _comparison_metric(right_calculated.get("prefermented_flour_pct"), 2, "%")),
            ("Overall protein", _comparison_metric(left_calculated.get("overall_protein_pct"), 2, "%"), _comparison_metric(right_calculated.get("overall_protein_pct"), 2, "%")),
            ("Overall ash", _comparison_metric(left_calculated.get("overall_ash_pct"), 2, "%"), _comparison_metric(right_calculated.get("overall_ash_pct"), 2, "%")),
        ],
    )

    left_preferments = {item["name"]: item for item in left_formula["preferments"]}
    right_preferments = {item["name"]: item for item in right_formula["preferments"]}
    preferment_rows: list[tuple[str, str, str]] = []
    for name in _ordered_union(list(left_preferments), list(right_preferments)):
        left_pref = left_preferments.get(name)
        right_pref = right_preferments.get(name)
        preferment_rows.extend(
            [
                (f"{name} · Included", "Yes" if left_pref else "No", "Yes" if right_pref else "No"),
                (f"{name} · Type", left_pref.get("type", "—") if left_pref else "—", right_pref.get("type", "—") if right_pref else "—"),
                (f"{name} · Preferment / total flour", _comparison_metric(left_pref.get("amount_pct") if left_pref else None, 2, "%"), _comparison_metric(right_pref.get("amount_pct") if right_pref else None, 2, "%")),
                (f"{name} · Water / preferment", _comparison_metric(left_pref.get("water_pct") if left_pref else None, 2, "%"), _comparison_metric(right_pref.get("water_pct") if right_pref else None, 2, "%")),
                (f"{name} · Leavening type", left_pref.get("leavening_type", "—") if left_pref else "—", right_pref.get("leavening_type", "—") if right_pref else "—"),
                (f"{name} · Leavening / preferment", _comparison_metric(left_pref.get("leavening_pct") if left_pref else None, 3, "%"), _comparison_metric(right_pref.get("leavening_pct") if right_pref else None, 3, "%")),
                (f"{name} · Flour blend", _comparison_flours(left_pref.get("flours", []) if left_pref else []), _comparison_flours(right_pref.get("flours", []) if right_pref else [])),
                (f"{name} · Notes", left_pref.get("notes") or "—" if left_pref else "—", right_pref.get("notes") or "—" if right_pref else "—"),
            ]
        )
    if preferment_rows:
        section("Preferments", preferment_rows)

    def ingredient_weights(log: dict[str, Any]) -> dict[tuple[str, str], str]:
        return {
            (item.get("name", "Unnamed"), item.get("source", "Formula")): (
                f"{_comparison_metric(item.get('units', {}).get('g'), 1, ' g')} / "
                f"{_comparison_metric(item.get('units', {}).get('lb'), 3, ' lb')}"
            )
            for item in log["calculated"].get("ingredients", [])
        }

    left_weights = ingredient_weights(left)
    right_weights = ingredient_weights(right)
    weight_rows = [
        (f"{name} · {source}", left_weights.get((name, source), "—"), right_weights.get((name, source), "—"))
        for name, source in _ordered_union(list(left_weights), list(right_weights))
    ]
    if weight_rows:
        section("Calculated Ingredient Weights", weight_rows)

    section(
        "Mix & Conditions",
        [
            ("Room temperature", _comparison_metric(left.get("room_temp_f"), 1, "°F"), _comparison_metric(right.get("room_temp_f"), 1, "°F")),
            ("Humidity", _comparison_metric(left.get("humidity_pct"), 1, "%"), _comparison_metric(right.get("humidity_pct"), 1, "%")),
            ("Flour temperature", _comparison_metric(left.get("flour_temp_f"), 1, "°F"), _comparison_metric(right.get("flour_temp_f"), 1, "°F")),
            ("Water temperature", _comparison_metric(left.get("water_temp_f"), 1, "°F"), _comparison_metric(right.get("water_temp_f"), 1, "°F")),
            ("Desired final dough temperature", _comparison_metric(left.get("desired_final_dough_temp_f"), 1, "°F"), _comparison_metric(right.get("desired_final_dough_temp_f"), 1, "°F")),
            ("Actual final dough temperature", _comparison_metric(left.get("final_dough_temp_f"), 1, "°F"), _comparison_metric(right.get("final_dough_temp_f"), 1, "°F")),
            ("Total mix time", _comparison_metric(left.get("total_mix_minutes"), 1, " min"), _comparison_metric(right.get("total_mix_minutes"), 1, " min")),
            ("Mix stages", _comparison_stages(left["mix_stages"]), _comparison_stages(right["mix_stages"])),
            ("Mix notes", left.get("mix_notes") or "—", right.get("mix_notes") or "—"),
        ],
    )
    section(
        "Service Result",
        [
            ("Rating", _comparison_metric(left.get("overall_rating"), 0, " / 5"), _comparison_metric(right.get("overall_rating"), 0, " / 5")),
            ("Service notes", left.get("service_notes") or "—", right.get("service_notes") or "—"),
            ("Pizza photos", _comparison_metric(len(left["photos"]), 0), _comparison_metric(len(right["photos"]), 0)),
        ],
    )
    return sections


@bp.app_template_filter("pretty_date")
def pretty_date(value: str | None) -> str:
    if not value:
        return ""
    try:
        parsed = date.fromisoformat(value)
        return parsed.strftime("%A, %B %-d, %Y")
    except ValueError:
        return value


@bp.app_template_filter("pretty_datetime")
def pretty_datetime(value: str | None) -> str:
    if not value:
        return ""
    try:
        parsed = datetime.fromisoformat(value)
        return parsed.strftime("%a, %b %-d at %-I:%M %p")
    except ValueError:
        return value


@bp.get("/")
def index():
    db = get_db()
    recent_logs = db.execute(
        "SELECT * FROM dough_logs ORDER BY service_date DESC, id DESC LIMIT 8"
    ).fetchall()
    return render_template("index.html", recent_logs=recent_logs)


@bp.get("/health")
def health():
    get_db().execute("SELECT 1").fetchone()
    return jsonify({"status": "ok"})


@bp.route("/templates")
def templates_list():
    rows = get_db().execute(
        "SELECT * FROM recipe_templates ORDER BY is_archived, updated_at DESC"
    ).fetchall()
    return render_template("templates_list.html", templates=rows)


@bp.route("/flours", methods=("GET", "POST"))
def flours():
    if request.method == "POST":
        try:
            name, protein_pct, ash_pct = _flour_values()
            now = _now()
            get_db().execute(
                """
                INSERT INTO flour_library (name, protein_pct, ash_pct, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?)
                """,
                (name, protein_pct, ash_pct, now, now),
            )
            get_db().commit()
        except FormulaError as error:
            flash(str(error), "error")
        except sqlite3.IntegrityError:
            flash("A flour with that name already exists.", "error")
        else:
            flash(f"Added {name} to the flour library.", "success")
            return redirect(url_for("main.flours"))
    return render_template("flours.html", flours=_flour_library())


@bp.post("/flours/<int:flour_id>/edit")
def flour_edit(flour_id: int):
    if get_db().execute("SELECT id FROM flour_library WHERE id = ?", (flour_id,)).fetchone() is None:
        abort(404)
    try:
        name, protein_pct, ash_pct = _flour_values()
        get_db().execute(
            """
            UPDATE flour_library
            SET name = ?, protein_pct = ?, ash_pct = ?, updated_at = ?
            WHERE id = ?
            """,
            (name, protein_pct, ash_pct, _now(), flour_id),
        )
        get_db().commit()
    except FormulaError as error:
        flash(str(error), "error")
    except sqlite3.IntegrityError:
        flash("A flour with that name already exists.", "error")
    else:
        flash(f"Updated {name}.", "success")
    return redirect(url_for("main.flours"))


@bp.post("/flours/<int:flour_id>/delete")
def flour_delete(flour_id: int):
    row = get_db().execute("SELECT name FROM flour_library WHERE id = ?", (flour_id,)).fetchone()
    if row is None:
        abort(404)
    get_db().execute("DELETE FROM flour_library WHERE id = ?", (flour_id,))
    get_db().commit()
    flash(f"Removed {row['name']} from the flour library. Saved formulas were not changed.", "success")
    return redirect(url_for("main.flours"))


@bp.route("/templates/new", methods=("GET", "POST"))
def template_new():
    formula = normalize_formula(DEFAULT_FORMULA)
    if request.method == "POST":
        name = request.form.get("name", "").strip()
        description = request.form.get("description", "").strip()
        try:
            formula = _formula_from_form()
            calculate_formula(formula)
            if not name:
                raise FormulaError("Template name is required.")
        except FormulaError as error:
            flash(str(error), "error")
            return render_template(
                "recipe_form.html",
                page_title="New recipe template",
                form_kind="template",
                formula=formula,
                record={"name": name, "description": description},
                flour_library=_flour_library(),
            )
        now = _now()
        cursor = get_db().execute(
            """
            INSERT INTO recipe_templates (name, description, formula_json, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?)
            """,
            (name, description, _json(formula), now, now),
        )
        get_db().commit()
        flash("Recipe template saved.", "success")
        return redirect(url_for("main.template_edit", template_id=cursor.lastrowid))

    return render_template(
        "recipe_form.html",
        page_title="New recipe template",
        form_kind="template",
        formula=formula,
        record={"name": "", "description": ""},
        flour_library=_flour_library(),
    )


@bp.route("/templates/<int:template_id>/edit", methods=("GET", "POST"))
def template_edit(template_id: int):
    row = _template(template_id)
    formula = normalize_formula(_loads(row["formula_json"], DEFAULT_FORMULA))
    record = dict(row)
    if request.method == "POST":
        record["name"] = request.form.get("name", "").strip()
        record["description"] = request.form.get("description", "").strip()
        try:
            formula = _formula_from_form()
            calculate_formula(formula)
            if not record["name"]:
                raise FormulaError("Template name is required.")
        except FormulaError as error:
            flash(str(error), "error")
        else:
            get_db().execute(
                """
                UPDATE recipe_templates
                SET name = ?, description = ?, formula_json = ?, updated_at = ?
                WHERE id = ?
                """,
                (record["name"], record["description"], _json(formula), _now(), template_id),
            )
            get_db().commit()
            flash("Recipe template updated. Existing dough logs were not changed.", "success")
            return redirect(url_for("main.template_edit", template_id=template_id))
    return render_template(
        "recipe_form.html",
        page_title=f"Edit {record['name']}",
        form_kind="template",
        formula=formula,
        record=record,
        flour_library=_flour_library(),
    )


@bp.post("/templates/<int:template_id>/archive")
def template_archive(template_id: int):
    row = _template(template_id)
    archived = 0 if row["is_archived"] else 1
    get_db().execute(
        "UPDATE recipe_templates SET is_archived = ?, updated_at = ? WHERE id = ?",
        (archived, _now(), template_id),
    )
    get_db().commit()
    flash("Template restored." if not archived else "Template archived.", "success")
    return redirect(url_for("main.templates_list"))


@bp.route("/logs/new", methods=("GET", "POST"))
def log_new():
    db = get_db()
    templates = _recipe_choices()
    template_id = request.args.get("template", type=int)
    selected_template = _template(template_id) if template_id else None
    formula = normalize_formula(
        _loads(selected_template["formula_json"], DEFAULT_FORMULA) if selected_template else DEFAULT_FORMULA
    )
    record: dict[str, Any] = {
        "template_id": template_id,
        "title": selected_template["name"] if selected_template else "Service Dough",
        "service_date": date.today().isoformat(),
        "mix_datetime": datetime.now().astimezone().strftime("%Y-%m-%dT%H:%M"),
        "room_temp_f": "",
        "humidity_pct": "",
        "flour_temp_f": "",
        "water_temp_f": "",
        "desired_final_dough_temp_f": "",
        "final_dough_temp_f": "",
        "mix_notes": "",
        "service_notes": "",
        "overall_rating": None,
        "mix_stages": [{"speed": "Low", "minutes": 5, "notes": ""}],
    }
    if request.method == "POST":
        record.update(request.form)
        record["template_id"] = request.form.get("template_id", type=int)
        record["mix_stages"] = _loads(request.form.get("mix_stages_json"), [])
        try:
            formula = _formula_from_form()
            calculated = calculate_formula(formula)
            if not request.form.get("service_date"):
                raise FormulaError("Service date is required.")
            if not request.form.get("mix_datetime"):
                raise FormulaError("Mix date and time are required.")
        except FormulaError as error:
            flash(str(error), "error")
            return render_template(
                "recipe_form.html",
                page_title="Create dough log",
                form_kind="log",
                formula=formula,
                record=record,
                templates=templates,
                flour_library=_flour_library(),
            )

        now = _now()
        cursor = db.execute(
            """
            INSERT INTO dough_logs (
                template_id, title, service_date, mix_datetime, formula_json, calculated_json,
                room_temp_f, humidity_pct, flour_temp_f, water_temp_f,
                desired_final_dough_temp_f, final_dough_temp_f, mix_stages_json, mix_notes,
                service_notes, overall_rating,
                created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                record["template_id"],
                request.form.get("title", "Service Dough").strip() or "Service Dough",
                request.form["service_date"],
                request.form["mix_datetime"],
                _json(formula),
                _json(calculated),
                _optional_float("room_temp_f"),
                _optional_float("humidity_pct"),
                _optional_float("flour_temp_f"),
                _optional_float("water_temp_f"),
                _optional_float("desired_final_dough_temp_f"),
                _optional_float("final_dough_temp_f"),
                _json(record["mix_stages"]),
                request.form.get("mix_notes", "").strip(),
                request.form.get("service_notes", "").strip(),
                _optional_int("overall_rating"),
                now,
                now,
            ),
        )
        db.commit()
        flash("Dough log created. The formula is now preserved as a service-day snapshot.", "success")
        return redirect(url_for("main.log_detail", log_id=cursor.lastrowid))

    return render_template(
        "recipe_form.html",
        page_title="Create dough log",
        form_kind="log",
        formula=formula,
        record=record,
        templates=templates,
        flour_library=_flour_library(),
    )


@bp.get("/logs/<int:log_id>")
def log_detail(log_id: int):
    return render_template("log_detail.html", log=_log_view(_log(log_id)))


@bp.route("/logs/<int:log_id>/edit", methods=("GET", "POST"))
def log_edit(log_id: int):
    db = get_db()
    templates = _recipe_choices()
    row = _log(log_id)
    record = _log_view(row)
    formula = normalize_formula(record["formula"])
    if request.method == "POST":
        record.update(request.form)
        record["template_id"] = request.form.get("template_id", type=int)
        record["mix_stages"] = _loads(request.form.get("mix_stages_json"), [])
        try:
            formula = _formula_from_form()
            calculated = calculate_formula(formula)
            rating = _optional_int("overall_rating")
            if rating is not None and rating not in range(1, 6):
                raise FormulaError("Rating must be between 1 and 5.")
        except FormulaError as error:
            flash(str(error), "error")
        else:
            db.execute(
                """
                UPDATE dough_logs SET
                    template_id = ?, title = ?, service_date = ?, mix_datetime = ?,
                    formula_json = ?, calculated_json = ?, room_temp_f = ?, humidity_pct = ?,
                    flour_temp_f = ?, water_temp_f = ?, desired_final_dough_temp_f = ?,
                    final_dough_temp_f = ?, mix_stages_json = ?, mix_notes = ?, service_notes = ?,
                    overall_rating = ?, updated_at = ?
                WHERE id = ?
                """,
                (
                    record["template_id"],
                    request.form.get("title", "Service Dough").strip() or "Service Dough",
                    request.form["service_date"],
                    request.form["mix_datetime"],
                    _json(formula),
                    _json(calculated),
                    _optional_float("room_temp_f"),
                    _optional_float("humidity_pct"),
                    _optional_float("flour_temp_f"),
                    _optional_float("water_temp_f"),
                    _optional_float("desired_final_dough_temp_f"),
                    _optional_float("final_dough_temp_f"),
                    _json(record["mix_stages"]),
                    request.form.get("mix_notes", "").strip(),
                    request.form.get("service_notes", "").strip(),
                    rating,
                    _now(),
                    log_id,
                ),
            )
            db.commit()
            flash("Dough log updated.", "success")
            return redirect(url_for("main.log_detail", log_id=log_id))

    return render_template(
        "recipe_form.html",
        page_title=f"Edit {record['title']}",
        form_kind="log",
        formula=formula,
        record=record,
        templates=templates,
        flour_library=_flour_library(),
    )


@bp.post("/logs/<int:log_id>/delete")
def log_delete(log_id: int):
    row = _log(log_id)
    photos = get_db().execute("SELECT filename FROM photos WHERE dough_log_id = ?", (log_id,)).fetchall()
    get_db().execute("DELETE FROM dough_logs WHERE id = ?", (log_id,))
    get_db().commit()
    for photo in photos:
        Path(current_app.config["UPLOAD_FOLDER"], photo["filename"]).unlink(missing_ok=True)
    flash(f"Deleted {row['title']} for {pretty_date(row['service_date'])}.", "success")
    return redirect(url_for("main.history"))


@bp.route("/history")
def history():
    query = "SELECT * FROM dough_logs WHERE 1 = 1"
    values: list[Any] = []
    if request.args.get("from"):
        query += " AND service_date >= ?"
        values.append(request.args["from"])
    if request.args.get("to"):
        query += " AND service_date <= ?"
        values.append(request.args["to"])
    if request.args.get("rating", type=int):
        query += " AND overall_rating = ?"
        values.append(request.args.get("rating", type=int))
    if request.args.get("q"):
        query += " AND (title LIKE ? OR mix_notes LIKE ? OR service_notes LIKE ?)"
        term = f"%{request.args['q'].strip()}%"
        values.extend((term, term, term))
    query += " ORDER BY service_date DESC, id DESC"
    rows = [_history_view(row) for row in get_db().execute(query, values).fetchall()]
    return render_template("history.html", logs=rows)


@bp.get("/compare")
def compare_logs():
    if "left" in request.args or "right" in request.args:
        log_ids = [
            request.args.get("left", type=int),
            request.args.get("right", type=int),
        ]
    else:
        log_ids = request.args.getlist("log", type=int)

    if len(log_ids) != 2 or not all(log_ids) or log_ids[0] == log_ids[1]:
        flash("Choose exactly two different dough logs to compare.", "error")
        return redirect(url_for("main.history"))

    left = _log_view(_log(log_ids[0]))
    right = _log_view(_log(log_ids[1]))
    sections = _comparison_sections(left, right)
    difference_count = sum(
        row["changed"] for section in sections for row in section["rows"]
    )
    matching_count = sum(
        not row["changed"] for section in sections for row in section["rows"]
    )
    differences_only = request.args.get("differences") == "1"
    visible_sections = []
    for section in sections:
        rows = (
            [row for row in section["rows"] if row["changed"]]
            if differences_only
            else section["rows"]
        )
        if rows:
            visible_sections.append({"title": section["title"], "rows": rows})

    choices = get_db().execute(
        "SELECT id, title, service_date FROM dough_logs "
        "ORDER BY service_date DESC, id DESC"
    ).fetchall()
    return render_template(
        "compare.html",
        left=left,
        right=right,
        choices=choices,
        sections=visible_sections,
        difference_count=difference_count,
        matching_count=matching_count,
        differences_only=differences_only,
    )


@bp.post("/logs/<int:log_id>/photos")
def photo_upload(log_id: int):
    _log(log_id)
    files = request.files.getlist("photos")
    caption = request.form.get("caption", "").strip()
    if not files or all(not item.filename for item in files):
        flash("Choose at least one pizza photo.", "error")
        return redirect(url_for("main.log_detail", log_id=log_id))
    saved = 0
    for upload in files[:10]:
        if not upload.filename:
            continue
        original_name = secure_filename(upload.filename) or "pizza-photo"
        try:
            image = Image.open(BytesIO(upload.read()))
            image = ImageOps.exif_transpose(image)
            image.thumbnail((1800, 1800), Image.Resampling.LANCZOS)
            if image.mode not in {"RGB", "L"}:
                background = Image.new("RGB", image.size, "white")
                if "A" in image.getbands():
                    background.paste(image, mask=image.getchannel("A"))
                else:
                    background.paste(image)
                image = background
            elif image.mode == "L":
                image = image.convert("RGB")
            filename = f"{uuid.uuid4().hex}.jpg"
            image.save(Path(current_app.config["UPLOAD_FOLDER"], filename), "JPEG", quality=88, optimize=True)
        except (UnidentifiedImageError, OSError, ValueError):
            flash(f"{original_name} was not recognized as a supported image.", "error")
            continue
        get_db().execute(
            "INSERT INTO photos (dough_log_id, filename, original_name, caption, created_at) VALUES (?, ?, ?, ?, ?)",
            (log_id, filename, original_name, caption, _now()),
        )
        saved += 1
    get_db().commit()
    if saved:
        flash(f"Added {saved} pizza photo{'s' if saved != 1 else ''}.", "success")
    return redirect(url_for("main.log_detail", log_id=log_id))


@bp.get("/photos/<path:filename>")
def photo_file(filename: str):
    row = get_db().execute("SELECT id FROM photos WHERE filename = ?", (filename,)).fetchone()
    if row is None:
        abort(404)
    return send_from_directory(current_app.config["UPLOAD_FOLDER"], filename)


@bp.post("/photos/<int:photo_id>/delete")
def photo_delete(photo_id: int):
    row = get_db().execute("SELECT * FROM photos WHERE id = ?", (photo_id,)).fetchone()
    if row is None:
        abort(404)
    get_db().execute("DELETE FROM photos WHERE id = ?", (photo_id,))
    get_db().commit()
    Path(current_app.config["UPLOAD_FOLDER"], row["filename"]).unlink(missing_ok=True)
    flash("Photo deleted.", "success")
    return redirect(url_for("main.log_detail", log_id=row["dough_log_id"]))


@bp.get("/export.json")
def export_json():
    db = get_db()
    payload = {
        "exported_at": _now(),
        "flours": [dict(row) for row in db.execute("SELECT * FROM flour_library ORDER BY id")],
        "templates": [dict(row) for row in db.execute("SELECT * FROM recipe_templates ORDER BY id")],
        "dough_logs": [dict(row) for row in db.execute("SELECT * FROM dough_logs ORDER BY id")],
        "photos": [dict(row) for row in db.execute("SELECT * FROM photos ORDER BY id")],
    }
    response = jsonify(payload)
    response.headers["Content-Disposition"] = f"attachment; filename=dough-log-{date.today().isoformat()}.json"
    return response


@bp.app_errorhandler(413)
def upload_too_large(_error):
    flash("That upload is larger than the configured photo limit.", "error")
    return redirect(request.referrer or url_for("main.index")), 413
