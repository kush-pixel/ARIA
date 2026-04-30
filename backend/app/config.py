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
    anthropic_api_key: str = Field("", description="Anthropic API key for Layer 3 LLM")
    # GROQ — temporary testing override; remove when reverting to Anthropic
    groq_api_key: str = Field("", description="GROQ API key (testing only — swap back to Anthropic)")

    # Application
    app_env: str = Field("development", description="development | staging | production")
    app_debug: bool = Field(False, description="Enable SQLAlchemy echo and debug logging")
    app_secret_key: str = Field(..., description="JWT signing secret")

    # Feature flags
    demo_mode: bool = Field(False, description="Demo mode: enables admin trigger endpoints")
    briefing_trigger: str = Field("07:30", description="Daily briefing generation time HH:MM UTC")

    # Auth
    patient_jwt_secret: str = Field(
        "",
        description="Separate JWT secret for patient tokens (blast-radius isolation from clinician JWT).",
    )

    # Security (Fix 35, 36)
    patient_pseudonym_key: str = Field(
        "",
        description=(
            "HMAC-SHA256 secret for patient ID pseudonymization (Fix 35). "
            "When non-empty, MED_REC_NO is replaced with a 16-char hex digest "
            "at adapter time. Activating requires clearing the DB and re-ingesting."
        ),
    )
    jwt_expiry_minutes: int = Field(
        60,
        description=(
            "JWT access token lifetime in minutes (Fix 36). "
            "Must not exceed 60 — a leaked 7-day token gives 168× more exposure "
            "than a 1-hour token."
        ),
    )


settings = Settings()
