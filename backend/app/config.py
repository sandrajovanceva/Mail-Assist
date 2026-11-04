from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

BASE_DIR = Path(__file__).resolve().parent.parent


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=BASE_DIR / ".env", extra="ignore")

    gemini_api_key: str = ""
    secret_key: str = "dev-secret-change-me"  # override in .env for production
    google_client_id: str = ""
    google_client_secret: str = ""
    google_redirect_uri: str = "http://localhost:8000/auth/google/callback"
    frontend_url: str = "http://localhost:5173"

    # Two model tiers, both swappable from .env so the evaluation chapter can
    # compare models without touching code.
    #
    # The classifier runs on *every* synced email, so it defaults to a lite model
    # with a generous free-tier daily quota. The drafter runs only when the user
    # asks for a reply, so it can afford the stronger (lower-quota) flash model.
    classifier_model: str = "gemini-3.5-flash-lite"
    drafter_model: str = "gemini-3.8-flash"
    assistant_model: str = ""  # empty uses classifier_model for interactive chat

    # Thinking depth, only sent when set ("low"/"medium"/"high", Gemini 3+).
    # Left empty by default so swapping in an older model from .env can't 400 on
    # an unsupported field; set it to measure the thinking/quality tradeoff.
    classifier_thinking_level: str = ""
    drafter_thinking_level: str = ""

    # Free-tier keys are rate limited (429 RESOURCE_EXHAUSTED). Keep sync
    # concurrency low and retry with backoff rather than failing the whole sync.
    sync_max_emails: int = 20
    sync_concurrency: int = 4
    llm_max_retries: int = 4
    llm_retry_base_delay: float = 2.0  # seconds; doubled each attempt
    llm_timeout_seconds: int = 90  # per request; drafting long mail is slow

    # A sync still marked "running" after this long is assumed dead (crashed
    # worker, killed process) and no longer blocks new syncs.
    sync_stale_after_minutes: int = 10

    database_url: str = f"sqlite:///{BASE_DIR / 'mailassist.db'}"


settings = Settings()
