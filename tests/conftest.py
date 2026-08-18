from __future__ import annotations

import pytest

from doughlog import create_app


@pytest.fixture()
def app(tmp_path):
    app = create_app(
        {
            "TESTING": True,
            "SECRET_KEY": "test-secret",
            "DATABASE": str(tmp_path / "test.sqlite3"),
            "UPLOAD_FOLDER": str(tmp_path / "uploads"),
            "BASIC_AUTH_USERNAME": "",
            "BASIC_AUTH_PASSWORD": "",
        }
    )
    yield app


@pytest.fixture()
def client(app):
    return app.test_client()
