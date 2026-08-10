import pytest
from pydantic import ValidationError

from app.config import Settings, get_settings


def test_settings_load():
    settings = get_settings()
    assert settings.database_url is not None
    assert settings.secret_key is not None
    assert settings.algorithm == "HS256"
    assert settings.access_token_expire_minutes == 30


def _production_settings(**overrides):
    values = {
        "DATABASE_URL": "postgresql+asyncpg://user:password@db/scholargraph",
        "SECRET_KEY": "a-secure-production-secret-with-32-characters",
        "ENVIRONMENT": "production",
        "ALLOWED_ORIGINS": '["https://scholargraph.example.com"]',
        "ADMIN_EMAILS": "admin@example.com",
    }
    values.update(overrides)
    return Settings(_env_file=None, **values)


def test_production_settings_accept_explicit_security_configuration():
    settings = _production_settings()

    assert settings.allowed_origins == ["https://scholargraph.example.com"]
    assert settings.admin_emails == ["admin@example.com"]
    assert settings.scheduler_enabled is False


@pytest.mark.parametrize(
    ("override", "message"),
    [
        ({"SECRET_KEY": "replace-me"}, "SECRET_KEY"),
        ({"ALLOWED_ORIGINS": "*"}, "Wildcard ALLOWED_ORIGINS"),
        ({"ALLOWED_ORIGINS": ""}, "ALLOWED_ORIGINS"),
        ({"ADMIN_EMAILS": ""}, "ADMIN_EMAILS"),
    ],
)
def test_production_settings_reject_unsafe_values(override, message):
    with pytest.raises(ValidationError, match=message):
        _production_settings(**override)


def test_list_settings_are_normalised_and_deduplicated():
    settings = Settings(
        _env_file=None,
        DATABASE_URL="postgresql+asyncpg://user:password@db/scholargraph",
        SECRET_KEY="development-secret",
        ADMIN_EMAILS="Admin@Example.com, admin@example.com",
        GRAPH_REFRESH_TOPICS="cs.AI, cs.LG",
    )

    assert settings.admin_emails == ["admin@example.com"]
    assert settings.graph_refresh_topics == ["cs.AI", "cs.LG"]
