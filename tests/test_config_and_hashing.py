"""Tests for settings, hashing and database error handling (no live database)."""

from __future__ import annotations

import logging

import pytest

from config import settings as cfg
from db import connection
from db.hashing import hash_access_code, verify_access_code

SECRET_URL = "postgresql://demo_user:s3cretpass@127.0.0.1:1/demo_db"


@pytest.fixture(autouse=True)
def clean_state(monkeypatch):
    """Isolate each test from the real environment and any cached state."""
    for name in (
        "NEON_DATABASE_URL",
        "TOKEN_SIGNING_KEY",
        "DEMO_STAFF_PIN_FRONTDESK",
        "DEMO_STAFF_PIN_MANAGER",
        "DB_POOL_MIN",
        "DB_POOL_MAX",
        "DB_POOL_TIMEOUT_SECONDS",
    ):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(cfg, "_load_dotenv_if_available", lambda: None)
    cfg.reset_settings_cache()
    connection.close_pool()
    yield
    connection.close_pool()
    cfg.reset_settings_cache()


# ------------------------------------------------------------------ settings
def test_missing_database_url_raises_generic_error():
    with pytest.raises(cfg.ConfigError) as info:
        cfg.get_settings()
    assert str(info.value) == cfg.CONFIG_ERROR_MESSAGE


@pytest.mark.parametrize("bad", ["", "mysql://user:s3cretpass@host/db", "not a url"])
def test_invalid_database_url_never_echoed(monkeypatch, bad):
    monkeypatch.setenv("NEON_DATABASE_URL", bad)
    with pytest.raises(cfg.ConfigError) as info:
        cfg.get_settings()
    assert str(info.value) == cfg.CONFIG_ERROR_MESSAGE


def test_valid_settings_and_defaults(monkeypatch):
    monkeypatch.setenv("NEON_DATABASE_URL", SECRET_URL)
    settings = cfg.get_settings()
    assert settings.database_url == SECRET_URL
    assert (settings.db_pool_min, settings.db_pool_max) == (1, 5)
    assert settings.token_signing_key is None


def test_require_optional_setting(monkeypatch):
    monkeypatch.setenv("NEON_DATABASE_URL", SECRET_URL)
    monkeypatch.setenv("TOKEN_SIGNING_KEY", "k")
    settings = cfg.get_settings()
    assert settings.require("token_signing_key") == "k"
    with pytest.raises(cfg.ConfigError):
        settings.require("demo_staff_pin_manager")


def test_bad_pool_setting_is_a_config_error(monkeypatch):
    monkeypatch.setenv("NEON_DATABASE_URL", SECRET_URL)
    monkeypatch.setenv("DB_POOL_MAX", "many")
    with pytest.raises(cfg.ConfigError):
        cfg.get_settings()


# ------------------------------------------------------------------ hashing
def test_hash_round_trip_and_wrong_code():
    stored = hash_access_code("123456", iterations=1000)
    assert stored.startswith("pbkdf2_sha256$1000$")
    assert "123456" not in stored
    assert verify_access_code("123456", stored)
    assert not verify_access_code("654321", stored)


def test_hashes_are_salted():
    assert hash_access_code("123456", 1000) != hash_access_code("123456", 1000)


@pytest.mark.parametrize("stored", ["", "garbage", "a$b$c", "md5$1$00$00", "pbkdf2_sha256$x$zz$00"])
def test_malformed_hash_is_rejected_not_raised(stored):
    assert verify_access_code("123456", stored) is False


# ------------------------------------------------------- database error handling
def test_health_check_without_configuration_is_degraded_not_raised():
    assert connection.health_check() == {"status": "degraded", "database": "unavailable"}


def test_unreachable_database_gives_generic_error_and_no_secrets(monkeypatch, caplog):
    monkeypatch.setenv("NEON_DATABASE_URL", SECRET_URL)
    monkeypatch.setenv("DB_POOL_TIMEOUT_SECONDS", "1")
    cfg.reset_settings_cache()
    with caplog.at_level(logging.DEBUG):
        with pytest.raises(connection.DatabaseUnavailableError) as info:
            with connection.get_conn():
                pass  # pragma: no cover
        assert connection.health_check()["database"] == "unavailable"
    assert str(info.value) == connection.GENERIC_DB_MESSAGE
    assert "s3cretpass" not in str(info.value)
    assert "s3cretpass" not in caplog.text


def test_pool_disables_server_side_prepared_statements(monkeypatch):
    """Neon's pooled endpoint (PgBouncer) needs prepare_threshold=None."""
    monkeypatch.setenv("NEON_DATABASE_URL", SECRET_URL)
    cfg.reset_settings_cache()
    pool = connection.get_pool()
    assert pool.kwargs["prepare_threshold"] is None


def test_seed_cli_reports_missing_configuration_without_traceback(capsys):
    from db import seed_synthetic

    assert seed_synthetic.main(["--credentials", "0"]) == 1
    out = capsys.readouterr().out
    assert cfg.CONFIG_ERROR_MESSAGE in out and "Traceback" not in out
