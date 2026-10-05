"""Serviço BentoML para a triagem de pedidos LAI.

O BentoML cuida do servidor HTTP, OpenAPI e empacotamento. A regra de negócio
continua isolada em ``ModelService`` para que treinamento e serving usem o
mesmo contrato de artefato.
"""

from __future__ import annotations

from typing import Any

import bentoml
from bentoml.exceptions import BadInput

from mlops_lai.core.errors import ContractError
from mlops_lai.core.settings import Settings, get_settings
from mlops_lai.domain.schemas import PedidoLAI
from mlops_lai.features.preprocessor import LEAKAGE_FIELDS
from mlops_lai.model.loader import ModelService
from mlops_lai.monitoring.telemetry import LATENCY, TelemetryRecorder


class ScoringHandler:
    """Adaptador testável entre o contrato Pydantic e o modelo registrado."""

    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or get_settings()
        self.model = ModelService(
            self.settings.resolved_artifact_dir(), expected_version=self.settings.model_version
        )
        self.telemetry = TelemetryRecorder(
            self.settings.enable_telemetry, self.settings.resolved_telemetry_path()
        )

    def score(self, pedido: PedidoLAI) -> dict[str, Any]:
        with LATENCY.time():
            result = self.model.score(pedido.model_dump(exclude_none=True))
        self.telemetry.record(
            score=result["probabilidade_reencaminhamento"],
            model_version=result["model_version"],
            organ_known=result["orgao_conhecido"],
            history_supplied=result["historico_informado"],
        )
        return result

    def score_baseline(self, pedido: PedidoLAI) -> dict[str, Any]:
        return self.model.baseline(pedido.model_dump(exclude_none=True))

    def health(self) -> dict[str, Any]:
        return {
            "status": "ok",
            "model_version": self.model.version,
            "artifact_sha256": self.model.artifact_sha256,
            "n_features": len(self.model.preprocessor.feature_order),
            "threshold": self.model.preprocessor.threshold,
            "train_years": self.model.preprocessor.meta["train_years"],
            "personal_data_embedded": False,
        }

    def metadata(self) -> dict[str, Any]:
        return {
            "model_version": self.model.version,
            "feature_count": len(self.model.preprocessor.feature_order),
            "request_history_fields": self.model.preprocessor.caller_features,
            "leakage_fields_rejected": sorted(LEAKAGE_FIELDS),
            "artifact_source": self.model.manifest.source,
        }


@bentoml.service(name="lai-triagem", traffic={"timeout": 20}, resources={"cpu": "2"})
class LaiTriagem:
    """APIs BentoML: ``POST /score``, ``/score_baseline``, ``/health`` e metadata."""

    def __init__(self) -> None:
        self.handler = ScoringHandler()

    @bentoml.api
    def score(self, pedido: PedidoLAI) -> dict[str, Any]:
        """Calcula a probabilidade de reencaminhamento de um pedido LAI."""
        try:
            return self.handler.score(pedido)
        except ContractError as exc:
            raise BadInput(str(exc)) from exc

    @bentoml.api
    def score_baseline(self, pedido: PedidoLAI) -> dict[str, Any]:
        try:
            return self.handler.score_baseline(pedido)
        except ContractError as exc:
            raise BadInput(str(exc)) from exc

    @bentoml.api
    def health(self) -> dict[str, Any]:
        return self.handler.health()

    @bentoml.api
    def metadata(self) -> dict[str, Any]:
        return self.handler.metadata()
