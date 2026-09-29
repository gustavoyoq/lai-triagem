"""Normaliza dados de treinamento sem salvar dados pessoais nos artefatos."""

from __future__ import annotations

import argparse
from pathlib import Path

import numpy as np
import pandas as pd

from mlops_lai.pipelines.common import load_config, resolve

BASE_CATEGORICAL = [
    "Esfera",
    "UF",
    "Municipio",
    "OrgaoDestinatario",
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
]
HISTORY_FIELDS = [
    "n_pedidos_previos",
    "n_pedidos_previos_neste_orgao",
    "n_orgaos_distintos_previos",
    "dias_desde_ultimo_pedido",
    "prev_reenc_solicitante",
    "prev_reenc_solicitante_den",
    "prev_reenc_neste_orgao",
    "prev_reenc_neste_orgao_den",
]


def read_csv_resilient(path: Path) -> pd.DataFrame:
    """Aceita CSV UTF-8 ou UTF-16/`;`, formato divulgado pela CGU."""
    with path.open("rb") as stream:
        prefix = stream.read(2)
    encoding = "utf-16" if prefix in {b"\xff\xfe", b"\xfe\xff"} else "utf-8-sig"
    return pd.read_csv(path, sep=None, engine="python", encoding=encoding)


def normalize_target(series: pd.Series) -> pd.Series:
    if pd.api.types.is_numeric_dtype(series):
        result = pd.to_numeric(series, errors="raise")
    else:
        values = series.astype("string").str.strip().str.casefold()
        mapping = {
            "sim": 1,
            "yes": 1,
            "true": 1,
            "1": 1,
            "não": 0,
            "nao": 0,
            "no": 0,
            "false": 0,
            "0": 0,
        }
        if not values.dropna().isin(mapping).all():
            invalid = values.dropna()[~values.dropna().isin(mapping)].unique().tolist()[:5]
            raise ValueError(f"rótulos não reconhecidos: {invalid}")
        result = values.map(mapping)
    if not result.dropna().isin([0, 1]).all():
        raise ValueError("o alvo deve ser binário")
    return result.astype("int8")


def prepare_frame(raw: pd.DataFrame, target_col: str, date_col: str) -> pd.DataFrame:
    """Limpa texto e cria apenas variáveis disponíveis no momento da chegada."""
    raw = raw.copy()
    raw.columns = [str(column).strip() for column in raw.columns]
    missing = {target_col, date_col, "OrgaoDestinatario"}.difference(raw.columns)
    if missing:
        raise ValueError(f"colunas obrigatórias ausentes: {sorted(missing)}")
    for column in raw.select_dtypes(include=["object", "string"]).columns:
        raw[column] = raw[column].astype("string").str.strip()
    registered = pd.to_datetime(raw[date_col], format="%d/%m/%Y", errors="raise")
    raw["target"] = normalize_target(raw[target_col])
    raw["_registered_at"] = registered
    raw["year"] = registered.dt.year.astype("int16")
    raw["reg_month"] = registered.dt.month.astype("int8")
    raw["reg_dow"] = registered.dt.dayofweek.astype("int8")
    raw["reg_day"] = registered.dt.day.astype("int8")
    for column in BASE_CATEGORICAL:
        if column not in raw:
            raw[column] = pd.NA
    birth_values = raw.get("DataNascimento", pd.Series(pd.NA, index=raw.index))
    birth = pd.to_datetime(birth_values, format="%d/%m/%Y", errors="coerce")
    raw["idade"] = ((registered - birth).dt.days / 365.25).round(1)
    raw.loc[~raw["idade"].between(10, 110), "idade"] = np.nan
    raw["uf_match"] = (
        raw.get("UF_sol", pd.Series("~", index=raw.index)).fillna("~")
        == raw.get("UF", pd.Series("!", index=raw.index)).fillna("!")
    ).astype("int8")
    for field in HISTORY_FIELDS:
        values = raw.get(field, pd.Series(-1.0, index=raw.index))
        raw[field] = pd.to_numeric(values, errors="coerce").fillna(-1.0)
    raw["prev_reenc_rate_solicitante"] = np.where(
        raw["prev_reenc_solicitante_den"] > 0,
        raw["prev_reenc_solicitante"] / raw["prev_reenc_solicitante_den"],
        -1.0,
    )
    raw["prev_reenc_rate_neste_orgao"] = np.where(
        raw["prev_reenc_neste_orgao_den"] > 0,
        raw["prev_reenc_neste_orgao"] / raw["prev_reenc_neste_orgao_den"],
        -1.0,
    )
    return raw.sort_values("_registered_at", kind="stable").reset_index(drop=True)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", default="configs/base.yaml")
    arguments = parser.parse_args()
    config = load_config(arguments.config)
    source = resolve(config["data"]["raw_path"])
    destination = resolve(config["data"]["processed_path"])
    destination.parent.mkdir(parents=True, exist_ok=True)
    prepared = prepare_frame(
        read_csv_resilient(source), config["data"]["target_column"], config["data"]["date_column"]
    )
    prepared.to_parquet(destination, index=False)
    print(f"prepared_rows={len(prepared)} path={destination}")


if __name__ == "__main__":
    main()
