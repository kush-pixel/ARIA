"""Application configuration for ARIA backend.

Reads settings from a .env file using Pydantic v2 BaseSettings.
The .env file is resolved relative to the working directory, so
run uvicorn from the backend/ directory.
"""

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """ARIA runtime configuration loaded from environment / .env file."""

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # Database
    database_url: str = Field(..., description="asyncpg-compatible PostgreSQL URL")

    # Anthropic / AI
    anthropic_api_key: str = Field(..., description="Anthropic API key for Layer 3 LLM")

    # Application
    app_env: str = Field("development", description="development | staging | production")
    app_debug: bool = Field(False, description="Enable SQLAlchemy echo and debug logging")
    app_secret_key: str = Field(..., description="JWT signing secret")

    # Feature flags
    demo_mode: bool = Field(False, description="Demo mode: enables admin trigger endpoints")
    briefing_trigger: str = Field("07:30", description="Daily briefing generation time HH:MM UTC")


settings = Settings()
