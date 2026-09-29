from pathlib import Path

import pytest

from mlops_lai.core.errors import ContractError
from mlops_lai.core.settings import Settings
from mlops_lai.domain.schemas import PedidoLAI
from mlops_lai.service import ScoringHandler

ARTIFACTS = Path(__file__).resolve().parents[1] / "artifacts" / "registered"


def handler() -> ScoringHandler:
    return ScoringHandler(Settings(artifact_dir=ARTIFACTS, model_version="provided-2026-09-21"))


def test_bento_handler_health_and_score_contract() -> None:
    service = handler()
    health = service.health()
    score = service.score(
        PedidoLAI(
            OrgaoDestinatario="CC-PR – Casa Civil da Presidência da República",
            DataRegistro="15/09/2026",
        )
    )
    assert health["personal_data_embedded"] is False
    assert score["model_version"] == "provided-2026-09-21"


def test_bento_handler_refuses_post_hoc_fields() -> None:
    pedido = PedidoLAI.model_validate(
        {
            "OrgaoDestinatario": "CC-PR – Casa Civil da Presidência da República",
            "DataRegistro": "15/09/2026",
            "FoiReencaminhado": "Sim",
        }
    )
    with pytest.raises(ContractError, match="post-hoc"):
        handler().score(pedido)
