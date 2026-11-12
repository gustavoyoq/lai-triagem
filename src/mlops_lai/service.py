"""Serviço BentoML para a triagem de pedidos LAI.

O BentoML cuida do servidor HTTP, OpenAPI e empacotamento. A regra de negócio
continua isolada em ``ModelService`` para que treinamento e serving usem o
mesmo contrato de artefato.
"""

from __future__ import annotations

from time import perf_counter_ns
from typing import Any

import bentoml
from bentoml.exceptions import BadInput

from mlops_lai.core.errors import ContractError
from mlops_lai.core.settings import Settings, get_settings
from mlops_lai.domain.schemas import PedidoLAI
from mlops_lai.features.preprocessor import LEAKAGE_FIELDS
from mlops_lai.model.loader import ModelService
from mlops_lai.monitoring.middleware import TelemetryMiddleware
from mlops_lai.monitoring.telemetry import LATENCY, get_recorder


class ScoringHandler:
    """Adaptador testável entre o contrato Pydantic e o modelo registrado."""

    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or get_settings()
        self.model = ModelService(
            self.settings.resolved_artifact_dir(), expected_version=self.settings.model_version
        )
        self.telemetry = get_recorder(self.settings)
        self.telemetry.preprocessor = self.model.preprocessor
        self.telemetry.model_version = self.model.version
        self.telemetry.artifact_sha256 = self.model.artifact_sha256

    def score(self, pedido: PedidoLAI) -> dict[str, Any]:
        payload = pedido.model_dump(exclude_none=True)
        with self.telemetry.operation("/score", payload) as event:
            start = perf_counter_ns()
            try:
                with LATENCY.time():
                    result = self.model.score(payload, telemetry=event)
            finally:
                event["inference_ms"] = (perf_counter_ns() - start) / 1_000_000
            event["response"] = result
            return result

    def score_baseline(self, pedido: PedidoLAI) -> dict[str, Any]:
        payload = pedido.model_dump(exclude_none=True)
        with self.telemetry.operation("/score_baseline", payload) as event:
            event["stage"] = "baseline_lookup"
            result = self.model.baseline(payload)
            event["prediction_raw"] = float(
                self.model.preprocessor.organ_rate.get(
                    str(payload["OrgaoDestinatario"]).strip(), self.model.preprocessor.base_rate
                )
            )
            event["response"] = result
            return result

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


# A margem do runtime permite ao middleware registrar sua própria resposta 504.
@bentoml.service(
    name="lai-triagem",
    traffic={"timeout": get_settings().request_timeout_seconds + 1},
    resources={"cpu": "2"},
)
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

    @bentoml.on_shutdown
    def close_telemetry(self) -> None:
        self.handler.telemetry.close()


LaiTriagem.add_asgi_middleware(TelemetryMiddleware)
