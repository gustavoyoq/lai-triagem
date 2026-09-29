"""Promove um artefato validado para um diretório de registro sem sobrescrever versões."""

from __future__ import annotations

import argparse
import shutil
from pathlib import Path

from mlops_lai.pipelines.common import resolve
from mlops_lai.pipelines.validate_artifact import validate


def promote(source: str, target: str) -> Path:
    candidate, destination = resolve(source), resolve(target)
    model = validate(str(candidate))
    if destination.exists():
        raise FileExistsError(f"destino já existe: {destination}; versões promovidas são imutáveis")
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(candidate, destination)
    print(f"promoted version={model.version} to={destination}")
    return destination


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", default="artifacts/generated")
    parser.add_argument("--target", required=True, help="Ex.: artifacts/registry/trained-20260921")
    arguments = parser.parse_args()
    promote(arguments.source, arguments.target)


if __name__ == "__main__":
    main()
