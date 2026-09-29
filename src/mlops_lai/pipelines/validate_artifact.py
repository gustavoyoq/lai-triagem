"""Portão de promoção: integridade, compatibilidade e ausência de leakage conhecido."""

from __future__ import annotations

import argparse

from mlops_lai.core.errors import ArtifactError
from mlops_lai.features.preprocessor import LEAKAGE_FIELDS
from mlops_lai.model.loader import ModelService
from mlops_lai.pipelines.common import resolve


def validate(artifact_dir: str) -> ModelService:
    model = ModelService(resolve(artifact_dir))
    forbidden_features = sorted(set(model.preprocessor.feature_order).intersection(LEAKAGE_FIELDS))
    if forbidden_features:
        raise ArtifactError(f"artefato contém features post-hoc: {forbidden_features}")
    accidental_history = sorted(
        set(model.preprocessor.caller_features).intersection(model.preprocessor.organ_rate)
    )
    if accidental_history:
        raise ArtifactError(f"tabela do artefato contém chave pessoal: {accidental_history}")
    if model.preprocessor.meta.get("contains_personal_data", False):
        raise ArtifactError("metadado afirma conter dados pessoais")
    return model


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--artifact-dir", default="artifacts/registered")
    arguments = parser.parse_args()
    model = validate(arguments.artifact_dir)
    print(f"artifact_valid version={model.version} sha256={model.artifact_sha256}")


if __name__ == "__main__":
    main()
