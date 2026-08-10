from __future__ import annotations

import json
from functools import lru_cache
from typing import Literal

from pydantic import Field, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    database_url: str = Field(validation_alias="DATABASE_URL")
    secret_key: str = Field(validation_alias="SECRET_KEY")

    algorithm: Literal["HS256"] = Field(
        default="HS256",
        validation_alias="ALGORITHM",
    )

    access_token_expire_minutes: int = Field(
        default=30,
        gt=0,
        validation_alias="ACCESS_TOKEN_EXPIRE_MINUTES",
    )

    environment: Literal["development", "test", "production"] = Field(
        default="development",
        validation_alias="ENVIRONMENT",
    )

    allowed_origins: list[str] = Field(
        default_factory=lambda: [
            "http://localhost:8000",
            "http://127.0.0.1:8000",
        ],
        validation_alias="ALLOWED_ORIGINS",
    )

    admin_emails: list[str] = Field(
        default_factory=list,
        validation_alias="ADMIN_EMAILS",
    )

    scheduler_enabled: bool = Field(
        default=False,
        validation_alias="SCHEDULER_ENABLED",
    )

    graph_refresh_topics: list[str] = Field(
        default_factory=lambda: ["cs.AI"],
        validation_alias="GRAPH_REFRESH_TOPICS",
    )

    @field_validator("database_url", mode="before")
    @classmethod
    def normalise_database_url(cls, value: object) -> object:
        """
        Accept standard Render PostgreSQL URLs while preserving the asynchronous
        SQLAlchemy engine used by the application.
        """
        if not isinstance(value, str):
            return value

        url = value.strip()

        if url.startswith("postgres://"):
            return (
                "postgresql+asyncpg://"
                + url.removeprefix("postgres://")
            )

        if url.startswith("postgresql://"):
            return (
                "postgresql+asyncpg://"
                + url.removeprefix("postgresql://")
            )

        # Explicit postgresql+asyncpg:// and postgresql+psycopg:// URLs are
        # both supported when the corresponding driver is installed.
        return url

    @field_validator(
        "allowed_origins",
        "admin_emails",
        "graph_refresh_topics",
        mode="before",
    )
    @classmethod
    def parse_list_setting(cls, value: object) -> object:
        if value in (None, ""):
            return []

        if isinstance(value, str):
            value = value.strip()

            if value.startswith("["):
                return json.loads(value)

            return [
                origin.strip()
                for origin in value.split(",")
                if origin.strip()
            ]

        return value

    @model_validator(mode="after")
    def validate_security_settings(self):
        self.allowed_origins = list(dict.fromkeys(self.allowed_origins))
        self.admin_emails = list(
            dict.fromkeys(email.strip().lower() for email in self.admin_emails)
        )

        if "*" in self.allowed_origins:
            raise ValueError(
                "Wildcard ALLOWED_ORIGINS cannot be used with credentialed CORS"
            )

        if self.environment == "production":
            if len(self.secret_key) < 32 or "replace" in self.secret_key.lower():
                raise ValueError(
                    "SECRET_KEY must be a non-placeholder value of at least "
                    "32 characters in production"
                )

            if not self.allowed_origins:
                raise ValueError("ALLOWED_ORIGINS must be set in production")

            if not self.admin_emails:
                raise ValueError("ADMIN_EMAILS must be set in production")

        return self

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )


@lru_cache
def get_settings() -> Settings:
    return Settings()
