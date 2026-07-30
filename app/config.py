from __future__ import annotations

import json
from functools import lru_cache

from pydantic import Field, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    database_url: str = Field(validation_alias="DATABASE_URL")
    secret_key: str = Field(validation_alias="SECRET_KEY")

    algorithm: str = Field(
        default="HS256",
        validation_alias="ALGORITHM",
    )

    access_token_expire_minutes: int = Field(
        default=30,
        validation_alias="ACCESS_TOKEN_EXPIRE_MINUTES",
    )

    environment: str = Field(
        default="development",
        validation_alias="ENVIRONMENT",
    )

    allowed_origins: list[str] = Field(
        default_factory=lambda: ["*"],
        validation_alias="ALLOWED_ORIGINS",
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

    @field_validator("allowed_origins", mode="before")
    @classmethod
    def parse_allowed_origins(cls, value: object) -> object:
        if value in (None, ""):
            return ["*"]

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
    def apply_environment_defaults(self):
        if self.environment.lower() != "production":
            self.allowed_origins = ["*"]
        elif not self.allowed_origins:
            raise ValueError(
                "ALLOWED_ORIGINS must be set in production"
            )

        return self

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )


@lru_cache
def get_settings() -> Settings:
    return Settings()