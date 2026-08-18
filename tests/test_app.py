from __future__ import annotations

import io
import json
import secrets

import pytest
from PIL import Image

from doughlog import create_app
from doughlog.db import get_db


def formula_fields(**overrides):
    values = {
        "ball_count": "20",
        "ball_weight_g": "700",
        "hydration_pct": "68",
        "salt_pct": "3",
        "yeast_type": "IDY",
        "yeast_pct": "0.07",
        "residue_pct": "1",
        "flours_json": json.dumps(
            [
                {"name": "00 Normal", "pct": 50, "protein_pct": 12.5, "ash_pct": 0.55},
                {"name": "00 Reinforced", "pct": 50, "protein_pct": 13.0, "ash_pct": 0.60},
            ]
        ),
        "ingredients_json": json.dumps([{"name": "Canola Oil", "pct": 3.4}]),
        "preferments_json": json.dumps(
            [
                {
                    "name": "Poolish",
                    "type": "Poolish",
                    "amount_pct": 55,
                    "water_pct": 50,
                    "leavening_type": "IDY",
                    "leavening_pct": 0.01,
                    "flours": [
                        {"name": "High Mountain", "pct": 75, "protein_pct": 13.5, "ash_pct": 0.65},
                        {"name": "Einkorn", "pct": 25, "protein_pct": 14.0, "ash_pct": 1.80},
                    ],
                    "notes": "12 hours at room temperature",
                },
                {
                    "name": "Levain",
                    "type": "Levain",
                    "amount_pct": 5,
                    "water_pct": 50,
                    "leavening_type": "Mature Starter",
                    "leavening_pct": 10,
                    "flours": [
                        {"name": "Whole Wheat", "pct": 100, "protein_pct": 14.2, "ash_pct": 1.60}
                    ],
                    "notes": "Use at peak",
                },
            ]
        ),
    }
    values.update(overrides)
    return values


def create_template(client):
    response = client.post(
        "/templates/new",
        data={**formula_fields(), "name": "Standard Service Dough", "description": "Base 700 g dough"},
        follow_redirects=False,
    )
    assert response.status_code == 302
    return int(response.headers["Location"].split("/")[-2])


def create_log(client, template_id):
    response = client.post(
        "/logs/new",
        data={
            **formula_fields(),
            "template_id": str(template_id),
            "title": "Friday Service Dough",
            "service_date": "2026-08-21",
            "mix_datetime": "2026-08-19T09:30",
            "room_temp_f": "72",
            "humidity_pct": "58",
            "flour_temp_f": "70",
            "water_temp_f": "62",
            "desired_final_dough_temp_f": "76",
            "final_dough_temp_f": "77",
            "mix_stages_json": json.dumps(
                [
                    {"speed": "Low", "minutes": 5, "notes": "Combine"},
                    {"speed": "Medium", "minutes": 4, "notes": "Develop"},
                ]
            ),
            "mix_notes": "Slightly faster cleanup than usual.",
            "service_notes": "Opened easily and baked crisp.",
            "overall_rating": "5",
        },
        follow_redirects=False,
    )
    assert response.status_code == 302
    return int(response.headers["Location"].rstrip("/").split("/")[-1])


def test_basic_auth_protects_app_but_not_health_check(tmp_path):
    auth_username = f"test-user-{secrets.token_hex(4)}"
    auth_password = secrets.token_urlsafe(32)
    invalid_username = f"invalid-user-{secrets.token_hex(4)}"
    invalid_password = secrets.token_urlsafe(32)
    app = create_app(
        {
            "TESTING": True,
            "SECRET_KEY": secrets.token_hex(32),
            "DATABASE": str(tmp_path / "auth.sqlite3"),
            "UPLOAD_FOLDER": str(tmp_path / "uploads"),
            "BASIC_AUTH_USERNAME": auth_username,
            "BASIC_AUTH_PASSWORD": auth_password,
        }
    )
    client = app.test_client()

    unauthorized = client.get("/")
    assert unauthorized.status_code == 401
    assert unauthorized.headers["WWW-Authenticate"] == (
        'Basic realm="Pizzeria Mari Dough Log", charset="UTF-8"'
    )
    assert unauthorized.headers["Cache-Control"] == "no-store"
    assert client.get("/", auth=(auth_username, invalid_password)).status_code == 401
    assert client.get("/", auth=(invalid_username, auth_password)).status_code == 401

    authorized = client.get("/", auth=(auth_username, auth_password))
    assert authorized.status_code == 200
    assert b"Service Days" in authorized.data
    assert client.get("/static/style.css").status_code == 401
    assert client.get("/health").get_json() == {"status": "ok"}


def test_basic_auth_rejects_incomplete_configuration(tmp_path):
    with pytest.raises(RuntimeError, match="must either both be set or both be empty"):
        create_app(
            {
                "TESTING": True,
                "DATABASE": str(tmp_path / "incomplete.sqlite3"),
                "UPLOAD_FOLDER": str(tmp_path / "uploads"),
                "BASIC_AUTH_USERNAME": f"test-user-{secrets.token_hex(4)}",
                "BASIC_AUTH_PASSWORD": "",
            }
        )


def test_new_calculator_has_requested_defaults_and_removed_controls(client):
    response = client.get("/templates/new")
    assert response.status_code == 200
    assert b'name="ball_weight_g"' in response.data
    assert b'value="700"' in response.data
    assert b'name="yeast_pct"' in response.data
    assert b'value="0.07"' in response.data
    for removed in (
        b'thickness_factor', b'diameter_in', b'width_in', b'length_in', b'pan_depth_in',
        b'salt_name', b'oil_density', b'leftover_ball_count', b'actual_ball_count',
        b'performance_tags_json', b'id="tag-picker"',
    ):
        assert removed not in response.data


def test_selecting_recipe_can_load_complete_formula_into_new_log(client):
    template_id = create_template(client)
    response = client.get("/logs/new")
    assert response.status_code == 200
    assert b'data-load-recipe' in response.data
    assert b'recipeTemplates:' in response.data
    assert b'Standard Service Dough' in response.data
    for formula_value in (
        b'"ball_count": 20', b'"ball_weight_g": 700.0', b'"hydration_pct": 68.0',
        b'"salt_pct": 3.0', b'"yeast_type": "IDY"', b'"yeast_pct": 0.07',
        b'"residue_pct": 1.0', b'"flours"', b'"ingredients"', b'"preferments"',
        b'High Mountain', b'Einkorn', b'Levain',
    ):
        assert formula_value in response.data

    selected = client.get(f"/logs/new?template={template_id}")
    assert selected.status_code == 200
    assert b'value="Standard Service Dough"' in selected.data
    assert f'value="{template_id}" selected'.encode() in selected.data

    script = client.get("/static/app.js")
    for behavior in (
        b"function loadRecipe(recipe)", b"select[data-load-recipe]",
        b"form.elements.title.value = recipe.name", b"renderFlours()",
        b"renderIngredients()", b"renderPreferments()",
    ):
        assert behavior in script.data


def test_home_is_a_simple_utility_dashboard(client):
    response = client.get("/")
    assert response.status_code == 200
    assert b"Service Days" in response.data
    for removed in (
        b"Production journal", b"Service Logs", b"Average Rating", b"Awaiting Review",
        b"Recipe Templates", b"Calculate a service-day mix", b"New Dough Log",
        b"<footer", b"Export records",
    ):
        assert removed not in response.data


def test_page_and_form_header_helper_text_is_removed(client):
    pages_and_removed_text = {
        "/flours": (b"Reusable ingredients", b"Save each flour once"),
        "/history": (b"Production archive", b"Find the service days that worked"),
        "/templates": (b"Reusable formulas", b"Editing a template never changes"),
        "/templates/new": (
            b"Reusable formula", b"Name the formula", b"Set the number of dough balls",
            b"Hydration, yeast, salt", b"Each preferment's total weight",
        ),
        "/logs/new": (
            b"Service-day snapshot", b"One log represents", b"Record the conditions",
            b"Add each change in speed", b"Complete this after",
        ),
    }
    for path, removed_text in pages_and_removed_text.items():
        response = client.get(path)
        assert response.status_code == 200
        for text in removed_text:
            assert text not in response.data


def test_flour_library_is_reusable_in_formula_pick_lists(client, app):
    response = client.post(
        "/flours",
        data={"name": "High Mountain", "protein_pct": "13.5", "ash_pct": "0.65"},
        follow_redirects=True,
    )
    assert response.status_code == 200
    assert b"High Mountain" in response.data

    recipe = client.get("/templates/new")
    assert recipe.status_code == 200
    assert b'"name": "High Mountain"' in recipe.data
    assert b'"protein_pct": 13.5' in recipe.data
    script = client.get("/static/app.js")
    assert b"Choose saved flour" in script.data
    assert b"selectedFlour.protein_pct" in script.data
    assert b"selectedFlour.ash_pct" in script.data

    with app.app_context():
        flour_id = get_db().execute(
            "SELECT id FROM flour_library WHERE name = 'High Mountain'"
        ).fetchone()[0]
    client.post(
        f"/flours/{flour_id}/edit",
        data={"name": "High Mountain", "protein_pct": "13.7", "ash_pct": "0.67"},
    )
    with app.app_context():
        flour = get_db().execute("SELECT * FROM flour_library WHERE id = ?", (flour_id,)).fetchone()
        assert flour["protein_pct"] == 13.7
        assert flour["ash_pct"] == 0.67

    exported = client.get("/export.json").get_json()
    assert exported["flours"][0]["name"] == "High Mountain"


def test_brand_assets_are_packaged_and_served(client):
    page = client.get("/")
    assert page.status_code == 200
    assert b"assets/pm-horizontal-logo-cream.png" in page.data

    expected_assets = {
        "compagnon-medium.otf": "font/otf",
        "semplicita-modern-book.otf": "font/otf",
        "pm-horizontal-logo-cream.png": "image/png",
    }
    for filename, mimetype in expected_assets.items():
        response = client.get(f"/static/assets/{filename}")
        assert response.status_code == 200
        assert response.mimetype == mimetype


def test_template_daily_log_and_snapshot_history(client, app):
    template_id = create_template(client)
    log_id = create_log(client, template_id)

    detail = client.get(f"/logs/{log_id}")
    assert detail.status_code == 200
    for content in (
        b"Friday Service Dough", b"Poolish", b"High Mountain", b"Einkorn", b"Levain",
        b"Total Mix Time", b"9 minutes", b"Overall Protein", b"Pounds",
        b"Slightly faster cleanup",
    ):
        assert content in detail.data
    assert "★★★★★".encode() in detail.data
    assert b"Actual Dough Balls" not in detail.data
    assert b"Performance Tags" not in detail.data

    edit = client.get(f"/logs/{log_id}/edit")
    assert b'actual_ball_count' not in edit.data
    assert b'performance_tags_json' not in edit.data
    assert b'review-notes-grid' in edit.data

    client.post(
        f"/templates/{template_id}/edit",
        data={**formula_fields(hydration_pct="71"), "name": "Standard Service Dough v2", "description": "Updated formula"},
    )
    with app.app_context():
        template_formula = json.loads(
            get_db().execute("SELECT formula_json FROM recipe_templates WHERE id = ?", (template_id,)).fetchone()[0]
        )
        log_formula = json.loads(
            get_db().execute("SELECT formula_json FROM dough_logs WHERE id = ?", (log_id,)).fetchone()[0]
        )
    assert template_formula["hydration_pct"] == 71
    assert log_formula["hydration_pct"] == 68


def test_uploads_and_serves_pizza_photo(client, app):
    template_id = create_template(client)
    log_id = create_log(client, template_id)
    buffer = io.BytesIO()
    Image.new("RGB", (2400, 1800), (210, 90, 50)).save(buffer, "PNG")
    buffer.seek(0)

    response = client.post(
        f"/logs/{log_id}/photos",
        data={"photos": (buffer, "finished-pizza.png"), "caption": "Plain pie"},
        content_type="multipart/form-data",
        follow_redirects=True,
    )
    assert response.status_code == 200
    assert b"Added 1 pizza photo" in response.data
    assert b"Plain pie" in response.data

    with app.app_context():
        photo = get_db().execute("SELECT * FROM photos WHERE dough_log_id = ?", (log_id,)).fetchone()
    served = client.get(f"/photos/{photo['filename']}")
    assert served.status_code == 200
    assert served.mimetype == "image/jpeg"


def test_history_filters_by_rating_and_shows_recipe_metrics(client, app):
    template_id = create_template(client)
    log_id = create_log(client, template_id)
    response = client.get("/history?rating=5")
    assert response.status_code == 200
    assert b"Friday Service Dough" in response.data
    for heading in (b"Hydration", b"Flours Used", b"IDY", b"Protein", b"Ash"):
        assert heading in response.data
    for value in (b"68.00%", b"0.070%", b"00 Normal", b"00 Reinforced", b"High Mountain", b"Einkorn", b"Whole Wheat"):
        assert value in response.data
    with app.app_context():
        calculated = json.loads(
            get_db().execute("SELECT calculated_json FROM dough_logs WHERE id = ?", (log_id,)).fetchone()[0]
        )
    assert f"{calculated['overall_protein_pct']:.2f}%".encode() in response.data
    assert f"{calculated['overall_ash_pct']:.2f}%".encode() in response.data
    response = client.get("/history?rating=2")
    assert b"Friday Service Dough" not in response.data
