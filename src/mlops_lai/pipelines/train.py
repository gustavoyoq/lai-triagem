"""Treino temporal que gera um artefato verificável e compatível com a API."""

from __future__ import annotations

import argparse
import json
from datetime import UTC, datetime
from typing import Any

import lightgbm as lgb
import numpy as np
import pandas as pd
from sklearn.isotonic import IsotonicRegression
from sklearn.metrics import average_precision_score, roc_auc_score

from mlops_lai.metrics import precision_at_k
from mlops_lai.pipelines.common import load_config, resolve, write_manifest
from mlops_lai.pipelines.prepare import BASE_CATEGORICAL, HISTORY_FIELDS

NUMERIC_FEATURES = [
    "reg_month",
    "reg_dow",
    "reg_day",
    "idade",
    "orgao_rate",
    "uf_match",
    "n_pedidos_previos",
    "prev_reenc_solicitante",
    "prev_reenc_rate_solicitante",
    "n_pedidos_previos_neste_orgao",
    "prev_reenc_neste_orgao",
    "n_orgaos_distintos_previos",
    "dias_desde_ultimo_pedido",
    "prev_reenc_rate_neste_orgao",
    "orgao_rate_movel_90d",
    "orgao_rate_movel_365d",
    "dias_desde_primeiro_pedido_do_orgao",
]
FEATURE_ORDER = BASE_CATEGORICAL + NUMERIC_FEATURES


def smoothed_rate(frame: pd.DataFrame, base_rate: float, prior: float = 50.0) -> dict[str, float]:
    grouped = frame.groupby("OrgaoDestinatario", dropna=False)["target"].agg(["sum", "count"])
    return {
        str(organ): float((row["sum"] + prior * base_rate) / (row["count"] + prior))
        for organ, row in grouped.iterrows()
        if pd.notna(organ)
    }


def window_rate(
    frame: pd.DataFrame, base_rate: float, days: int, prior: float = 20.0
) -> dict[str, float]:
    cutoff = frame["_registered_at"].max() - pd.Timedelta(days=days)
    return smoothed_rate(frame.loc[frame["_registered_at"] >= cutoff], base_rate, prior)


def causal_organ_rate(
    frame: pd.DataFrame,
    base_rate: float,
    maturity_days: int,
    prior: float,
    window_days: int | None = None,
) -> pd.Series:
    """Taxa por órgão disponível à época de cada linha de treino.

    O alvo só passa a integrar a estatística após a janela de maturação. Isso
    impede que a própria linha (ou pedidos recém-abertos) informe sua feature.
    """
    result = pd.Series(index=frame.index, dtype="float64")
    delay = np.timedelta64(maturity_days, "D")
    for _, group in frame.groupby("OrgaoDestinatario", dropna=False, sort=False):
        ordered = group.sort_values("_registered_at", kind="stable")
        dates = ordered["_registered_at"].to_numpy(dtype="datetime64[ns]")
        labels = ordered["target"].to_numpy(dtype="float64")
        cumulative = np.concatenate(([0.0], np.cumsum(labels)))
        rates = np.empty(len(ordered), dtype="float64")
        for index, date in enumerate(dates):
            upper = np.searchsorted(dates, date - delay, side="right")
            lower = 0
            if window_days is not None:
                lower = np.searchsorted(
                    dates, date - delay - np.timedelta64(window_days, "D"), side="right"
                )
            count = upper - lower
            positives = cumulative[upper] - cumulative[lower]
            rates[index] = (positives + prior * base_rate) / (count + prior)
        result.loc[ordered.index] = rates
    return result


def encode_features(
    frame: pd.DataFrame,
    category_codes: dict[str, dict[str, int]],
    organ_rate: dict[str, float],
    organ_rate_90: dict[str, float],
    organ_rate_365: dict[str, float],
    organ_birth: dict[str, str],
    base_rate: float,
) -> pd.DataFrame:
    """Aplica apenas estatísticas ajustadas no conjunto de treino ao frame inteiro."""
    encoded = pd.DataFrame(index=frame.index)
    for feature in BASE_CATEGORICAL:
        encoded[feature] = (
            frame[feature].fillna("").astype(str).map(category_codes[feature]).fillna(-1)
        )
    organ = frame["OrgaoDestinatario"].fillna("").astype(str)
    encoded["orgao_rate"] = organ.map(organ_rate).fillna(base_rate)
    encoded["orgao_rate_movel_90d"] = organ.map(organ_rate_90).fillna(base_rate)
    encoded["orgao_rate_movel_365d"] = organ.map(organ_rate_365).fillna(base_rate)
    registered = pd.to_datetime(frame["_registered_at"], errors="raise")
    birth = pd.to_datetime(organ.map(organ_birth), errors="coerce")
    encoded["dias_desde_primeiro_pedido_do_orgao"] = (registered - birth).dt.days
    for feature in NUMERIC_FEATURES:
        if feature not in encoded:
            encoded[feature] = pd.to_numeric(frame.get(feature), errors="coerce")
    return encoded[FEATURE_ORDER].astype("float64")


def metrics_for(
    y_true: np.ndarray, scores: np.ndarray, queue_fraction: float
) -> dict[str, float | None]:
    roc = float(roc_auc_score(y_true, scores)) if len(np.unique(y_true)) > 1 else None
    pr = float(average_precision_score(y_true, scores)) if len(np.unique(y_true)) > 1 else None
    return {
        "roc_auc": roc,
        "pr_auc": pr,
        "precision_at_queue": precision_at_k(y_true, scores, queue_fraction),
    }


def train(config: dict[str, Any]) -> dict[str, Any]:
    data_path = resolve(config["data"]["processed_path"])
    if not data_path.exists():
        raise FileNotFoundError(
            f"dataset processado não encontrado: {data_path}; execute lai-prepare"
        )
    frame = pd.read_parquet(data_path)
    if frame.empty:
        raise ValueError("dataset processado vazio")
    split = config["split"]
    train_years = split["train_years"]
    validation_year, test_year = split["validation_year"], split["test_year"]
    train_frame = frame.loc[frame["year"].isin(train_years)].copy()
    validation_frame = frame.loc[frame["year"] == validation_year].copy()
    test_frame = frame.loc[frame["year"] == test_year].copy()
    if train_frame.empty or validation_frame.empty or test_frame.empty:
        raise ValueError("corte temporal incompleto: são necessários treino, validação e teste")
    if train_frame["_registered_at"].max() >= validation_frame["_registered_at"].min():
        raise ValueError("corte temporal inválido: treino se sobrepõe à validação")
    base_rate = float(train_frame["target"].mean())
    category_codes = {
        feature: {
            value: index
            for index, value in enumerate(
                sorted(train_frame[feature].dropna().astype(str).unique())
            )
        }
        for feature in BASE_CATEGORICAL
    }
    organ_rate = smoothed_rate(train_frame, base_rate)
    organ_rate_90 = window_rate(train_frame, base_rate, 90)
    organ_rate_365 = window_rate(train_frame, base_rate, 365)
    births = (
        train_frame.groupby("OrgaoDestinatario", dropna=True)["_registered_at"]
        .min()
        .dt.strftime("%Y-%m-%d")
        .to_dict()
    )
    X = encode_features(
        frame, category_codes, organ_rate, organ_rate_90, organ_rate_365, births, base_rate
    )
    X_train = X.loc[train_frame.index].copy()
    maturity_days = int(config["data"]["maturity_days"])
    X_train["orgao_rate"] = causal_organ_rate(train_frame, base_rate, maturity_days, prior=50.0)
    X_train["orgao_rate_movel_90d"] = causal_organ_rate(
        train_frame, base_rate, maturity_days, prior=20.0, window_days=90
    )
    X_train["orgao_rate_movel_365d"] = causal_organ_rate(
        train_frame, base_rate, maturity_days, prior=20.0, window_days=365
    )
    X_validation = X.loc[validation_frame.index]
    X_test = X.loc[test_frame.index]
    y_train = train_frame["target"].to_numpy()
    y_validation = validation_frame["target"].to_numpy()
    y_test = test_frame["target"].to_numpy()
    params = {
        "objective": "binary",
        "metric": "binary_logloss",
        "verbosity": -1,
        "learning_rate": config["model"]["learning_rate"],
        "num_leaves": config["model"]["num_leaves"],
        "feature_fraction": config["model"]["feature_fraction"],
        "bagging_fraction": config["model"]["bagging_fraction"],
        "bagging_freq": config["model"]["bagging_freq"],
        "min_data_in_leaf": config["model"]["min_data_in_leaf"],
        "seed": config["project"]["seed"],
        "feature_fraction_seed": config["project"]["seed"],
        "bagging_seed": config["project"]["seed"],
        "data_random_seed": config["project"]["seed"],
    }
    train_dataset = lgb.Dataset(X_train, label=y_train, feature_name=FEATURE_ORDER)
    validation_dataset = lgb.Dataset(X_validation, label=y_validation, reference=train_dataset)
    booster = lgb.train(
        params,
        train_dataset,
        num_boost_round=config["model"]["num_boost_round"],
        valid_sets=[validation_dataset],
        callbacks=[lgb.early_stopping(config["model"]["early_stopping_rounds"], verbose=False)],
    )
    val_scores = booster.predict(X_validation, num_iteration=booster.best_iteration)
    test_scores = booster.predict(X_test, num_iteration=booster.best_iteration)
    queue_fraction = float(config["model"]["queue_fraction"])
    threshold = float(np.quantile(val_scores, 1 - queue_fraction))
    calibrator = IsotonicRegression(out_of_bounds="clip").fit(val_scores, y_validation)
    metadata = {
        "model_file": "model.txt",
        "created_utc": datetime.now(UTC).isoformat(),
        "data_snapshot": str(frame["_registered_at"].max()),
        "train_years": train_years,
        "val_year": validation_year,
        "test_year": test_year,
        "maturity_days": config["data"]["maturity_days"],
        "feature_order": FEATURE_ORDER,
        "categorical_features": BASE_CATEGORICAL,
        "numeric_features": NUMERIC_FEATURES,
        "category_codes": category_codes,
        "organ_rate": organ_rate,
        "organ_rate_movel_90d": organ_rate_90,
        "organ_rate_movel_365d": organ_rate_365,
        "organ_birth": births,
        "base_rate": base_rate,
        "threshold": threshold,
        "queue_fraction": queue_fraction,
        "caller_supplied_features": HISTORY_FIELDS,
        "caller_supplied_default": -1,
        "derived_from_caller": ["prev_reenc_rate_solicitante", "prev_reenc_rate_neste_orgao"],
        "calibration": {
            "grid": calibrator.X_thresholds_.tolist(),
            "image": calibrator.y_thresholds_.tolist(),
        },
        "contains_personal_data": False,
    }
    output = resolve(config["artifact"]["output_dir"])
    output.mkdir(parents=True, exist_ok=True)
    # A API C do LightGBM falha em caminhos Windows Unicode; a serialização
    # nativa em texto preserva o mesmo formato e deixa o pathlib gravar o arquivo.
    (output / "model.txt").write_text(
        booster.model_to_string(num_iteration=booster.best_iteration), encoding="utf-8"
    )
    (output / "preprocessor.json").write_text(
        json.dumps(metadata, ensure_ascii=False), encoding="utf-8"
    )
    version = f"trained-{datetime.now(UTC):%Y%m%dT%H%M%SZ}"
    write_manifest(output, "model.txt", "preprocessor.json", version)
    report = {
        "model_version": version,
        "best_iteration": booster.best_iteration,
        "threshold": threshold,
        "validation": metrics_for(y_validation, val_scores, queue_fraction),
        "test": metrics_for(y_test, test_scores, queue_fraction),
    }
    report_path = resolve(
        config.get("reports", {}).get("training_metrics_path", "reports/training_metrics.json")
    )
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    return report


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", default="configs/base.yaml")
    arguments = parser.parse_args()
    result = train(load_config(arguments.config))
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
