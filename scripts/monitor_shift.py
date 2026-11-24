"""Compara telemetria de referência e produção: PSI da entrada e KS da saída.

Par monitorado (Parte 1 / requisito 3):
- entrada categórica OrgaoDestinatario → Population Stability Index (PSI)
- saída contínua prediction_raw → Distância de Kolmogorov-Smirnov (KS)

Reusa mlops_lai.monitoring.drift.report; não redefine a fórmula.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from mlops_lai.monitoring.drift import report
from mlops_lai.pipelines.common import resolve

INPUT_VAR = "OrgaoDestinatario"
OUTPUT_VAR = "prediction_raw"
PSI_THRESHOLD = 0.2


def summarize(result: dict) -> dict:
    categorical = result.get("categorical") or {}
    numeric = result.get("numeric") or {}
    if INPUT_VAR not in categorical:
        raise KeyError(f"PSI de {INPUT_VAR} ausente no relatório de drift")
    if OUTPUT_VAR not in numeric:
        raise KeyError(f"KS de {OUTPUT_VAR} ausente no relatório de drift")

    organ = categorical[INPUT_VAR]
    score = numeric[OUTPUT_VAR]
    psi = float(organ["psi"])
    ks = float(score["ks_d"])
    n_ref = int(result["events_reference"])
    n_cur = int(result["events_current"])
    min_samples = result["min_samples"]
    organ_counts = (sum(organ["reference_counts"]), sum(organ["current_counts"]))
    score_counts = (score["reference"]["n"], score["current"]["n"])
    input_status = "measured" if min(organ_counts) >= min_samples else "inconclusive"
    output_status = score["status"]
    psi_alert = None if input_status == "inconclusive" else psi >= PSI_THRESHOLD
    if psi_alert is None:
        psi_reading = f"inconclusivo com mínimo de {min_samples} observações por lote"
    else:
        psi_reading = f"{'alerta' if psi_alert else 'abaixo do alerta'} no limiar {PSI_THRESHOLD}"
    ks_reading = "inconclusivo" if output_status == "inconclusive" else "medido"
    return {
        "input_variable": INPUT_VAR,
        "input_metric": "PSI",
        "input_psi": psi,
        "input_psi_threshold": PSI_THRESHOLD,
        "input_psi_alert": psi_alert,
        "input_status": input_status,
        "input_counts": list(organ_counts),
        "output_variable": OUTPUT_VAR,
        "output_metric": "KS",
        "output_ks_d": ks,
        "output_status": output_status,
        "output_counts": list(score_counts),
        "events_reference": n_ref,
        "events_current": n_cur,
        "model_version": result.get("model_version"),
        "reading": (
            f"Foram lidas {n_ref} respostas de referência e {n_cur} de produção. "
            f"O PSI da distribuição de {INPUT_VAR} foi {psi:.4f} ({psi_reading}). "
            f"O KS de {OUTPUT_VAR} foi {ks:.4f} ({ks_reading}). "
            f"A diferença máxima entre as curvas acumuladas dos escores foi {ks:.1%}."
        ),
    }


def analyze(
    reference: str | Path,
    current: str | Path,
    *,
    min_samples: int,
    expected_count: int | None = None,
) -> dict:
    result = report(resolve(current), reference_path=resolve(reference), min_samples=min_samples)
    summary = summarize(result)
    if expected_count is not None:
        expected = [expected_count] * 2
        counts = [summary["events_reference"], summary["events_current"]]
        if (
            counts != expected
            or summary["input_counts"] != expected
            or summary["output_counts"] != expected
        ):
            raise ValueError(
                f"esperadas exatamente {expected_count} respostas completas por lote; "
                f"respostas={counts}, PSI={summary['input_counts']}, KS={summary['output_counts']}"
            )
    result["reference_source"] = str(reference)
    result["current_source"] = str(current)
    result["summary"] = summary
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--reference",
        required=True,
        help="pasta JSONL da corrida de referência",
    )
    parser.add_argument(
        "--current",
        required=True,
        help="pasta JSONL da corrida de produção",
    )
    parser.add_argument("--output", type=Path, default=Path("reports/drift.json"))
    parser.add_argument("--min-samples", type=int, default=20)
    parser.add_argument("--expected-count", type=int, help="quantidade exata de respostas por lote")
    args = parser.parse_args()

    try:
        result = analyze(
            args.reference,
            args.current,
            min_samples=args.min_samples,
            expected_count=args.expected_count,
        )
    except (OSError, ValueError, KeyError) as exc:
        parser.exit(1, f"{exc}\n")

    output = resolve(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps(result, indent=2, ensure_ascii=False, allow_nan=False) + "\n", encoding="utf-8"
    )

    print(json.dumps(result["summary"], indent=2, ensure_ascii=False))
    print(f"wrote: {output}")


if __name__ == "__main__":
    main()
