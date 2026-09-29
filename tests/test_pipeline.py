from pathlib import Path

import pandas as pd

from mlops_lai.model.loader import ModelService
from mlops_lai.pipelines.prepare import prepare_frame
from mlops_lai.pipelines.train import causal_organ_rate, train


def synthetic_raw() -> pd.DataFrame:
    rows: list[dict[str, str]] = []
    for year in range(2022, 2027):
        for month in ("01", "04", "08", "11"):
            index = len(rows)
            rows.append(
                {
                    "DataRegistro": f"15/{month}/{year}",
                    "OrgaoDestinatario": "Órgão A" if index % 2 else "Órgão B",
                    "FoiReencaminhado": "Sim" if index % 3 == 0 else "Não",
                    "UF": "PE",
                    "UF_sol": "PE",
                    "DataNascimento": "10/01/1990",
                }
            )
    return pd.DataFrame(rows)


def training_config(processed: Path, artifacts: Path, reports: Path) -> dict[str, object]:
    return {
        "project": {"seed": 42},
        "data": {"processed_path": str(processed), "maturity_days": 60},
        "split": {"train_years": [2022, 2023, 2024], "validation_year": 2025, "test_year": 2026},
        "model": {
            "learning_rate": 0.1,
            "num_leaves": 4,
            "feature_fraction": 1.0,
            "bagging_fraction": 1.0,
            "bagging_freq": 0,
            "min_data_in_leaf": 1,
            "num_boost_round": 10,
            "early_stopping_rounds": 3,
            "queue_fraction": 0.25,
        },
        "artifact": {"output_dir": str(artifacts)},
        "reports": {"training_metrics_path": str(reports / "training.json")},
    }


def test_train_pipeline_writes_servable_artifact(tmp_path: Path) -> None:
    processed = tmp_path / "training.parquet"
    prepared = prepare_frame(synthetic_raw(), "FoiReencaminhado", "DataRegistro")
    prepared.to_parquet(processed, index=False)
    artifacts = tmp_path / "artifacts"
    result = train(training_config(processed, artifacts, tmp_path / "reports"))
    model = ModelService(artifacts)
    score = model.score({"OrgaoDestinatario": "Órgão A", "DataRegistro": "15/09/2026"})
    assert (artifacts / "manifest.json").is_file()
    assert result["model_version"] == model.version
    assert 0 <= score["probabilidade_reencaminhamento"] <= 1


def test_causal_rate_excludes_same_day_and_unmatured_labels() -> None:
    frame = pd.DataFrame(
        {
            "OrgaoDestinatario": ["A", "A", "A"],
            "_registered_at": pd.to_datetime(["2024-01-01", "2024-01-01", "2024-04-01"]),
            "target": [1, 0, 0],
        }
    )
    values = causal_organ_rate(frame, base_rate=0.5, maturity_days=60, prior=2.0)
    assert values.iloc[0] == values.iloc[1] == 0.5
    assert values.iloc[2] == 0.5
