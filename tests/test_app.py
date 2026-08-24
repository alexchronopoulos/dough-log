from __future__ import annotations

import io
import json
import secrets
import sqlite3

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


def create_template(client, **overrides):
    data = {
        **formula_fields(),
        "name": "Standard Service Dough",
        "description": "Base 700 g dough",
    }
    data.update(overrides)
    response = client.post(
        "/templates/new",
        data=data,
        follow_redirects=False,
    )
    assert response.status_code == 302
    return int(response.headers["Location"].split("/")[-2])


def create_log(client, template_id, **overrides):
    data = {
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
    }
    data.update(overrides)
    response = client.post(
        "/logs/new",
        data=data,
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


def test_default_recipe_prefills_future_recipe_formulas(client, app):
    first_id = create_template(
        client,
        name="House Dough",
        description="Default starting formula",
        ball_weight_g="650",
        hydration_pct="71",
        salt_pct="2.8",
        yeast_pct="0.09",
    )

    response = client.post(
        f"/templates/{first_id}/default",
        follow_redirects=True,
    )
    assert response.status_code == 200
    assert b"House Dough is now the default for new recipes." in response.data
    assert b"Default Recipe" in response.data

    new_recipe = client.get("/templates/new")
    assert new_recipe.status_code == 200
    for copied_value in (
        b'"ball_weight_g": 650.0',
        b'"hydration_pct": 71.0',
        b'"salt_pct": 2.8',
        b'"yeast_pct": 0.09',
        b'High Mountain',
        b'Einkorn',
        b'Levain',
    ):
        assert copied_value in new_recipe.data
    assert b'value="House Dough"' not in new_recipe.data
    assert b"Default starting formula" not in new_recipe.data

    second_id = create_template(
        client,
        name="Alternate Dough",
        hydration_pct="66",
    )
    client.post(f"/templates/{second_id}/default")
    with app.app_context():
        defaults = get_db().execute(
            "SELECT id FROM recipe_templates WHERE is_default = 1"
        ).fetchall()
        assert [row["id"] for row in defaults] == [second_id]

    client.post(f"/templates/{second_id}/archive")
    with app.app_context():
        assert get_db().execute(
            "SELECT COUNT(*) FROM recipe_templates WHERE is_default = 1"
        ).fetchone()[0] == 0


def test_existing_database_is_migrated_for_default_recipes_and_flour_mills(tmp_path):
    database = tmp_path / "legacy.sqlite3"
    connection = sqlite3.connect(database)
    connection.execute(
        """
        CREATE TABLE recipe_templates (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            description TEXT NOT NULL DEFAULT '',
            formula_json TEXT NOT NULL,
            is_archived INTEGER NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )
        """
    )
    connection.execute(
        """
        CREATE TABLE flour_library (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL UNIQUE COLLATE NOCASE,
            protein_pct REAL NOT NULL,
            ash_pct REAL NOT NULL,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )
        """
    )
    connection.execute(
        """
        INSERT INTO flour_library
            (name, protein_pct, ash_pct, created_at, updated_at)
        VALUES ('Legacy Flour', 12.5, 0.6, '2026-08-18', '2026-08-18')
        """
    )
    connection.commit()
    connection.close()

    app = create_app(
        {
            "TESTING": True,
            "SECRET_KEY": secrets.token_hex(32),
            "DATABASE": str(database),
            "UPLOAD_FOLDER": str(tmp_path / "uploads"),
            "BASIC_AUTH_USERNAME": "",
            "BASIC_AUTH_PASSWORD": "",
        }
    )
    with app.app_context():
        columns = {
            row["name"]
            for row in get_db().execute(
                "PRAGMA table_info(recipe_templates)"
            ).fetchall()
        }
        assert "is_default" in columns
        flour_columns = {
            row["name"]
            for row in get_db().execute(
                "PRAGMA table_info(flour_library)"
            ).fetchall()
        }
        assert "mill" in flour_columns
        assert get_db().execute(
            "SELECT mill FROM flour_library WHERE name = 'Legacy Flour'"
        ).fetchone()["mill"] == ""


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
        data={
            "mill": "Central Milling",
            "name": "High Mountain",
            "protein_pct": "13.5",
            "ash_pct": "0.65",
        },
        follow_redirects=True,
    )
    assert response.status_code == 200
    assert b"High Mountain" in response.data

    recipe = client.get("/templates/new")
    assert recipe.status_code == 200
    assert b'"name": "High Mountain"' in recipe.data
    assert b'"mill": "Central Milling"' in recipe.data
    assert b'"protein_pct": 13.5' in recipe.data
    script = client.get("/static/app.js")
    assert b"Choose saved flour" in script.data
    assert b"selectedFlour.protein_pct" in script.data
    assert b"selectedFlour.ash_pct" in script.data
    assert b"flour.mill" in script.data

    with app.app_context():
        flour_id = get_db().execute(
            "SELECT id FROM flour_library WHERE name = 'High Mountain'"
        ).fetchone()[0]
    client.post(
        f"/flours/{flour_id}/edit",
        data={
            "mill": "Cairnspring Mills",
            "name": "High Mountain",
            "protein_pct": "13.7",
            "ash_pct": "0.67",
        },
    )
    with app.app_context():
        flour = get_db().execute("SELECT * FROM flour_library WHERE id = ?", (flour_id,)).fetchone()
        assert flour["mill"] == "Cairnspring Mills"
        assert flour["protein_pct"] == 13.7
        assert flour["ash_pct"] == 0.67

    exported = client.get("/export.json").get_json()
    assert exported["flours"][0]["name"] == "High Mountain"
    assert exported["flours"][0]["mill"] == "Cairnspring Mills"


def test_flour_blend_calculator_uses_library_flours(client, app):
    flour_data = [
        ("North Mill", "Flour A", "14", "0.6"),
        ("South Mill", "Flour B", "10", "1.0"),
        ("East Mill", "Flour C", "12", "1.4"),
        ("West Mill", "Flour D", "16", "0.4"),
    ]
    for mill, name, protein, ash in flour_data:
        response = client.post(
            "/flours",
            data={"mill": mill, "name": name, "protein_pct": protein, "ash_pct": ash},
        )
        assert response.status_code == 302

    library_page = client.get("/flours")
    assert b"Blend Calculator" in library_page.data
    with app.app_context():
        flour_ids = {
            row["name"]: row["id"]
            for row in get_db().execute("SELECT id, name FROM flour_library")
        }
    selected_flours = {
        "minimum_flour_pct": "1",
        "flour_1_mill": "North Mill",
        "flour_2_mill": "South Mill",
        "flour_3_mill": "East Mill",
        "flour_4_mill": "West Mill",
        "flour_1_id": str(flour_ids["Flour A"]),
        "flour_2_id": str(flour_ids["Flour B"]),
        "flour_3_id": str(flour_ids["Flour C"]),
        "flour_4_id": str(flour_ids["Flour D"]),
    }

    response = client.post(
        "/flours/blend",
        data={
            "target_protein_pct": "13",
            "target_ash_pct": "0.85",
            **selected_flours,
        },
    )
    assert response.status_code == 200
    for content in (
        b"Flour Blend Calculator",
        b"Exact Target",
        b"Calculated Four-Flour Blend",
        b"Minimum each flour",
        b"data-blend-output>25</output>%",
        b"data-live-total>100",
        b"13.000%",
        b"0.850%",
        b"Flour Distribution",
        b"North Mill",
        b"South Mill",
        b"East Mill",
        b"West Mill",
        b'data-mill-select',
        b'data-flour-select',
    ):
        assert content in response.data
    for removed in (b"Poolish Formula", b"Final Mix Flours", b"Poolish Flours"):
        assert removed not in response.data
    assert response.data.count(b'type="range"') == 4
    assert response.data.count(b'step="1"') >= 5
    assert response.data.count(b'type="checkbox" aria-label="Lock') == 4
    for behavior in (
        b"data-blend-editor",
        b"data-live-protein",
        b"data-live-ash",
        b"data-reset-blend",
        b"const redistribute",
        b"Manual Adjustment",
        b"data-blend-lock",
        b"is-required-balance",
        b"Locked at",
    ):
        assert behavior in response.data

    closest = client.post(
        "/flours/blend",
        data={
            "target_protein_pct": "20",
            "target_ash_pct": "3",
            **selected_flours,
            "minimum_flour_pct": "5",
        },
    )
    assert b"Closest Available Blend" in closest.data
    assert b"every flour at or above 5%" in closest.data

    zero_minimum = client.post(
        "/flours/blend",
        data={
            "target_protein_pct": "12",
            "target_ash_pct": "1.4",
            **selected_flours,
            "minimum_flour_pct": "0",
        },
    )
    assert zero_minimum.status_code == 200
    assert zero_minimum.data.count(b"data-blend-output>0</output>%") == 3
    assert b"data-blend-output>100</output>%" in zero_minimum.data

    fractional_minimum = client.post(
        "/flours/blend",
        data={
            "target_protein_pct": "13",
            "target_ash_pct": "0.85",
            **selected_flours,
            "minimum_flour_pct": "1.5",
        },
    )
    assert b"whole-number percentage" in fractional_minimum.data

    wrong_mill = client.post(
        "/flours/blend",
        data={
            "target_protein_pct": "13",
            "target_ash_pct": "0.85",
            **selected_flours,
            "flour_1_mill": "South Mill",
        },
    )
    assert b"Choose a flour from the selected mill" in wrong_mill.data

    duplicate = client.post(
        "/flours/blend",
        data={
            "target_protein_pct": "13",
            "target_ash_pct": "0.85",
            **selected_flours,
            "flour_4_mill": "North Mill",
            "flour_4_id": str(flour_ids["Flour A"]),
        },
    )
    assert b"Choose four different flours" in duplicate.data


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


def test_compare_two_dough_logs_side_by_side(client):
    template_id = create_template(client)
    left_id = create_log(client, template_id)
    right_id = create_log(
        client,
        template_id,
        title="Saturday Service Dough",
        service_date="2026-08-22",
        hydration_pct="71",
        ball_weight_g="650",
        room_temp_f="75",
        mix_notes="Needed more mixing.",
        service_notes="More open crumb.",
        overall_rating="4",
    )

    history = client.get("/history")
    assert history.status_code == 200
    assert b"Compare Selected" in history.data
    assert history.data.count(b'name="log"') == 2

    comparison = client.get(f"/compare?log={left_id}&log={right_id}")
    assert comparison.status_code == 200
    for content in (
        b"Compare Dough Logs",
        b"Friday Service Dough",
        b"Saturday Service Dough",
        b"68.00%",
        b"71.00%",
        b"Changed",
        b"Batch &amp; Formula",
        b"Calculated Ingredient Weights",
        b"Mix &amp; Conditions",
        b"Service Result",
        b"High Mountain",
    ):
        assert content in comparison.data

    differences_only = client.get(
        f"/compare?left={left_id}&right={right_id}&differences=1"
    )
    assert differences_only.status_code == 200
    assert b"Differences only" in differences_only.data
    assert b"Yeast type" not in differences_only.data

    invalid = client.get(f"/compare?log={left_id}", follow_redirects=True)
    assert invalid.status_code == 200
    assert b"Choose exactly two different dough logs to compare." in invalid.data
