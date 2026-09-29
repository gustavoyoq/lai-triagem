"""Modelos Pydantic do contrato HTTP no instante de chegada do pedido."""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

HistoryValue = Annotated[float | None, Field(default=None, ge=0)]


class PedidoLAI(BaseModel):
    """Campos disponíveis quando o pedido chega; extras são validados pelo domínio."""

    model_config = ConfigDict(extra="allow", str_strip_whitespace=True)

    OrgaoDestinatario: Annotated[str, Field(min_length=1, max_length=500)]
    DataRegistro: str
    Esfera: str | None = None
    UF: str | None = None
    Municipio: str | None = None
    FormaResposta: str | None = None
    OrigemSolicitacao: str | None = None
    TipoDemandante: str | None = None
    Genero: str | None = None
    Escolaridade: str | None = None
    Profissao: str | None = None
    TipoPessoaJuridica: str | None = None
    Pais: str | None = None
    UF_sol: str | None = None
    Municipio_sol: str | None = None
    DataNascimento: str | None = None

    n_pedidos_previos: HistoryValue = None
    prev_reenc_solicitante: HistoryValue = None
    prev_reenc_solicitante_den: HistoryValue = None
    n_pedidos_previos_neste_orgao: HistoryValue = None
    prev_reenc_neste_orgao: HistoryValue = None
    prev_reenc_neste_orgao_den: HistoryValue = None
    n_orgaos_distintos_previos: HistoryValue = None
    dias_desde_ultimo_pedido: HistoryValue = None

    @field_validator("DataRegistro")
    @classmethod
    def validate_registration_date(cls, value: str) -> str:
        try:
            datetime.strptime(value, "%d/%m/%Y")
        except ValueError as exc:
            raise ValueError("DataRegistro deve usar o formato DD/MM/AAAA") from exc
        return value

    @field_validator("DataNascimento")
    @classmethod
    def validate_birth_date(cls, value: str | None) -> str | None:
        if value is None:
            return None
        try:
            datetime.strptime(value, "%d/%m/%Y")
        except ValueError as exc:
            raise ValueError("DataNascimento deve usar o formato DD/MM/AAAA") from exc
        return value


class ScoreResponse(BaseModel):
    probabilidade_reencaminhamento: float = Field(ge=0, le=1)
    alerta: Literal["ALTO RISCO", "BAIXO RISCO"]
    threshold: float = Field(ge=0, le=1)
    probabilidade_calibrada: float | None = Field(default=None, ge=0, le=1)
    calibrada_apenas_para_leitura: bool = True
    orgao_conhecido: bool
    orgao_rate_historica: float = Field(ge=0, le=1)
    orgao_rate_movel_90d: float = Field(ge=0, le=1)
    base_rate_coorte: float = Field(ge=0, le=1)
    historico_informado: bool
    historico_parcial_ignorado: bool
    model_version: str


class BaselineResponse(BaseModel):
    probabilidade_reencaminhamento: float = Field(ge=0, le=1)
    alerta: Literal["ALTO RISCO", "BAIXO RISCO"]
    metodo: str
    orgao_conhecido: bool
    model_version: str


class HealthResponse(BaseModel):
    status: Literal["ok"]
    model_version: str
    artifact_sha256: str
    n_features: int = Field(gt=0)
    threshold: float = Field(ge=0, le=1)
    train_years: list[int]
    personal_data_embedded: bool = False
