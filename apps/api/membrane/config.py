"""Runtime configuration for the Membrane proxy.

Every knob is environment driven so the same container runs locally, in
docker-compose, and on Cloud Run without a code change.
"""

from __future__ import annotations

import os
from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="MEMBRANE_",
        env_file=(".env", "../../.env"),
        extra="ignore",
    )

    # --- identity ---------------------------------------------------------
    environment: str = "dev"
    service_name: str = "membrane-api"

    # --- storage ----------------------------------------------------------
    # Default is a local SQLite file so the stack runs with zero infra.
    # docker-compose and Cloud Run override this with a Postgres DSN.
    database_url: str = "sqlite+aiosqlite:///./membrane.db"
    redis_url: str | None = None

    # --- cryptography -----------------------------------------------------
    # Signing key for intent contracts and Telegram callbacks.
    signing_key: str = "dev-insecure-signing-key-change-me"
    # Salt for the audit trail's span hashes. Rotating this makes historical
    # hashes unlinkable, which is the intended privacy behaviour.
    hash_salt: str = "dev-insecure-hash-salt-change-me"

    # --- policy -----------------------------------------------------------
    contract_ttl_seconds: int = 900
    approval_timeout_seconds: int = 60
    # Separator thresholds. Spans scoring at or above `quarantine` are
    # stripped; spans between `ambiguous` and `quarantine` escalate to the
    # model tier when it is configured, and otherwise quarantine (fail closed).
    separator_quarantine_threshold: float = 0.60
    separator_ambiguous_threshold: float = 0.35
    # Recursive decoding depth for L1. Bounded so a decode bomb cannot stall
    # a request.
    max_decode_depth: int = 4
    max_content_bytes: int = 4 * 1024 * 1024

    # --- circuit breaker --------------------------------------------------
    breaker_window_seconds: int = 120
    breaker_hold_threshold: int = 5
    breaker_cooldown_seconds: int = 300

    # --- adaptive source trust -------------------------------------------
    trust_initial: float = 0.70
    trust_incident_multiplier: float = 0.35
    trust_recovery_per_hour: float = 0.01

    # --- model escalation (L2 tier two) ----------------------------------
    vertex_project: str | None = None
    vertex_location: str = "us-central1"
    # gemini-2.0-flash was retired by Google and now 404s. Anything from 2.5
    # onward is a thinking model, which the classifier's request body accounts
    # for — see _request_body.
    vertex_model: str = "gemini-2.5-flash"
    vertex_timeout_seconds: float = 3.0
    # Simpler alternative to Vertex for local development: a Gemini API key.
    # When set it takes precedence over the Vertex path.
    gemini_api_key: str | None = None
    # When no model is reachable, ambiguous spans quarantine rather than pass.
    escalation_enabled: bool = True

    # --- human loop -------------------------------------------------------
    telegram_bot_token: str | None = None
    telegram_chat_id: str | None = None
    telegram_api_base: str = "https://api.telegram.org"
    # Demo mode lets the dashboard resolve held actions when no bot is wired.
    dashboard_approvals_enabled: bool = True

    # --- privacy ----------------------------------------------------------
    # Live previews are streamed to the dashboard over SSE from memory only.
    # They are NEVER written to the audit table. Turn off for production.
    live_preview_enabled: bool = True
    live_preview_chars: int = 160
    # Optional, bounded, off by default: retain span text for replay.
    replay_retention_enabled: bool = False
    replay_retention_hours: int = 24

    # --- server -----------------------------------------------------------
    cors_origins: str = "http://localhost:3000,http://127.0.0.1:3000"
    # An exact list cannot cover a platform that mints a new hostname per
    # deployment — every Vercel preview is its own origin. This is matched in
    # addition to the list above, never instead of it.
    #   e.g. ^https://membrane-[a-z0-9-]+\.vercel\.app$
    cors_origin_regex: str | None = None

    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]

    @property
    def is_postgres(self) -> bool:
        return self.database_url.startswith("postgresql")

    def assert_production_safe(self) -> list[str]:
        """Return a list of configuration problems that must not ship."""
        problems: list[str] = []
        if self.environment == "prod":
            if "dev-insecure" in self.signing_key:
                problems.append("MEMBRANE_SIGNING_KEY is still the dev default")
            if "dev-insecure" in self.hash_salt:
                problems.append("MEMBRANE_HASH_SALT is still the dev default")
            if self.live_preview_enabled:
                problems.append(
                    "MEMBRANE_LIVE_PREVIEW_ENABLED streams content previews; "
                    "disable it in production"
                )
        return problems


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()


def reset_settings_cache() -> None:
    """Used by tests that mutate the environment."""
    get_settings.cache_clear()
    os.environ.pop("_MEMBRANE_SETTINGS_CACHED", None)
