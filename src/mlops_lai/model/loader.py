"""Registro local imutável: modelo nativo + metadados + hashes SHA-256."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import lightgbm as lgb

from mlops_lai.core.errors import ArtifactError
from mlops_lai.features.preprocessor import Preprocessor, risk_label


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


@dataclass(frozen=True)
class ArtifactManifest:
    model_file: str
    preprocessor_file: str
    model_sha256: str
    preprocessor_sha256: str
    model_version: str
    source: str

    @classmethod
    def from_file(cls, path: Path) -> ArtifactManifest:
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
            return cls(**{key: raw[key] for key in cls.__dataclass_fields__})
        except (OSError, json.JSONDecodeError, KeyError, TypeError) as exc:
            raise ArtifactError(f"manifesto inválido em {path}: {exc}") from exc


class ModelService:
    """Modelo carregado uma vez no startup e usado por requisições concorrentes."""

    def __init__(self, artifact_dir: Path, expected_version: str | None = None) -> None:
        self.artifact_dir = artifact_dir
        self.manifest = ArtifactManifest.from_file(artifact_dir / "manifest.json")
        if expected_version and expected_version != self.manifest.model_version:
            raise ArtifactError(
                f"versão configurada ({expected_version}) difere do artefato "
                f"({self.manifest.model_version})"
            )
        self._validate_integrity()
        self.preprocessor = Preprocessor.from_file(artifact_dir / self.manifest.preprocessor_file)
        try:
            # `model_file` usa a API C do LightGBM, que em Windows falha em
            # alguns caminhos Unicode (por exemplo, diretórios com acentos).
            # O formato nativo de texto é o mesmo; carregar seu conteúdo evita
            # depender da codificação do caminho e também funciona em Linux.
            model_text = (artifact_dir / self.manifest.model_file).read_text(encoding="utf-8")
            self.booster = lgb.Booster(model_str=model_text)
        except Exception as exc:  # LightGBM não expõe um tipo de erro estável.
            raise ArtifactError(f"LightGBM não carregou o modelo: {exc}") from exc
        if self.booster.num_feature() != len(self.preprocessor.feature_order):
            raise ArtifactError("modelo e preprocessor têm números de features incompatíveis")

    @property
    def version(self) -> str:
        return self.manifest.model_version

    @property
    def artifact_sha256(self) -> str:
        return self.manifest.model_sha256

    def _validate_integrity(self) -> None:
        expected = {
            self.manifest.model_file: self.manifest.model_sha256,
            self.manifest.preprocessor_file: self.manifest.preprocessor_sha256,
        }
        for relative, known_hash in expected.items():
            candidate = self.artifact_dir / relative
            if not candidate.is_file():
                raise ArtifactError(f"arquivo do artefato ausente: {candidate}")
            if sha256_file(candidate) != known_hash:
                raise ArtifactError(f"checksum SHA-256 inválido: {candidate.name}")

    def score(
        self, payload: dict[str, Any], *, telemetry: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        if telemetry is not None:
            telemetry["stage"] = "preprocessing"
        features = self.preprocessor.transform(payload)
        if telemetry is not None:
            telemetry["features"] = features.iloc[0].to_dict()
            telemetry["quality"] = self.preprocessor.monitoring_state(payload, features)
            telemetry["stage"] = "inference"
        raw_score = float(self.booster.predict(features)[0])
        if telemetry is not None:
            telemetry["prediction_raw"] = raw_score
            telemetry["stage"] = "calibration"
        calibrated = self.preprocessor.calibrate(raw_score)
        history = self.preprocessor.history_state(payload)
        organ = str(payload["OrgaoDestinatario"]).strip()
        return {
            "probabilidade_reencaminhamento": round(raw_score, 6),
            "alerta": risk_label(raw_score, self.preprocessor.threshold),
            "threshold": self.preprocessor.threshold,
            "probabilidade_calibrada": None if calibrated is None else round(calibrated, 6),
            "calibrada_apenas_para_leitura": True,
            "orgao_conhecido": self.preprocessor.organ_is_known(organ),
            "orgao_rate_historica": round(
                float(self.preprocessor.organ_rate.get(organ, self.preprocessor.base_rate)), 6
            ),
            "orgao_rate_movel_90d": round(
                float(self.preprocessor.organ_movel_90.get(organ, self.preprocessor.base_rate)), 6
            ),
            "base_rate_coorte": round(self.preprocessor.base_rate, 6),
            "historico_informado": history.supplied,
            "historico_parcial_ignorado": history.partial_ignored,
            "model_version": self.version,
        }

    def baseline(self, payload: dict[str, Any]) -> dict[str, Any]:
        self.preprocessor.validate_payload(payload)
        organ = str(payload["OrgaoDestinatario"]).strip()
        score = float(self.preprocessor.organ_rate.get(organ, self.preprocessor.base_rate))
        return {
            "probabilidade_reencaminhamento": round(score, 6),
            "alerta": risk_label(score, self.preprocessor.threshold),
            "metodo": "lookup histórico por órgão (sem modelo)",
            "orgao_conhecido": self.preprocessor.organ_is_known(organ),
            "model_version": self.version,
        }
