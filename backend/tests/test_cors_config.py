# SPDX-License-Identifier: AGPL-3.0-only
"""Tests for CORS origin parsing in config.py.

Covers:
  - default origins (including the literal "null" for file:// use)
  - whitespace trimming
  - empty entry removal
  - explicit "null" origin parsing
  - preflight behavior for null origin (200) and unlisted origin (400)
"""

from __future__ import annotations

import importlib

from fastapi.testclient import TestClient

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _load_settings_with(monkeypatch, cors_value: str | None):
    """Reload app.config with a patched CORS_ORIGINS env var."""
    import app.config as config_module

    if cors_value is None:
        monkeypatch.delenv("CORS_ORIGINS", raising=False)
    else:
        monkeypatch.setenv("CORS_ORIGINS", cors_value)

    return importlib.reload(config_module).settings


def _make_client(settings):
    """Build a fresh FastAPI app with the given settings' CORS origins."""
    # Patch the settings object used by main.py at import time
    import app.config as config_module
    import app.main as main_module

    config_module.settings = settings

    return importlib.reload(main_module).app


# ---------------------------------------------------------------------------
# Config parsing tests
# ---------------------------------------------------------------------------


def test_default_cors_origins_include_dev_ports(monkeypatch):
    settings = _load_settings_with(monkeypatch, None)
    assert "http://localhost:5173" in settings.cors_origins
    assert "http://localhost:3000" in settings.cors_origins
    assert "null" in settings.cors_origins  # file:// support


def test_whitespace_trimming(monkeypatch):
    settings = _load_settings_with(monkeypatch, "http://a.com, http://b.com ,http://c.com")
    assert settings.cors_origins == ["http://a.com", "http://b.com", "http://c.com"]


def test_empty_entries_removed(monkeypatch):
    settings = _load_settings_with(monkeypatch, "http://a.com,,http://b.com,")
    assert settings.cors_origins == ["http://a.com", "http://b.com"]


def test_explicit_null_origin(monkeypatch):
    settings = _load_settings_with(monkeypatch, "null,http://localhost:5173")
    assert settings.cors_origins == ["null", "http://localhost:5173"]


# ---------------------------------------------------------------------------
# Preflight behavior tests
# ---------------------------------------------------------------------------


def _preflight(client, origin: str) -> int:
    response = client.options(
        "/api/sol/turn",
        headers={
            "Origin": origin,
            "Access-Control-Request-Method": "POST",
            "Access-Control-Request-Headers": "content-type",
        },
    )
    return response


def test_preflight_null_origin_returns_allow_origin(monkeypatch):
    """Origin: null (from file:// pages) should be allowed when listed."""
    settings = _load_settings_with(monkeypatch, "null,http://localhost:5173")
    client = TestClient(_make_client(settings))

    response = _preflight(client, "null")

    assert response.status_code == 200
    assert response.headers.get("access-control-allow-origin") == "null"


def test_preflight_unlisted_origin_rejected(monkeypatch):
    """An origin not in the list should be rejected with 400."""
    settings = _load_settings_with(monkeypatch, "http://localhost:5173")
    client = TestClient(_make_client(settings))

    response = _preflight(client, "http://evil.com")

    assert response.status_code == 400


def test_preflight_listed_origin_returns_allow_origin(monkeypatch):
    """A listed origin should receive the expected CORS headers."""
    settings = _load_settings_with(monkeypatch, "http://localhost:63342")
    client = TestClient(_make_client(settings))

    response = _preflight(client, "http://localhost:63342")

    assert response.status_code == 200
    assert response.headers.get("access-control-allow-origin") == "http://localhost:63342"
