"""System behavior that must hold even with no database (BR-21, BR-22)."""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from api import schemas
from api.main import MAX_BODY_BYTES, create_app
from config import settings as cfg
from config.settings import DEMO_BANNER_TEXT
from rules import business_rules as br

KEY = "unit-test-key-" + "k" * 32


@pytest.fixture
def no_db_env(monkeypatch):
    monkeypatch.setattr(cfg, "_load_dotenv_if_available", lambda: None)
    monkeypatch.delenv("NEON_DATABASE_URL", raising=False)
    monkeypatch.setenv("TOKEN_SIGNING_KEY", KEY)
    cfg.reset_settings_cache()
    yield
    cfg.reset_settings_cache()


@pytest.fixture
def client(no_db_env):
    return TestClient(create_app(static_dir=None))


# ------------------------------------------------------------ T01-2 / BR-22
def test_health_without_configuration_is_503_and_generic(client):
    response = client.get("/api/v1/health")
    assert response.status_code == 503
    assert response.json() == {"status": "degraded", "database": "unavailable"}


def test_routes_that_need_the_database_return_a_generic_503(client):
    response = client.get("/api/v1/privacy-notice")
    assert response.status_code == 503
    body = response.json()
    assert body["error"]["code"] == "unavailable"
    assert not br.contains_internal_details(response.text)
    assert "NEON" not in response.text and "postgres" not in response.text.lower()


def test_sign_in_without_configuration_is_a_generic_503(client):
    response = client.post("/api/v1/auth/patient", json={"appt_id": "A", "access_code": "1"})
    assert response.status_code == 503 and not br.contains_internal_details(response.text)


def test_missing_signing_key_is_a_generic_503(client, monkeypatch):
    monkeypatch.delenv("TOKEN_SIGNING_KEY", raising=False)
    cfg.reset_settings_cache()
    response = client.get("/api/v1/wait-times", headers={"Authorization": "Bearer abc.def.ghi"})
    assert response.status_code == 503 and response.json()["error"]["code"] == "unavailable"


# ------------------------------------------------------------ headers and errors
def test_security_headers_on_every_kind_of_response(client):
    for path in ("/", "/api/v1/health", "/api/v1/privacy-notice", "/no/such/page"):
        response = client.get(path)
        assert br.missing_security_headers(response.headers) == [], path


def test_api_responses_are_never_cached(client):
    for path in ("/api/v1/health", "/api/v1/privacy-notice", "/api/v1/nope"):
        assert client.get(path).headers["cache-control"] == "no-store", path


def test_unknown_route_and_wrong_method_use_the_standard_format(client):
    missing = client.get("/api/v1/nothing-here")
    assert missing.status_code == 404
    assert missing.json() == br.error_body("not_found", br.MESSAGES["not_found"])
    wrong = client.delete("/api/v1/health")
    assert wrong.status_code == 405 and set(wrong.json()) == {"error"}


def test_validation_errors_never_echo_what_was_sent(client):
    response = client.post(
        "/api/v1/auth/patient", json={"appt_id": "A" * 100, "access_code": "hunter2-secret"}
    )
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "invalid_request"
    assert "hunter2" not in response.text and "AAAA" not in response.text
    extra = client.post("/api/v1/auth/patient", json={"appt_id": "A", "access_code": "1", "x": 1})
    assert extra.status_code == 422


def test_oversized_request_is_refused(client):
    response = client.post(
        "/api/v1/auth/patient",
        content=b"x" * (MAX_BODY_BYTES + 1),
        headers={"content-type": "application/json"},
    )
    assert response.status_code == 413
    assert response.json()["error"]["code"] == "payload_too_large"
    assert br.missing_security_headers(response.headers) == []


def test_unexpected_errors_return_a_safe_500():
    app = create_app(static_dir=None)

    @app.get("/boom")
    def boom():  # pragma: no cover - raises on purpose
        raise RuntimeError('File "/app/secret.py", password=postgresql://u:pw@host/db')

    response = TestClient(app, raise_server_exceptions=False).get("/boom")
    assert response.status_code == 500
    assert response.json()["error"]["code"] == "internal"
    assert not br.contains_internal_details(response.text)
    assert "secret.py" not in response.text and "pw@host" not in response.text
    assert br.missing_security_headers(response.headers) == []


def test_interactive_docs_work_and_have_no_strict_csp(client):
    assert client.get("/docs").status_code == 200
    assert "content-security-policy" not in client.get("/docs").headers
    assert "x-content-type-options" in client.get("/docs").headers
    assert client.get("/openapi.json").status_code == 200


def test_root_info_mentions_the_demo(client):
    body = client.get("/").json()
    assert DEMO_BANNER_TEXT == body["demo"]


# ------------------------------------------------------------ static patient page
def test_patient_page_is_served_with_security_headers(no_db_env, tmp_path: Path):
    (tmp_path / "index.html").write_text("<html><body>hello</body></html>", encoding="utf-8")
    app = TestClient(create_app(static_dir=tmp_path))
    page = app.get("/patient/")
    assert page.status_code == 200 and "hello" in page.text
    assert br.missing_security_headers(page.headers) == []
    assert app.get("/", follow_redirects=False).headers["location"] == "/patient/"


# ------------------------------------------------------------ BR-03 / BR-21
def model_fields(model) -> set[str]:
    return set(model.model_fields)


def test_response_models_match_the_whitelists():
    allowed = br.ALLOWED_RESPONSE_FIELDS
    assert model_fields(schemas.AppointmentOut) == allowed["patient_appointment"]
    assert model_fields(schemas.SlotOut) == allowed["patient_slot"]
    assert model_fields(schemas.WaitEstimateOut) == allowed["patient_wait_estimate"]


def test_no_api_field_has_a_prohibited_name(no_db_env):
    spec = create_app(static_dir=None).openapi()
    names = set()
    for schema in spec["components"]["schemas"].values():
        names.update(schema.get("properties", {}))
    assert names, "no schema properties found"
    assert [n for n in names if br.has_prohibited_field_name(n)] == []


def test_requests_reject_unknown_fields():
    for model in (
        schemas.SignInRequest,
        schemas.ConsentRequest,
        schemas.CancelRequest,
        schemas.RescheduleRequest,
    ):
        assert model.model_config.get("extra") == "forbid"


def test_patients_cannot_record_staff_consent():
    with pytest.raises(ValueError):
        schemas.ConsentRequest(consent_type="C3", status="Granted", notice_version="1.0")
