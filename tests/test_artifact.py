from pathlib import Path

from mlops_lai.model.loader import ModelService
from mlops_lai.pipelines.validate_artifact import validate

ARTIFACTS = Path(__file__).resolve().parents[1] / "artifacts" / "registered"


def test_registered_artifact_has_valid_hash_and_compatible_schema() -> None:
    model = validate(str(ARTIFACTS))
    assert model.booster.num_feature() == len(model.preprocessor.feature_order)
    assert model.version == "provided-2026-09-21"


def test_model_scores_known_organ_without_personal_history() -> None:
    model = ModelService(ARTIFACTS, expected_version="provided-2026-09-21")
    result = model.score(
        {
            "OrgaoDestinatario": "CC-PR – Casa Civil da Presidência da República",
            "DataRegistro": "15/09/2026",
        }
    )
    assert 0 <= result["probabilidade_reencaminhamento"] <= 1
    assert result["historico_informado"] is False
    assert result["orgao_conhecido"] is True
