from __future__ import annotations

import hashlib
import hmac
import os
import secrets
import time
from datetime import timedelta
from pathlib import Path
from urllib.parse import urlsplit

from flask import Flask, abort, redirect, render_template, request, session, url_for
from werkzeug.middleware.proxy_fix import ProxyFix


DEFAULT_SECRET_KEY = "development-only-change-me"
LOGIN_CONTENT_SECURITY_POLICY = (
    "default-src 'self'; base-uri 'none'; object-src 'none'; "
    "frame-ancestors 'none'; form-action 'self'; img-src 'self'; "
    "font-src 'self'; style-src 'self'"
)
APPLICATION_CONTENT_SECURITY_POLICY = (
    "default-src 'self'; base-uri 'self'; object-src 'none'; "
    "frame-ancestors 'none'; form-action 'self'; img-src 'self' data:; "
    "font-src 'self'; style-src 'self' 'unsafe-inline'; "
    "script-src 'self' 'unsafe-inline'; connect-src 'self'"
)


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


def _positive_environment_integer(name: str, default: int) -> int:
    raw_value = os.environ.get(name)
    if raw_value is None:
        return default
    try:
        value = int(raw_value)
    except ValueError as error:
        raise RuntimeError(f"{name} must be a positive whole number.") from error
    if value < 1:
        raise RuntimeError(f"{name} must be a positive whole number.")
    return value


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
        TRUST_PROXY_HEADERS=_environment_flag("TRUST_PROXY_HEADERS", True),
        AUTH_FAILURE_WINDOW_MINUTES=_positive_environment_integer(
            "AUTH_FAILURE_WINDOW_MINUTES", 15
        ),
        AUTH_MAX_FAILURES_PER_IP=_positive_environment_integer(
            "AUTH_MAX_FAILURES_PER_IP", 5
        ),
        AUTH_MAX_FAILURES_TOTAL=_positive_environment_integer(
            "AUTH_MAX_FAILURES_TOTAL", 20
        ),
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
    if app.config["AUTH_MAX_FAILURES_TOTAL"] < app.config["AUTH_MAX_FAILURES_PER_IP"]:
        raise RuntimeError(
            "AUTH_MAX_FAILURES_TOTAL must be at least AUTH_MAX_FAILURES_PER_IP."
        )

    if app.config["TRUST_PROXY_HEADERS"]:
        app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1)

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
        username_matches = hmac.compare_digest(username, auth_username)
        password_matches = hmac.compare_digest(password, auth_password)
        return username_matches & password_matches

    def ensure_csrf_token() -> str:
        token = session.get("csrf_token")
        if not token:
            token = secrets.token_urlsafe(32)
            session["csrf_token"] = token
        return token

    def csrf_token_matches() -> bool:
        saved_token = session.get("csrf_token")
        supplied_token = request.form.get("csrf_token", "")
        return bool(
            saved_token
            and supplied_token
            and hmac.compare_digest(saved_token, supplied_token)
        )

    def remember_login() -> None:
        session.clear()
        session.permanent = True
        session["auth_fingerprint"] = authentication_fingerprint()
        ensure_csrf_token()

    def login_client_key() -> str:
        secret_key = app.secret_key
        if isinstance(secret_key, str):
            secret_key = secret_key.encode("utf-8")
        remote_address = (request.remote_addr or "unknown").encode("utf-8")
        return hmac.new(
            secret_key,
            b"dough-log-login-client\0" + remote_address,
            hashlib.sha256,
        ).hexdigest()

    def login_retry_after(client_key: str, now: int) -> int:
        window_seconds = int(app.config["AUTH_FAILURE_WINDOW_MINUTES"]) * 60
        cutoff = now - window_seconds
        connection = db.get_db()
        connection.execute("DELETE FROM login_failures WHERE attempted_at < ?", (cutoff,))
        client_failures = connection.execute(
            "SELECT COUNT(*) AS failure_count, MIN(attempted_at) AS oldest_attempt "
            "FROM login_failures WHERE client_key = ? AND attempted_at >= ?",
            (client_key, cutoff),
        ).fetchone()
        total_failures = connection.execute(
            "SELECT COUNT(*) AS failure_count, MIN(attempted_at) AS oldest_attempt "
            "FROM login_failures WHERE attempted_at >= ?",
            (cutoff,),
        ).fetchone()
        connection.commit()

        retry_after = 0
        limits = (
            (client_failures, int(app.config["AUTH_MAX_FAILURES_PER_IP"])),
            (total_failures, int(app.config["AUTH_MAX_FAILURES_TOTAL"])),
        )
        for row, maximum_failures in limits:
            if row["failure_count"] >= maximum_failures and row["oldest_attempt"] is not None:
                remaining = window_seconds - (now - int(row["oldest_attempt"]))
                retry_after = max(retry_after, remaining, 1)
        return retry_after

    def record_login_failure(client_key: str, now: int) -> None:
        connection = db.get_db()
        connection.execute(
            "INSERT INTO login_failures (client_key, attempted_at) VALUES (?, ?)",
            (client_key, now),
        )
        connection.commit()
        app.logger.warning("Rejected sign-in attempt from client %s", client_key[:12])

    def clear_client_login_failures(client_key: str) -> None:
        connection = db.get_db()
        connection.execute("DELETE FROM login_failures WHERE client_key = ?", (client_key,))
        connection.commit()

    def safe_destination(value: str | None) -> str:
        if value:
            parsed = urlsplit(value)
            if not parsed.scheme and not parsed.netloc and value.startswith("/") and not value.startswith("//"):
                return value
        return url_for("main.index")

    def login_response(
        destination: str,
        error: str | None = None,
        status: int = 200,
        retry_after: int | None = None,
    ):
        response = app.make_response(
            (
                render_template(
                    "login.html",
                    destination=destination,
                    error=error,
                    login_csrf_token=ensure_csrf_token(),
                    login_page=True,
                ),
                status,
            )
        )
        response.headers["Cache-Control"] = "no-store"
        response.headers["Content-Security-Policy"] = LOGIN_CONTENT_SECURITY_POLICY
        if retry_after:
            response.headers["Retry-After"] = str(retry_after)
        return response

    @app.before_request
    def require_authentication():
        if not auth_username or request.endpoint in {"main.health", "login", "static"}:
            return None

        if session_is_authenticated():
            ensure_csrf_token()
            if request.method == "POST" and not csrf_token_matches():
                abort(400, description="Invalid form submission.")
            return None

        authorization = request.authorization
        is_basic = bool(authorization and (authorization.type or "").lower() == "basic")
        destination = request.full_path[:-1] if request.full_path.endswith("?") else request.full_path
        if (
            is_basic
            and authorization
            and authorization.username is not None
            and authorization.password is not None
        ):
            client_key = login_client_key()
            now = int(time.time())
            retry_after = login_retry_after(client_key, now)
            if retry_after:
                return login_response(
                    safe_destination(destination),
                    "Sign-in is temporarily unavailable. Please try again later.",
                    429,
                    retry_after,
                )
            if credentials_match(authorization.username, authorization.password):
                clear_client_login_failures(client_key)
                remember_login()
                if request.method == "POST":
                    return redirect(safe_destination(destination))
                return None
            record_login_failure(client_key, now)

        session.clear()
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

        if request.method == "POST":
            if not csrf_token_matches():
                return login_response(
                    destination,
                    "The sign-in form expired. Reload the page and try again.",
                    400,
                )

            client_key = login_client_key()
            now = int(time.time())
            retry_after = login_retry_after(client_key, now)
            if retry_after:
                return login_response(
                    destination,
                    "Sign-in is temporarily unavailable. Please try again later.",
                    429,
                    retry_after,
                )

            username = request.form.get("username", "")
            password = request.form.get("password", "")
            if credentials_match(username, password):
                clear_client_login_failures(client_key)
                remember_login()
                return redirect(destination)
            record_login_failure(client_key, now)
            return login_response(
                destination,
                "The username or password is incorrect.",
                401,
            )

        return login_response(destination)

    @app.post("/logout")
    def logout():
        session.clear()
        return redirect(url_for("login"))

    @app.after_request
    def add_security_headers(response):
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("X-Frame-Options", "DENY")
        response.headers.setdefault("Referrer-Policy", "no-referrer")
        response.headers.setdefault("Cross-Origin-Opener-Policy", "same-origin")
        response.headers.setdefault("Cross-Origin-Resource-Policy", "same-origin")
        response.headers.setdefault(
            "Permissions-Policy", "camera=(), geolocation=(), microphone=()"
        )
        if request.endpoint == "login":
            content_security_policy = LOGIN_CONTENT_SECURITY_POLICY
        else:
            content_security_policy = APPLICATION_CONTENT_SECURITY_POLICY
        response.headers.setdefault("Content-Security-Policy", content_security_policy)
        if request.is_secure:
            response.headers.setdefault(
                "Strict-Transport-Security", "max-age=31536000; includeSubDomains"
            )
        if auth_username and request.endpoint not in {"main.health", "static"}:
            response.headers["Cache-Control"] = "no-store"
        return response

    app.jinja_env.globals["csrf_token"] = ensure_csrf_token

    with app.app_context():
        db.init_db()

    return app
