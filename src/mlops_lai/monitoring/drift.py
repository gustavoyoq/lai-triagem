"""Relatório PSI sobre a distribuição agregada de escores, sem PII."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np
import pandas as pd

from mlops_lai.pipelines.common import resolve


def population_stability_index(reference: np.ndarray, current: np.ndarray) -> float:
    """Calcula PSI usando bins fixos de score; 0.2 é alerta comum, não veredito."""
    if not len(reference) or not len(current):
        raise ValueError("referência e janela atual precisam ter eventos")
    bins = np.arange(-0.5, 10.6, 1.0)
    expected = np.histogram(reference, bins=bins)[0] / len(reference)
    actual = np.histogram(current, bins=bins)[0] / len(current)
    expected, actual = np.clip(expected, 1e-6, None), np.clip(actual, 1e-6, None)
    return float(np.sum((actual - expected) * np.log(actual / expected)))


def report(path: Path, reference_rows: int) -> dict[str, object]:
    if not path.exists():
        raise FileNotFoundError(f"telemetria não encontrada: {path}")
    events = pd.read_json(path, lines=True)
    if len(events) <= reference_rows:
        raise ValueError("a janela atual precisa conter ao menos um evento")
    psi = population_stability_index(
        events["score_bin"].iloc[:reference_rows], events["score_bin"].iloc[reference_rows:]
    )
    return {
        "events_reference": reference_rows,
        "events_current": int(len(events) - reference_rows),
        "score_psi": psi,
        "status": "alert" if psi >= 0.2 else "ok",
        "note": "PSI é um sinal para investigação, não um gatilho automático de retreino.",
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--telemetry", default="monitoring/telemetry/scoring.jsonl")
    parser.add_argument("--reference-rows", type=int, required=True)
    arguments = parser.parse_args()
    result = report(resolve(arguments.telemetry), arguments.reference_rows)
    output = resolve("reports/drift.json")
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
