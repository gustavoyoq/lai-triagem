"""Recalcula e confere os resultados publicados da AV2 com a API desligada."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from build_monitor_sets import (
    ARTIFACT,
    PRODUCTION_OUT,
    REFERENCE_OUT,
    SEED,
    N,
    build_sets,
    write_jsonl,
)
from monitor_shift import OUTPUT_VAR, analyze
from plot_latency import ARCHIVE, latency_png, load_latency
from replay_inputs import load_rows

LATENCY_METRICS = (
    "min_ms",
    "p25_ms",
    "p50_ms",
    "p75_ms",
    "iqr_ms",
    "p90_ms",
    "p95_ms",
    "p99_ms",
    "max_ms",
)


def reproduce(output: Path) -> None:
    reference, production = build_sets(ARTIFACT, SEED)
    for recorded, generated in [(REFERENCE_OUT, reference), (PRODUCTION_OUT, production)]:
        if generated != load_rows(recorded):
            raise ValueError(f"os pedidos gerados divergem de {recorded.name} em {recorded.parent}")

    latency_path = ARCHIVE / "latency_percentiles.json"
    recorded_latency = json.loads(latency_path.read_text(encoding="utf-8"))
    latency = load_latency(latency_path)
    if not latency["valid"] or latency["requests"] != N or latency["successes"] != N:
        raise ValueError(f"a rodada arquivada precisa conter {N} chamadas HTTP 200")
    rounded_latency = latency | {key: round(latency[key], 4) for key in LATENCY_METRICS}
    for key in LATENCY_METRICS:
        if rounded_latency[key] != recorded_latency[key]:
            raise ValueError(f"latência {key} divergente de {recorded_latency[key]} ms")
    rounded_latency["payload"] = recorded_latency["payload"]

    drift = analyze(ARCHIVE / "reference", ARCHIVE / "production", min_samples=N, expected_count=N)
    recorded_drift = json.loads((ARCHIVE / "drift.json").read_text(encoding="utf-8"))
    summary = drift["summary"]
    for key in ("input_psi", "output_ks_d"):
        if round(summary[key], 4) != round(recorded_drift["summary"][key], 4):
            raise ValueError(f"{key} diverge do resultado arquivado")
    scores = drift["numeric"][OUTPUT_VAR]
    recorded_scores = recorded_drift["numeric"][OUTPUT_VAR]
    for cohort in ("reference", "current"):
        if round(scores[cohort]["median"], 4) != round(recorded_scores[cohort]["median"], 4):
            raise ValueError(f"mediana de {cohort} diverge do resultado arquivado")

    for cohort, rows in [("reference", reference), ("production", production)]:
        write_jsonl(output / cohort / "score_inputs.jsonl", rows)
    for filename, result in [("latency_percentiles.json", rounded_latency), ("drift.json", drift)]:
        (output / filename).write_text(
            json.dumps(result, indent=2, ensure_ascii=False, allow_nan=False) + "\n",
            encoding="utf-8",
        )
    (output / "latency_distribution.png").write_bytes(latency_png(latency))

    print(f"Lotes reproduzidos e conferidos · {N} pedidos em cada lote")
    print(f"HTTP 200 na medição de latência · {latency['successes']}")
    for percentile in (90, 95, 99):
        print(f"p{percentile} = {latency[f'p{percentile}_ms']:.4f} ms")
    print(f"PSI de {summary['input_variable']} = {summary['input_psi']:.4f}")
    print(f"KS D de {summary['output_variable']} = {summary['output_ks_d']:.4f}")
    print(f"Resultados conferidos. Arquivos gerados em {output}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True, help="pasta para os resultados")
    args = parser.parse_args()
    try:
        reproduce(args.output)
    except (OSError, ValueError, KeyError) as exc:
        parser.exit(1, f"Falha na reprodução. {exc}\n")


if __name__ == "__main__":
    main()
