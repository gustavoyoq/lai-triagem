"""Transformação de chegada do pedido, extraída da lógica do modelo fornecido.

O módulo é a fonte única de verdade do contrato: não usa campos posteriores à
triagem e não armazena histórico de solicitantes no artefato.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from mlops_lai.core.errors import ArtifactError, ContractError

LEAKAGE_FIELDS = frozenset(
    {
        "Situacao",
        "FoiProrrogado",
        "FoiReencaminhado",
        "DataResposta",
        "Decisao",
        "EspecificacaoDecisao",
        "DetalhamentoDecisao",
        "MotivoNegativaAcesso",
        "PrazoRestricaoAcesso",
        "AssuntoPedido",
        "SubAssuntoPedido",
        "Tag",
        "PrazoAtendimento",
        "prazo_dias",
        "ProtocoloPedido",
        "protocolo_seq",
    }
)

REQUEST_FIELDS = frozenset(
    {
        "OrgaoDestinatario",
        "DataRegistro",
        "Esfera",
        "UF",
        "Municipio",
        "FormaResposta",
        "OrigemSolicitacao",
        "TipoDemandante",
        "Genero",
        "Escolaridade",
        "Profissao",
        "TipoPessoaJuridica",
        "Pais",
        "UF_sol",
        "Municipio_sol",
        "DataNascimento",
        "n_pedidos_previos",
        "prev_reenc_solicitante",
        "prev_reenc_solicitante_den",
        "n_pedidos_previos_neste_orgao",
        "prev_reenc_neste_orgao",
        "prev_reenc_neste_orgao_den",
        "n_orgaos_distintos_previos",
        "dias_desde_ultimo_pedido",
    }
)


@dataclass(frozen=True)
class HistoryState:
    supplied: bool
    partial_ignored: bool


class Preprocessor:
    """Converte o payload HTTP numa linha ordenada para o LightGBM."""

    def __init__(self, metadata: dict[str, Any]) -> None:
        required = {
            "feature_order",
            "categorical_features",
            "numeric_features",
            "category_codes",
            "organ_rate",
            "base_rate",
            "threshold",
            "train_years",
        }
        missing = sorted(required.difference(metadata))
        if missing:
            raise ArtifactError(f"preprocessor.json sem campos obrigatórios: {missing}")
        self.meta = metadata
        self.feature_order = list(metadata["feature_order"])
        self.categorical = list(metadata["categorical_features"])
        self.numeric = list(metadata["numeric_features"])
        self.codes = metadata["category_codes"]
        self.organ_rate = metadata["organ_rate"]
        self.organ_movel_90 = metadata.get("organ_rate_movel_90d", {})
        self.organ_movel_365 = metadata.get("organ_rate_movel_365d", {})
        self.organ_birth = metadata.get("organ_birth", {})
        self.base_rate = float(metadata["base_rate"])
        self.threshold = float(metadata["threshold"])
        self.caller_features = list(metadata.get("caller_supplied_features", []))
        self.caller_default = float(metadata.get("caller_supplied_default", -1))
        self.derived_from_caller = set(metadata.get("derived_from_caller", []))
        self.calibration = metadata.get("calibration") or {}
        self._validate_metadata()

    @classmethod
    def from_file(cls, path: str | Path) -> Preprocessor:
        try:
            contents = Path(path).read_text(encoding="utf-8")
            return cls(json.loads(contents))
        except (OSError, json.JSONDecodeError) as exc:
            raise ArtifactError(f"não foi possível ler preprocessor.json: {exc}") from exc

    def _validate_metadata(self) -> None:
        if set(self.categorical).intersection(self.numeric):
            raise ArtifactError("features categóricas e numéricas não podem se sobrepor")
        if set(self.feature_order) != set(self.categorical).union(self.numeric):
            raise ArtifactError("feature_order não corresponde às features declaradas")
        if not 0 <= self.base_rate <= 1 or not 0 <= self.threshold <= 1:
            raise ArtifactError("base_rate e threshold devem pertencer a [0, 1]")
        if not set(self.caller_features).issubset(REQUEST_FIELDS):
            raise ArtifactError("artefato pede campo pessoal fora do contrato")

    @staticmethod
    def _normalize(payload: dict[str, Any]) -> dict[str, Any]:
        return {
            key: value.strip() if isinstance(value, str) else value
            for key, value in payload.items()
        }

    def validate_payload(self, payload: dict[str, Any]) -> None:
        keys = set(payload)
        leakage = sorted(keys.intersection(LEAKAGE_FIELDS))
        if leakage:
            raise ContractError(f"campos post-hoc recusados: {leakage}")
        unknown = sorted(keys.difference(REQUEST_FIELDS))
        if unknown:
            raise ContractError(f"campos fora do contrato de chegada: {unknown}")
        for name in ("OrgaoDestinatario", "DataRegistro"):
            if not payload.get(name):
                raise ContractError(f"campo obrigatório ausente: {name}")

    def history_state(self, payload: dict[str, Any]) -> HistoryState:
        present = sum(payload.get(name) is not None for name in self.caller_features)
        return HistoryState(
            supplied=bool(self.caller_features) and present == len(self.caller_features),
            partial_ignored=0 < present < len(self.caller_features),
        )

    def transform(self, payload: dict[str, Any]) -> pd.DataFrame:
        """Aplica o contrato atômico de histórico e produz as features do artefato."""
        self.validate_payload(payload)
        p = self._normalize(payload)
        registered = pd.to_datetime(p["DataRegistro"], format="%d/%m/%Y", errors="raise")
        birth_value = p.get("DataNascimento")
        born_person = (
            pd.to_datetime(birth_value, format="%d/%m/%Y", errors="raise")
            if birth_value is not None
            else pd.NaT
        )
        age = np.nan
        if pd.notna(born_person):
            age = round((registered - born_person).days / 365.25, 1)
            if not 10 <= age <= 110:
                age = np.nan

        organ = str(p["OrgaoDestinatario"])
        organ_age = np.nan
        if first_seen := self.organ_birth.get(organ):
            organ_age = float((registered - pd.Timestamp(first_seen)).days)

        derived: dict[str, float | int] = {
            "reg_month": registered.month,
            "reg_dow": registered.dayofweek,
            "reg_day": registered.day,
            "idade": age,
            "orgao_rate": float(self.organ_rate.get(organ, self.base_rate)),
            "orgao_rate_movel_90d": float(self.organ_movel_90.get(organ, self.base_rate)),
            "orgao_rate_movel_365d": float(self.organ_movel_365.get(organ, self.base_rate)),
            "dias_desde_primeiro_pedido_do_orgao": organ_age,
            "uf_match": int((p.get("UF_sol") or "~") == (p.get("UF") or "!")),
        }
        history = self.history_state(p)
        for name in self.caller_features:
            derived[name] = float(p[name]) if history.supplied else self.caller_default

        self._derive_history_rates(derived)
        row: dict[str, float] = {}
        for name in self.categorical:
            row[name] = float(self.codes.get(name, {}).get(str(p.get(name) or ""), -1))
        for name in self.numeric:
            value = derived.get(name, p.get(name, np.nan))
            row[name] = float(value) if value is not None else np.nan
        return pd.DataFrame([row], columns=self.feature_order).astype("float64")

    def _derive_history_rates(self, values: dict[str, float | int]) -> None:
        pairs = (
            ("prev_reenc_rate_solicitante", "prev_reenc_solicitante", "prev_reenc_solicitante_den"),
            ("prev_reenc_rate_neste_orgao", "prev_reenc_neste_orgao", "prev_reenc_neste_orgao_den"),
        )
        for output, numerator, denominator in pairs:
            if output not in self.derived_from_caller:
                continue
            num, den = (
                values.get(numerator, self.caller_default),
                values.get(denominator, self.caller_default),
            )
            values[output] = float(num / den) if num >= 0 and den > 0 else self.caller_default

    def organ_is_known(self, organ: str) -> bool:
        return organ.strip() in self.organ_rate

    def calibrate(self, score: float) -> float | None:
        if not self.calibration:
            return None
        grid, image = self.calibration.get("grid"), self.calibration.get("image")
        if not grid or not image or len(grid) != len(image):
            raise ArtifactError("calibração inválida no artefato")
        return float(np.interp(score, grid, image))


def risk_label(score: float, threshold: float) -> str:
    return "ALTO RISCO" if score >= threshold else "BAIXO RISCO"
