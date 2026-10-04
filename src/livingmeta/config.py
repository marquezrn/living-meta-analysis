"""Environment configuration; public files contain placeholders, never credentials."""

from functools import lru_cache
from pathlib import Path

from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore", case_sensitive=False)
    environment: str = "development"
    app_url: str = "http://localhost:8000"
    database_url: str = "sqlite:///private/livingmeta.sqlite"
    broker_url: str = "redis://localhost:6379/0"
    task_mode: str = "inline"
    storage_backend: str = "local"
    private_directory: Path = Path("private")
    session_secret: str = ""
    owner_github_login: str = "marquezrn"
    owner_github_id: str = "290779630"
    github_client_id: str = ""
    github_client_secret: str = ""
    openai_api_key: str = ""
    extraction_model: str = "gpt-6.1-sol"
    verification_model: str = "gpt-6-astra"
    max_total_openai_usd: float = Field(default=100.0, gt=0, le=100)
    max_run_openai_usd: float = Field(default=100.0, gt=0, le=100)
    contact_email: str = ""
    openalex_api_key: str = ""
    ncbi_api_key: str = ""
    elsevier_api_key: str = ""
    elsevier_insttoken: str = ""
    scopus_verified: bool = False
    r2_endpoint_url: str = ""
    r2_bucket: str = ""
    r2_access_key_id: str = ""
    r2_secret_access_key: str = ""
    max_upload_bytes: int = 100 * 1024 * 1024
    session_hours: int = 24
    invitation_hours: int = 72
    monitor_timezone: str = "Europe/Madrid"
    monitor_weekday: int = 0
    monitor_hour: int = 8
    allow_local_development_login: bool = True
    rscript: str = "Rscript"

    @property
    def production(self) -> bool:
        return self.environment == "production"

    @model_validator(mode="after")
    def production_requirements(self):
        if self.production:
            if len(self.session_secret) < 32:
                raise ValueError("Production requires a SESSION_SECRET of at least 32 characters")
            if not self.app_url.startswith("https://"):
                raise ValueError("Production requires an HTTPS APP_URL")
            if not self.github_client_id or not self.github_client_secret:
                raise ValueError("Production requires GitHub OAuth credentials")
            if self.task_mode != "celery":
                raise ValueError("Production requires TASK_MODE=celery")
            if self.storage_backend != "r2":
                raise ValueError("Production requires private STORAGE_BACKEND=r2")
            if not all([self.r2_endpoint_url, self.r2_bucket, self.r2_access_key_id, self.r2_secret_access_key]):
                raise ValueError("Production requires private R2 storage credentials")
            if not self.database_url.startswith(("postgresql://", "postgresql+psycopg://")):
                raise ValueError("Production requires PostgreSQL")
        return self


@lru_cache
def get_settings() -> Settings:
    return Settings()
