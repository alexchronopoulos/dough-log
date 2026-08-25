from __future__ import annotations

import hashlib
import hmac
import os
from datetime import timedelta
from pathlib import Path
from urllib.parse import urlsplit

from flask import Flask, redirect, render_template, request, session, url_for


DEFAULT_SECRET_KEY = "development-only-change-me"


def _environment_flag(name: str, default: bool) -> bool:
    value = os.environ.get(name)
    if value is None:
        return default
    normalized = value.strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    raise RuntimeError(f"{name} must be true or false.")


def _resolved_path(app: Flask, value: str, fallback: str) -> Path:
    configured = Path(value or fallback)
    if configured.is_absolute():
        return configured
    return Path(app.root_path).parent / configured


def create_app(test_config: dict | None = None) -> Flask:
    app = Flask(__name__, instance_relative_config=True)
    app.config.from_mapping(
        SECRET_KEY=os.environ.get("SECRET_KEY", DEFAULT_SECRET_KEY),
        DATABASE=os.environ.get("DATABASE_PATH", "instance/dough-log.sqlite3"),
        UPLOAD_FOLDER=os.environ.get("UPLOAD_FOLDER", "instance/uploads"),
        MAX_CONTENT_LENGTH=int(os.environ.get("MAX_UPLOAD_MB", "15")) * 1024 * 1024,
        BASIC_AUTH_USERNAME=os.environ.get("BASIC_AUTH_USERNAME", ""),
        BASIC_AUTH_PASSWORD=os.environ.get("BASIC_AUTH_PASSWORD", ""),
        PERMANENT_SESSION_LIFETIME=timedelta(days=30),
        SESSION_COOKIE_HTTPONLY=True,
        SESSION_COOKIE_NAME="pizzeria_mari_dough_log_session",
        SESSION_COOKIE_SAMESITE="Lax",
        SESSION_COOKIE_SECURE=_environment_flag("SESSION_COOKIE_SECURE", False),
        SESSION_REFRESH_EACH_REQUEST=False,
    )
    if test_config:
        app.config.update(test_config)

    auth_username = str(app.config.get("BASIC_AUTH_USERNAME") or "").strip()
    auth_password = str(app.config.get("BASIC_AUTH_PASSWORD") or "")
    if bool(auth_username) != bool(auth_password):
        raise RuntimeError(
            "BASIC_AUTH_USERNAME and BASIC_AUTH_PASSWORD must either both be set or both be empty."
        )
    if auth_username and (not app.secret_key or app.secret_key == DEFAULT_SECRET_KEY):
        raise RuntimeError(
            "Set SECRET_KEY to a long random value before enabling authentication."
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

    def authentication_fingerprint() -> str:
        secret_key = app.secret_key
        if isinstance(secret_key, str):
            secret_key = secret_key.encode("utf-8")
        credentials = f"{auth_username}\0{auth_password}".encode("utf-8")
        return hmac.new(secret_key, credentials, hashlib.sha256).hexdigest()

    def session_is_authenticated() -> bool:
        saved_fingerprint = session.get("auth_fingerprint")
        return bool(
            saved_fingerprint
            and hmac.compare_digest(saved_fingerprint, authentication_fingerprint())
        )

    def credentials_match(username: str, password: str) -> bool:
        return hmac.compare_digest(username, auth_username) and hmac.compare_digest(
            password, auth_password
        )

    def remember_login() -> None:
        session.clear()
        session.permanent = True
        session["auth_fingerprint"] = authentication_fingerprint()

    def safe_destination(value: str | None) -> str:
        if value:
            parsed = urlsplit(value)
            if not parsed.scheme and not parsed.netloc and value.startswith("/") and not value.startswith("//"):
                return value
        return url_for("main.index")

    @app.before_request
    def require_authentication():
        if not auth_username or request.endpoint in {"main.health", "login", "static"}:
            return None

        if session_is_authenticated():
            return None

        authorization = request.authorization
        is_basic = bool(authorization and (authorization.type or "").lower() == "basic")
        if (
            is_basic
            and authorization
            and authorization.username is not None
            and authorization.password is not None
            and credentials_match(authorization.username, authorization.password)
        ):
            remember_login()
            return None

        session.clear()
        destination = request.full_path[:-1] if request.full_path.endswith("?") else request.full_path
        return redirect(url_for("login", next=destination))

    @app.route("/login", methods=("GET", "POST"))
    def login():
        if not auth_username:
            return redirect(url_for("main.index"))

        destination = safe_destination(
            request.form.get("next") if request.method == "POST" else request.args.get("next")
        )
        if session_is_authenticated():
            return redirect(destination)

        error = None
        if request.method == "POST":
            username = request.form.get("username", "")
            password = request.form.get("password", "")
            if credentials_match(username, password):
                remember_login()
                return redirect(destination)
            error = "The username or password is incorrect."

        response = app.make_response(
            render_template("login.html", destination=destination, error=error)
        )
        response.headers["Cache-Control"] = "no-store"
        if error:
            response.status_code = 401
        return response

    @app.post("/logout")
    def logout():
        session.clear()
        return redirect(url_for("login"))

    with app.app_context():
        db.init_db()

    return app
