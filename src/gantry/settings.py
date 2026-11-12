"""Typed configuration. One place that reads the environment; everything else takes a Settings."""

from __future__ import annotations

from functools import lru_cache
from typing import Literal

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

Provider = Literal["auto", "offline", "anthropic", "openai"]
Effort = Literal["low", "medium", "high"]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env", env_prefix="GANTRY_", extra="ignore", env_file_encoding="utf-8", populate_by_name=True
    )

    # --- models: offline by default (deterministic, free, no keys). Real models are opt-in. -----------------
    provider: Provider = "offline"
    model_smart: str = "claude-opus-5-5"  # judgement calls: writes the polls, re-ranks the editor's shortlist
    model_fast: str = "claude-haiku-5-5"  # high-volume, latency-sensitive voice lines (notes, host messages, recaps)
    llm_effort: Effort = "low"  # a live broadcast is latency-bound: a suggestion that lands late is worth nothing
    llm_timeout_s: float = 30.0
    llm_max_tokens: int = 2000  # always set; includes the model's (adaptive, low-effort) thinking
    offline_token_delay_s: float = Field(0.01, ge=0, le=1)  # makes streamed tokens visible in Studio and the console

    openai_model_smart: str = "gpt-5.4-mini"
    openai_model_fast: str = "gpt-5.4-mini"
    openai_max_tokens: int = 1500

    anthropic_api_key: SecretStr | None = Field(None, validation_alias="ANTHROPIC_API_KEY")
    # Keys that are not scoped to a workspace must send `anthropic-workspace-id` on every request (else HTTP 400).
    anthropic_workspace_id: str | None = Field(None, validation_alias="ANTHROPIC_WORKSPACE_ID")
    openai_api_key: SecretStr | None = Field(None, validation_alias="OPENAI_API_KEY")

    # --- links the UI offers ------------------------------------------------------------------------------
    public_base_url: str = "http://127.0.0.1:2030"
    studio_url: str = "https://smith.langchain.com/studio/"
    langsmith_project: str = "gantry"
    langsmith_url: str = "https://smith.langchain.com/"

    def resolved_provider(self) -> Literal["offline", "anthropic", "openai"]:
        """`offline` unless a real provider is asked for. `auto` prefers Anthropic, then OpenAI, when a key is present."""
        if self.provider in {"anthropic", "openai"}:
            return self.provider  # type: ignore[return-value]
        if self.provider == "auto":
            if self.anthropic_api_key and self.anthropic_api_key.get_secret_value():
                return "anthropic"
            if self.openai_api_key and self.openai_api_key.get_secret_value():
                return "openai"
        return "offline"

    def model_name(self, role: str = "fast") -> str:
        p = self.resolved_provider()
        if p == "offline":
            return "deterministic offline model"
        if p == "openai":
            return self.openai_model_smart if role == "smart" else self.openai_model_fast
        return self.model_smart if role == "smart" else self.model_fast


@lru_cache
def get_settings() -> Settings:
    return Settings()
