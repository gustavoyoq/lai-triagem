"""Configuração centralizada, sempre relativa à raiz do projeto quando local."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

PROJECT_ROOT = Path(__file__).resolve().parents[3]


class Settings(BaseSettings):
    """Variáveis do serviço; `.env` é opcional e nunca é commitado."""

    model_config = SettingsConfigDict(env_file=".env", env_prefix="MLOPS_", extra="ignore")

    env: str = "development"
    artifact_dir: Path = Path("artifacts/registered")
    model_version: str = "provided-2026-09-21"
    enable_telemetry: bool = False
    telemetry_path: Path = Path("monitoring/telemetry/scoring.jsonl")
    log_level: str = "INFO"

    def resolved_artifact_dir(self) -> Path:
        return self._resolve(self.artifact_dir)

    def resolved_telemetry_path(self) -> Path:
        return self._resolve(self.telemetry_path)

    @staticmethod
    def _resolve(path: Path) -> Path:
        return path if path.is_absolute() else PROJECT_ROOT / path


@lru_cache
def get_settings() -> Settings:
    """Retém configuração imutável durante a vida do processo."""
    return Settings()
