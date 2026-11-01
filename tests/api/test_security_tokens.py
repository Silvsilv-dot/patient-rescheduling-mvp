"""Tokens and rate limiting (no database needed)."""

from __future__ import annotations

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import jwt
import pytest

from api.errors import ApiError
from api.security import MIN_KEY_LENGTH, RateLimiter, create_token, decode_token
from config import settings as cfg

NOW = datetime(2026, 11, 2, 8, 0, tzinfo=ZoneInfo("America/New_York"))
KEY = "unit-test-key-" + "k" * 64
URL = "postgresql://u:p@127.0.0.1:1/db"


@pytest.fixture(autouse=True)
def env(monkeypatch):
    monkeypatch.setattr(cfg, "_load_dotenv_if_available", lambda: None)
    monkeypatch.setenv("NEON_DATABASE_URL", URL)
    monkeypatch.setenv("TOKEN_SIGNING_KEY", KEY)
    cfg.reset_settings_cache()
    yield
    cfg.reset_settings_cache()


def forge(payload: dict, key: str = KEY, algorithm: str = "HS256") -> str:
    return jwt.encode(payload, key, algorithm=algorithm)


def good_payload(**overrides) -> dict:
    payload = {
        "sub": "APT-1",
        "role": "Patient",
        "pid": "PAT-1",
        "iat": int(NOW.timestamp()),
        "exp": int((NOW + timedelta(minutes=15)).timestamp()),
    }
    payload.update(overrides)
    return payload


def test_round_trip():
    claims = decode_token(create_token("Patient", "APT-1", NOW, patient_id="PAT-1"), NOW)
    assert (claims.role, claims.subject, claims.patient_id) == ("Patient", "APT-1", "PAT-1")
    assert claims.expires_at - claims.issued_at == timedelta(minutes=15)


def test_staff_style_token_has_no_patient_id():
    claims = decode_token(create_token("FrontDesk", "FrontDesk", NOW), NOW)
    assert claims.role == "FrontDesk" and claims.patient_id is None


def test_token_expiry_is_exact():
    token = create_token("Patient", "APT-1", NOW, patient_id="PAT-1")
    assert decode_token(token, NOW + timedelta(minutes=14, seconds=59))
    for late in (timedelta(minutes=15), timedelta(hours=2)):
        with pytest.raises(ApiError) as info:
            decode_token(token, NOW + late)
        assert info.value.status_code == 401


def test_each_token_is_unique():
    assert create_token("Patient", "A", NOW) != create_token("Patient", "A", NOW)


def test_tampered_token_is_refused():
    token = create_token("Patient", "APT-1", NOW, patient_id="PAT-1")
    head, body, sig = token.split(".")
    flipped = body[:-2] + ("AA" if not body.endswith("AA") else "BB")
    for bad in (
        f"{head}.{flipped}.{sig}",
        f"{head}.{body}.{sig[:-3]}abc",
        token + "x",
        "garbage",
        "",
    ):
        with pytest.raises(ApiError):
            decode_token(bad, NOW)


def test_token_signed_with_another_key_is_refused():
    with pytest.raises(ApiError):
        decode_token(forge(good_payload(), key="a-different-key-" + "z" * 32), NOW)


def test_unsigned_and_other_algorithm_tokens_are_refused():
    unsigned = jwt.encode(good_payload(), key="", algorithm="none")
    with pytest.raises(ApiError):
        decode_token(unsigned, NOW)
    with pytest.raises(ApiError):
        decode_token(forge(good_payload(), algorithm="HS512"), NOW)


@pytest.mark.parametrize("missing", ["exp", "iat", "sub", "role"])
def test_required_claims(missing):
    payload = good_payload()
    del payload[missing]
    with pytest.raises(ApiError):
        decode_token(forge(payload), NOW)


@pytest.mark.parametrize("role", ["Admin", "", "patient", 5])
def test_unknown_role_is_refused(role):
    with pytest.raises(ApiError):
        decode_token(forge(good_payload(role=role)), NOW)


def test_missing_or_short_signing_key_is_a_generic_config_error(monkeypatch):
    for value in (None, "", "short"):
        if value is None:
            monkeypatch.delenv("TOKEN_SIGNING_KEY", raising=False)
        else:
            monkeypatch.setenv("TOKEN_SIGNING_KEY", value)
        cfg.reset_settings_cache()
        with pytest.raises(cfg.ConfigError) as info:
            create_token("Patient", "A", NOW)
        assert str(info.value) == cfg.CONFIG_ERROR_MESSAGE
    assert MIN_KEY_LENGTH >= 32


# ---------------------------------------------------------------- rate limiter
def test_rate_limiter_blocks_after_the_limit_and_recovers():
    limiter = RateLimiter(max_calls=3, window_seconds=10)
    assert [limiter.allow("a", at=t) for t in (0, 1, 2)] == [True, True, True]
    assert limiter.allow("a", at=3) is False
    assert limiter.allow("a", at=9.9) is False
    assert limiter.allow("a", at=10) is True  # the first call has left the window


def test_rate_limiter_keys_are_independent_and_reset_works():
    limiter = RateLimiter(max_calls=1, window_seconds=60)
    assert limiter.allow("a", at=0) and limiter.allow("b", at=0)
    assert not limiter.allow("a", at=1)
    limiter.reset()
    assert limiter.allow("a", at=2)
