from __future__ import annotations

import hmac
import os
from pathlib import Path

from flask import Flask, Response, request


def _resolved_path(app: Flask, value: str, fallback: str) -> Path:
    configured = Path(value or fallback)
    if configured.is_absolute():
        return configured
    return Path(app.root_path).parent / configured


def create_app(test_config: dict | None = None) -> Flask:
    app = Flask(__name__, instance_relative_config=True)
    app.config.from_mapping(
        SECRET_KEY=os.environ.get("SECRET_KEY", "development-only-change-me"),
        DATABASE=os.environ.get("DATABASE_PATH", "instance/dough-log.sqlite3"),
        UPLOAD_FOLDER=os.environ.get("UPLOAD_FOLDER", "instance/uploads"),
        MAX_CONTENT_LENGTH=int(os.environ.get("MAX_UPLOAD_MB", "15")) * 1024 * 1024,
        BASIC_AUTH_USERNAME=os.environ.get("BASIC_AUTH_USERNAME", ""),
        BASIC_AUTH_PASSWORD=os.environ.get("BASIC_AUTH_PASSWORD", ""),
    )
    if test_config:
        app.config.update(test_config)

    auth_username = str(app.config.get("BASIC_AUTH_USERNAME") or "").strip()
    auth_password = str(app.config.get("BASIC_AUTH_PASSWORD") or "")
    if bool(auth_username) != bool(auth_password):
        raise RuntimeError(
            "BASIC_AUTH_USERNAME and BASIC_AUTH_PASSWORD must either both be set or both be empty."
        )
    app.config["BASIC_AUTH_USERNAME"] = auth_username
    app.config["BASIC_AUTH_PASSWORD"] = auth_password

    database_path = _resolved_path(app, str(app.config["DATABASE"]), "instance/dough-log.sqlite3")
    upload_path = _resolved_path(app, str(app.config["UPLOAD_FOLDER"]), "instance/uploads")
    database_path.parent.mkdir(parents=True, exist_ok=True)
    upload_path.mkdir(parents=True, exist_ok=True)
    app.config["DATABASE"] = str(database_path)
    app.config["UPLOAD_FOLDER"] = str(upload_path)

    from . import db
    from .routes import bp

    db.init_app(app)
    app.register_blueprint(bp)

    @app.before_request
    def require_basic_auth():
        if request.endpoint == "main.health" or not auth_username:
            return None

        authorization = request.authorization
        is_basic = bool(authorization and (authorization.type or "").lower() == "basic")
        username_matches = bool(
            is_basic
            and authorization
            and authorization.username is not None
            and hmac.compare_digest(authorization.username, auth_username)
        )
        password_matches = bool(
            is_basic
            and authorization
            and authorization.password is not None
            and hmac.compare_digest(authorization.password, auth_password)
        )
        if username_matches and password_matches:
            return None

        return Response(
            "Authentication required.",
            401,
            {
                "WWW-Authenticate": 'Basic realm="Pizzeria Mari Dough Log", charset="UTF-8"',
                "Cache-Control": "no-store",
            },
        )

    with app.app_context():
        db.init_db()

    return app
