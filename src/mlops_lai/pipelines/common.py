"""Utilitários compartilhados dos estágios de dados e modelo."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import yaml

from mlops_lai.core.settings import PROJECT_ROOT
from mlops_lai.model.loader import sha256_file


def load_config(path: str | Path) -> dict[str, Any]:
    config_path = Path(path)
    if not config_path.is_absolute():
        config_path = PROJECT_ROOT / config_path
    with config_path.open(encoding="utf-8") as stream:
        return yaml.safe_load(stream)


def resolve(path: str | Path) -> Path:
    candidate = Path(path)
    return candidate if candidate.is_absolute() else PROJECT_ROOT / candidate


def write_manifest(output_dir: Path, model_file: str, preprocessor_file: str, version: str) -> None:
    """Gera o atestado de integridade que o serving valida antes de carregar."""
    manifest = {
        "model_file": model_file,
        "preprocessor_file": preprocessor_file,
        "model_sha256": sha256_file(output_dir / model_file),
        "preprocessor_sha256": sha256_file(output_dir / preprocessor_file),
        "model_version": version,
        "source": "pipeline-temporal-reproducivel",
        "created_utc": datetime.now(UTC).isoformat(),
    }
    (output_dir / "manifest.json").write_text(
        json.dumps(manifest, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
