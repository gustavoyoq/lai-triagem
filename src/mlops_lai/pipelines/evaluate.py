"""Avalia exatamente o caminho de inferência que será servido."""

from __future__ import annotations

import argparse
import json
from typing import Any

import numpy as np
import pandas as pd
from sklearn.metrics import average_precision_score, roc_auc_score

from mlops_lai.features.preprocessor import REQUEST_FIELDS
from mlops_lai.metrics import precision_at_k
from mlops_lai.model.loader import ModelService
from mlops_lai.pipelines.common import load_config, resolve


def payload_from_row(row: pd.Series, history_fields: list[str]) -> dict[str, Any]:
    payload = {
        field: row[field]
        for field in REQUEST_FIELDS.intersection(row.index)
        if pd.notna(row[field]) and field not in history_fields
    }
    history = {field: row[field] for field in history_fields if field in row and row[field] >= 0}
    if len(history) == len(history_fields):
        payload.update(history)
    return payload


def evaluate(config: dict[str, Any], artifact_dir: str | None = None) -> dict[str, Any]:
    frame = pd.read_parquet(resolve(config["data"]["processed_path"]))
    test = frame.loc[frame["year"] == config["split"]["test_year"]]
    if test.empty:
        raise ValueError("não há amostras no ano de teste")
    model = ModelService(resolve(artifact_dir or config["artifact"]["output_dir"]))
    scores = np.array(
        [
            model.score(payload_from_row(row, model.preprocessor.caller_features))[
                "probabilidade_reencaminhamento"
            ]
            for _, row in test.iterrows()
        ]
    )
    target = test["target"].to_numpy()
    result = {
        "model_version": model.version,
        "rows": int(len(test)),
        "roc_auc": float(roc_auc_score(target, scores)) if len(np.unique(target)) > 1 else None,
        "pr_auc": float(average_precision_score(target, scores))
        if len(np.unique(target)) > 1
        else None,
        "precision_at_queue": precision_at_k(
            target, scores, float(config["model"]["queue_fraction"])
        ),
    }
    output = resolve(config.get("reports", {}).get("evaluation_path", "reports/evaluation.json"))
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    return result


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", default="configs/base.yaml")
    parser.add_argument("--artifact-dir")
    arguments = parser.parse_args()
    print(json.dumps(evaluate(load_config(arguments.config), arguments.artifact_dir), indent=2))


if __name__ == "__main__":
    main()
