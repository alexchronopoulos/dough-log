from __future__ import annotations

import io
import json
import re
import secrets
import sqlite3
from datetime import timedelta
from pathlib import Path

import pytest
from PIL import Image

from doughlog import create_app
from doughlog.db import get_db


def csrf_from(response) -> str:
    match = re.search(rb'name="csrf_token" value="([^"]+)"', response.data)
    assert match is not None
    return match.group(1).decode()


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


def test_login_session_remembers_credentials_for_30_days(tmp_path):
    auth_username = f"test-user-{secrets.token_hex(4)}"
    auth_password = secrets.token_urlsafe(32)
    app = create_app(
        {
            "TESTING": True,
            "SECRET_KEY": secrets.token_hex(32),
            "DATABASE": str(tmp_path / "auth.sqlite3"),
            "UPLOAD_FOLDER": str(tmp_path / "uploads"),
            "BASIC_AUTH_USERNAME": auth_username,
            "BASIC_AUTH_PASSWORD": auth_password,
            "SESSION_COOKIE_SECURE": False,
        }
    )
    client = app.test_client()

    unauthorized = client.get("/")
    assert unauthorized.status_code == 302
    assert unauthorized.headers["Location"].startswith("/login?next=")

    login_page = client.get(unauthorized.headers["Location"])
    assert login_page.status_code == 200
    assert login_page.headers["Cache-Control"] == "no-store"
    assert b"Sign In for 30 Days" in login_page.data
    assert b'name="username" autocomplete="username"' in login_page.data
    assert b'name="password" type="password" autocomplete="current-password"' in login_page.data
    assert b"New Log" not in login_page.data
    login_csrf = csrf_from(login_page)

    rejected = client.post(
        "/login",
        data={
            "username": auth_username,
            "password": secrets.token_urlsafe(32),
            "next": "/",
            "csrf_token": login_csrf,
        },
    )
    assert rejected.status_code == 401
    assert "WWW-Authenticate" not in rejected.headers
    assert b"The username or password is incorrect." in rejected.data

    signed_in = client.post(
        "/login",
        data={
            "username": auth_username,
            "password": auth_password,
            "next": "/",
            "csrf_token": login_csrf,
        },
    )
    assert signed_in.status_code == 302
    assert signed_in.headers["Location"] == "/"
    session_cookie = signed_in.headers["Set-Cookie"]
    assert "pizzeria_mari_dough_log_session=" in session_cookie
    assert "HttpOnly" in session_cookie
    assert "SameSite=Lax" in session_cookie
    assert auth_username not in session_cookie
    assert auth_password not in session_cookie
    assert app.permanent_session_lifetime == timedelta(days=30)
    assert app.config["SESSION_REFRESH_EACH_REQUEST"] is False

    authorized = client.get("/")
    assert authorized.status_code == 200
    assert b"Service Days" in authorized.data
    assert b"Log Out" in authorized.data
    assert "Set-Cookie" not in authorized.headers

    with client.session_transaction() as saved_session:
        logout_csrf = saved_session["csrf_token"]
    logged_out = client.post("/logout", data={"csrf_token": logout_csrf})
    assert logged_out.status_code == 302
    assert client.get("/").status_code == 302

    basic_compatible = client.get("/", auth=(auth_username, auth_password))
    assert basic_compatible.status_code == 200
    assert "Set-Cookie" in basic_compatible.headers

    assert client.get("/static/style.css").status_code == 200
    assert client.get("/health").get_json() == {"status": "ok"}


def test_login_cookie_can_be_marked_secure_and_rejects_external_redirects(tmp_path):
    auth_username = f"test-user-{secrets.token_hex(4)}"
    auth_password = secrets.token_urlsafe(32)
    app = create_app(
        {
            "TESTING": True,
            "SECRET_KEY": secrets.token_hex(32),
            "DATABASE": str(tmp_path / "secure-auth.sqlite3"),
            "UPLOAD_FOLDER": str(tmp_path / "uploads"),
            "BASIC_AUTH_USERNAME": auth_username,
            "BASIC_AUTH_PASSWORD": auth_password,
            "SESSION_COOKIE_SECURE": True,
        }
    )
    client = app.test_client()
    login_page = client.get(
        "/login", base_url="https://doughlog.pizzeriamari.com"
    )
    response = client.post(
        "/login",
        base_url="https://doughlog.pizzeriamari.com",
        data={
            "username": auth_username,
            "password": auth_password,
            "next": "https://example.com/not-allowed",
            "csrf_token": csrf_from(login_page),
        },
    )
    assert response.status_code == 302
    assert response.headers["Location"] == "/"
    assert "Secure" in response.headers["Set-Cookie"]


def test_changing_credentials_invalidates_an_existing_login_session(tmp_path):
    auth_username = f"test-user-{secrets.token_hex(4)}"
    auth_password = secrets.token_urlsafe(32)
    secret_key = secrets.token_hex(32)
    config = {
        "TESTING": True,
        "SECRET_KEY": secret_key,
        "DATABASE": str(tmp_path / "rotated-auth.sqlite3"),
        "UPLOAD_FOLDER": str(tmp_path / "uploads"),
        "BASIC_AUTH_USERNAME": auth_username,
        "BASIC_AUTH_PASSWORD": auth_password,
        "SESSION_COOKIE_SECURE": False,
    }
    original_app = create_app(config)
    original_client = original_app.test_client()
    login_page = original_client.get("/login")
    original_client.post(
        "/login",
        data={
            "username": auth_username,
            "password": auth_password,
            "next": "/",
            "csrf_token": csrf_from(login_page),
        },
    )
    saved_cookie = original_client.get_cookie("pizzeria_mari_dough_log_session")
    assert saved_cookie is not None

    rotated_app = create_app({**config, "BASIC_AUTH_PASSWORD": secrets.token_urlsafe(32)})
    rotated_client = rotated_app.test_client()
    rotated_client.set_cookie("pizzeria_mari_dough_log_session", saved_cookie.value)
    response = rotated_client.get("/")
    assert response.status_code == 302
    assert response.headers["Location"].startswith("/login?next=")


def test_login_throttle_persists_across_app_workers_and_recovers(tmp_path):
    auth_username = f"test-user-{secrets.token_hex(4)}"
    auth_password = secrets.token_urlsafe(32)
    config = {
        "TESTING": True,
        "SECRET_KEY": secrets.token_hex(32),
        "DATABASE": str(tmp_path / "throttled-auth.sqlite3"),
        "UPLOAD_FOLDER": str(tmp_path / "uploads"),
        "BASIC_AUTH_USERNAME": auth_username,
        "BASIC_AUTH_PASSWORD": auth_password,
        "SESSION_COOKIE_SECURE": False,
        "TRUST_PROXY_HEADERS": False,
        "AUTH_FAILURE_WINDOW_MINUTES": 15,
        "AUTH_MAX_FAILURES_PER_IP": 2,
        "AUTH_MAX_FAILURES_TOTAL": 10,
    }
    first_app = create_app(config)
    first_client = first_app.test_client()
    login_page = first_client.get("/login")
    csrf_token = csrf_from(login_page)
    for username, password in (
        (f"unknown-{secrets.token_hex(4)}", secrets.token_urlsafe(32)),
        (auth_username, secrets.token_urlsafe(32)),
    ):
        response = first_client.post(
            "/login",
            data={
                "username": username,
                "password": password,
                "next": "/",
                "csrf_token": csrf_token,
            },
        )
        assert response.status_code == 401
        assert b"The username or password is incorrect." in response.data

    with first_app.app_context():
        failures = get_db().execute(
            "SELECT client_key, attempted_at FROM login_failures"
        ).fetchall()
        assert len(failures) == 2
        assert all(row["client_key"] != "127.0.0.1" for row in failures)

    second_app = create_app(config)
    second_client = second_app.test_client()
    second_login_page = second_client.get("/login")
    blocked = second_client.post(
        "/login",
        data={
            "username": auth_username,
            "password": auth_password,
            "next": "/",
            "csrf_token": csrf_from(second_login_page),
        },
    )
    assert blocked.status_code == 429
    assert 1 <= int(blocked.headers["Retry-After"]) <= 900
    assert b"Sign-in is temporarily unavailable." in blocked.data

    with second_app.app_context():
        get_db().execute("UPDATE login_failures SET attempted_at = attempted_at - 901")
        get_db().commit()

    recovered = second_client.post(
        "/login",
        data={
            "username": auth_username,
            "password": auth_password,
            "next": "/",
            "csrf_token": csrf_from(blocked),
        },
    )
    assert recovered.status_code == 302
    with second_app.app_context():
        assert get_db().execute(
            "SELECT COUNT(*) AS count FROM login_failures"
        ).fetchone()["count"] == 0


def test_basic_auth_attempts_are_throttled_too(tmp_path):
    auth_username = f"test-user-{secrets.token_hex(4)}"
    auth_password = secrets.token_urlsafe(32)
    app = create_app(
        {
            "TESTING": True,
            "SECRET_KEY": secrets.token_hex(32),
            "DATABASE": str(tmp_path / "basic-throttle.sqlite3"),
            "UPLOAD_FOLDER": str(tmp_path / "uploads"),
            "BASIC_AUTH_USERNAME": auth_username,
            "BASIC_AUTH_PASSWORD": auth_password,
            "SESSION_COOKIE_SECURE": False,
            "TRUST_PROXY_HEADERS": False,
            "AUTH_MAX_FAILURES_PER_IP": 2,
            "AUTH_MAX_FAILURES_TOTAL": 10,
        }
    )
    client = app.test_client()
    for _attempt in range(2):
        response = client.get(
            "/", auth=(auth_username, secrets.token_urlsafe(32))
        )
        assert response.status_code == 302

    blocked = client.get("/", auth=(auth_username, auth_password))
    assert blocked.status_code == 429
    assert "Retry-After" in blocked.headers
    assert "unsafe-inline" not in blocked.headers["Content-Security-Policy"]


def test_total_login_limit_uses_nginx_forwarded_client_addresses(tmp_path):
    auth_username = f"test-user-{secrets.token_hex(4)}"
    auth_password = secrets.token_urlsafe(32)
    app = create_app(
        {
            "TESTING": True,
            "SECRET_KEY": secrets.token_hex(32),
            "DATABASE": str(tmp_path / "forwarded-throttle.sqlite3"),
            "UPLOAD_FOLDER": str(tmp_path / "uploads"),
            "BASIC_AUTH_USERNAME": auth_username,
            "BASIC_AUTH_PASSWORD": auth_password,
            "SESSION_COOKIE_SECURE": False,
            "TRUST_PROXY_HEADERS": True,
            "AUTH_MAX_FAILURES_PER_IP": 2,
            "AUTH_MAX_FAILURES_TOTAL": 2,
        }
    )
    client = app.test_client()
    login_page = client.get("/login")
    token = csrf_from(login_page)
    for address in ("203.0.113.10", "203.0.113.11"):
        response = client.post(
            "/login",
            headers={"X-Forwarded-For": address, "X-Forwarded-Proto": "https"},
            data={
                "username": auth_username,
                "password": secrets.token_urlsafe(32),
                "next": "/",
                "csrf_token": token,
            },
        )
        assert response.status_code == 401

    blocked = client.post(
        "/login",
        headers={"X-Forwarded-For": "203.0.113.12", "X-Forwarded-Proto": "https"},
        data={
            "username": auth_username,
            "password": auth_password,
            "next": "/",
            "csrf_token": token,
        },
    )
    assert blocked.status_code == 429
    with app.app_context():
        keys = {
            row["client_key"]
            for row in get_db().execute("SELECT client_key FROM login_failures")
        }
        assert len(keys) == 2


def test_login_csrf_and_security_headers(tmp_path):
    auth_username = f"test-user-{secrets.token_hex(4)}"
    auth_password = secrets.token_urlsafe(32)
    app = create_app(
        {
            "TESTING": True,
            "SECRET_KEY": secrets.token_hex(32),
            "DATABASE": str(tmp_path / "csrf-auth.sqlite3"),
            "UPLOAD_FOLDER": str(tmp_path / "uploads"),
            "BASIC_AUTH_USERNAME": auth_username,
            "BASIC_AUTH_PASSWORD": auth_password,
            "SESSION_COOKIE_SECURE": True,
            "TRUST_PROXY_HEADERS": True,
        }
    )
    client = app.test_client()
    base_url = "https://doughlog.pizzeriamari.com"
    login_page = client.get("/login", base_url=base_url)
    expected_headers = {
        "X-Content-Type-Options": "nosniff",
        "X-Frame-Options": "DENY",
        "Referrer-Policy": "no-referrer",
        "Cross-Origin-Opener-Policy": "same-origin",
        "Cross-Origin-Resource-Policy": "same-origin",
        "Strict-Transport-Security": "max-age=31536000; includeSubDomains",
    }
    for header, value in expected_headers.items():
        assert login_page.headers[header] == value
    assert "unsafe-inline" not in login_page.headers["Content-Security-Policy"]
    assert "frame-ancestors 'none'" in login_page.headers["Content-Security-Policy"]
    assert login_page.headers["Cache-Control"] == "no-store"

    missing_csrf = client.post(
        "/login",
        base_url=base_url,
        data={"username": auth_username, "password": auth_password, "next": "/"},
    )
    assert missing_csrf.status_code == 400
    assert b"The sign-in form expired." in missing_csrf.data

    signed_in = client.post(
        "/login",
        base_url=base_url,
        data={
            "username": auth_username,
            "password": auth_password,
            "next": "/",
            "csrf_token": csrf_from(login_page),
        },
    )
    assert signed_in.status_code == 302
    assert all(
        flag in signed_in.headers["Set-Cookie"]
        for flag in ("Secure", "HttpOnly", "SameSite=Lax")
    )

    rejected_change = client.post(
        "/flours",
        base_url=base_url,
        data={"mill": "Example", "name": "Example Flour"},
    )
    assert rejected_change.status_code == 400

    home = client.get("/", base_url=base_url)
    logout_csrf = csrf_from(home)
    assert client.post("/logout", base_url=base_url).status_code == 400
    assert client.post(
        "/logout",
        base_url=base_url,
        data={"csrf_token": logout_csrf},
    ).status_code == 302


def test_every_post_form_includes_a_csrf_token():
    template_directory = Path(__file__).parents[1] / "doughlog" / "templates"
    post_forms = []
    for template in template_directory.glob("*.html"):
        contents = template.read_text(encoding="utf-8")
        post_forms.extend(
            (template.name, form)
            for form in re.findall(
                r'<form\b[^>]*method="post"[^>]*>.*?</form>',
                contents,
                flags=re.DOTALL,
            )
        )
    assert len(post_forms) >= 12
    for template_name, form in post_forms:
        assert 'name="csrf_token"' in form, template_name


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


def test_authentication_rejects_the_development_secret_key(tmp_path):
    with pytest.raises(RuntimeError, match="Set SECRET_KEY"):
        create_app(
            {
                "TESTING": True,
                "DATABASE": str(tmp_path / "unsafe-secret.sqlite3"),
                "UPLOAD_FOLDER": str(tmp_path / "uploads"),
                "BASIC_AUTH_USERNAME": f"test-user-{secrets.token_hex(4)}",
                "BASIC_AUTH_PASSWORD": secrets.token_urlsafe(32),
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
    assert b'rel="icon" type="image/png"' in page.data
    assert b"assets/pm-icon-black.png" in page.data

    expected_assets = {
        "compagnon-medium.otf": "font/otf",
        "semplicita-modern-book.otf": "font/otf",
        "pm-horizontal-logo-cream.png": "image/png",
        "pm-icon-black.png": "image/png",
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
