"""Leitura de telemetria, referência reproduzível e análise de distribuições."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any

import numpy as np

from mlops_lai.monitoring.telemetry import json_safe
from mlops_lai.pipelines.common import resolve


def load_events(path: Path) -> list[dict[str, Any]]:
    """Lê arquivo legado ou diretório com os arquivos append-only de cada worker."""
    files = sorted(path.glob("*.jsonl")) if path.is_dir() else [path]
    if not files or not path.exists():
        raise FileNotFoundError(f"telemetria não encontrada: {path}")
    events = []
    for file in files:
        with file.open(encoding="utf-8") as stream:
            for line_number, line in enumerate(stream, 1):
                if not line.strip():
                    continue
                try:
                    event = json.loads(line)
                except ValueError as exc:
                    raise ValueError(f"JSON inválido em {file.name}, linha {line_number}") from exc
                if not isinstance(event, dict):
                    raise ValueError(f"evento inválido em {file.name}, linha {line_number}")
                events.append(event)
    return sorted(events, key=lambda e: (e.get("timestamp_utc") or "", e.get("request_id") or ""))


def valid_scores(events: list[dict[str, Any]], version: str | None = None) -> list[dict[str, Any]]:
    selected = [
        event
        for event in events
        if (event.get("schema_version", 1) == 1 and "score_bin" in event)
        or (event.get("endpoint") == "/score" and event.get("outcome") == "success")
    ]
    if version:
        selected = [e for e in selected if e.get("model_version") == version]
    ids = [e["request_id"] for e in selected if e.get("request_id")]
    if len(ids) != len(set(ids)):
        raise ValueError("telemetria contém request_ids duplicados")
    versions = {e.get("model_version") for e in selected}
    if len(versions) > 1:
        raise ValueError("telemetria mistura modelos; selecione --model-version")
    return selected


def masses_psi(expected: np.ndarray, actual: np.ndarray) -> float:
    expected = np.clip(expected, 1e-6, None)
    actual = np.clip(actual, 1e-6, None)
    expected, actual = expected / expected.sum(), actual / actual.sum()
    return float(np.sum((actual - expected) * np.log(actual / expected)))


def population_stability_index(reference: np.ndarray, current: np.ndarray) -> float:
    """Calcula PSI usando bins fixos de score; 0.2 é alerta comum, não veredito."""
    if not len(reference) or not len(current):
        raise ValueError("referência e janela atual precisam ter eventos")
    bins = np.arange(-0.5, 10.6, 1.0)
    expected = np.histogram(reference, bins=bins)[0] / len(reference)
    actual = np.histogram(current, bins=bins)[0] / len(current)
    return masses_psi(expected, actual)


def numeric_summary(values: list[float]) -> dict[str, Any]:
    data = np.asarray(values, dtype=float)
    if not len(data):
        return {"n": 0}
    q1, median, q3 = np.quantile(data, [0.25, 0.5, 0.75], method="linear")
    iqr = q3 - q1
    inliers = data[(data >= q1 - 1.5 * iqr) & (data <= q3 + 1.5 * iqr)]
    counts, edges = np.histogram(data, bins=10)
    return json_safe(
        {
            "n": len(data),
            "min": float(data.min()),
            "max": float(data.max()),
            "q1": float(q1),
            "median": float(median),
            "q3": float(q3),
            "iqr": float(iqr),
            "lower_whisker": float(inliers.min()),
            "upper_whisker": float(inliers.max()),
            "outliers": int(len(data) - len(inliers)),
            "histogram": {"edges": edges.tolist(), "counts": counts.tolist()},
        }
    )


def compare_numeric(reference: list[float], current: list[float]) -> dict[str, Any]:
    ref, now = np.asarray(reference), np.asarray(current)
    # Uma grade fixada pela referência, com cobertura das duas caudas.
    inner_edges = np.unique(np.quantile(ref, np.linspace(0, 1, 11)[1:-1]))
    edges = np.concatenate(([-np.inf], inner_edges, [np.inf]))
    ref_counts, _ = np.histogram(ref, bins=edges)
    now_counts, _ = np.histogram(now, bins=edges)
    support = np.union1d(ref, now)
    cdf_ref = np.searchsorted(np.sort(ref), support, side="right") / len(ref)
    cdf_now = np.searchsorted(np.sort(now), support, side="right") / len(now)
    return {
        "ks_d": float(np.max(np.abs(cdf_ref - cdf_now))),
        "psi": masses_psi(ref_counts / len(ref), now_counts / len(now)),
        "psi_edges": ["-Inf", *inner_edges.tolist(), "+Inf"],
        "reference_counts": ref_counts.tolist(),
        "current_counts": now_counts.tolist(),
        "reference": numeric_summary(reference),
        "current": numeric_summary(current),
    }


def distribution_values(events: list[dict[str, Any]]) -> tuple[dict, dict]:
    numeric: dict[str, list[float]] = {}
    categories: dict[str, list[str]] = {}
    for event in events:
        quality = event.get("quality") or {}
        defaults = quality.get("default_features", [])
        for group in ("features", "response"):
            for name, value in (event.get(group) or {}).items():
                if group == "features" and (
                    name in defaults or name in event.get("categorical_features", [])
                ):
                    continue
                key = f"{group}.{name}"
                if (
                    isinstance(value, (int, float))
                    and not isinstance(value, bool)
                    and np.isfinite(value)
                ):
                    numeric.setdefault(key, []).append(float(value))
        for name, value in (event.get("input") or {}).items():
            if isinstance(value, str):
                categories.setdefault(name, []).append(value)
            elif (
                isinstance(value, (int, float))
                and not isinstance(value, bool)
                and np.isfinite(value)
            ):
                numeric.setdefault(f"input.{name}", []).append(float(value))
        raw = event.get("prediction_raw")
        if isinstance(raw, (int, float)) and np.isfinite(raw):
            numeric.setdefault("prediction_raw", []).append(float(raw))
    return numeric, categories


def report(
    path: Path,
    reference_rows: int | None = None,
    *,
    reference_path: Path | None = None,
    model_version: str | None = None,
    min_samples: int = 20,
) -> dict[str, Any]:
    events = valid_scores(load_events(path), model_version)
    if min_samples < 1:
        raise ValueError("min_samples precisa ser positivo")
    if reference_path is not None:
        if reference_path.resolve() == path.resolve():
            raise ValueError("referência e janela atual precisam ter origens distintas")
        reference = valid_scores(load_events(reference_path), model_version)
        current = events
        overlap = {e.get("request_id") for e in reference} & {e.get("request_id") for e in current}
        if overlap - {None}:
            raise ValueError("referência e janela atual possuem request_ids em comum")
    else:
        if reference_rows is None or reference_rows < 1:
            raise ValueError("informe referência fixa ou reference_rows positivo")
        reference, current = events[:reference_rows], events[reference_rows:]
    if not reference or not current:
        raise ValueError("referência e janela atual precisam ter eventos válidos")
    versions = {e.get("model_version") for e in reference + current}
    if len(versions) > 1:
        raise ValueError("referência e janela atual precisam usar a mesma versão")
    reference_id = hashlib.sha256(
        json.dumps(reference, sort_keys=True, ensure_ascii=False).encode("utf-8")
    ).hexdigest()
    psi = population_stability_index(
        np.array([e["score_bin"] for e in reference]),
        np.array([e["score_bin"] for e in current]),
    )
    ref_numeric, ref_cat = distribution_values(reference)
    now_numeric, now_cat = distribution_values(current)
    numeric = {}
    for name in sorted(ref_numeric.keys() & now_numeric.keys()):
        comparison = compare_numeric(ref_numeric[name], now_numeric[name])
        comparison["status"] = (
            "inconclusive"
            if min(len(ref_numeric[name]), len(now_numeric[name])) < min_samples
            else "measured"
        )
        numeric[name] = comparison
    categorical = {}
    for name in sorted(ref_cat.keys() & now_cat.keys()):
        labels = sorted(set(ref_cat[name]) | set(now_cat[name]))
        ref_counts = np.array([ref_cat[name].count(label) for label in labels])
        now_counts = np.array([now_cat[name].count(label) for label in labels])
        categorical[name] = {
            "categories": labels,
            "reference_counts": ref_counts.tolist(),
            "current_counts": now_counts.tolist(),
            "psi": masses_psi(ref_counts / ref_counts.sum(), now_counts / now_counts.sum()),
        }
    sufficient = min(len(reference), len(current)) >= min_samples
    return {
        "reference_id": reference_id,
        "reference_source": str(reference_path or path),
        "reference_selection": "fixed_file" if reference_path else "chronological_prefix",
        "model_version": reference[0].get("model_version"),
        "reference_period": [reference[0].get("timestamp_utc"), reference[-1].get("timestamp_utc")],
        "current_period": [current[0].get("timestamp_utc"), current[-1].get("timestamp_utc")],
        "events_reference": len(reference),
        "events_current": len(current),
        "min_samples": min_samples,
        "score_psi": psi,
        "status": "inconclusive" if not sufficient else ("alert" if psi >= 0.2 else "ok"),
        "score_psi_threshold": 0.2,
        "psi_epsilon": 1e-6,
        "numeric": numeric,
        "categorical": categorical,
        "note": "Limiar 0.2 é exploratório. KS D e PSI descrevem mudança de distribuição.",
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--telemetry", default="monitoring/telemetry")
    reference = parser.add_mutually_exclusive_group(required=True)
    reference.add_argument("--reference-rows", type=int)
    reference.add_argument("--reference")
    parser.add_argument("--model-version")
    parser.add_argument("--min-samples", type=int, default=20)
    arguments = parser.parse_args()
    result = report(
        resolve(arguments.telemetry),
        arguments.reference_rows,
        reference_path=resolve(arguments.reference) if arguments.reference else None,
        model_version=arguments.model_version,
        min_samples=arguments.min_samples,
    )
    output = resolve("reports/drift.json")
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(result, indent=2, allow_nan=False) + "\n", encoding="utf-8")
    print(
        json.dumps(
            {key: value for key, value in result.items() if key not in {"numeric", "categorical"}},
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
