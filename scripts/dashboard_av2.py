"""Painel de latência e de mudança de distribuição da AV2."""

import argparse
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd
import streamlit as st
from monitor_shift import INPUT_VAR, OUTPUT_VAR, analyze
from plot_latency import ARCHIVE, latency_png, load_latency


def number(value: float, places: int = 2) -> str:
    return f"{value:.{places}f}".replace(".", ",")


def show_latency(path: Path) -> None:
    try:
        result = load_latency(path)
    except (OSError, ValueError) as exc:
        st.error(f"Latência indisponível. {exc}")
        return

    st.subheader("Tempo de resposta")
    st.caption(
        f"{result['requests']} chamadas sequenciais · {result['successes']} HTTP 200 · "
        f"{result['failures']} falhas"
    )
    if result["valid"]:
        for column, p in zip(st.columns(3), (90, 95, 99), strict=True):
            column.metric(f"p{p}", f"{number(result[f'p{p}_ms'])} ms")
        relation = "abaixo" if result["sla_p95_met"] else "no limite ou acima"
        st.write(f"O p95 ficou {relation} do limite de exemplo de {result['sla_p95_ms']:g} ms.")
        chart = latency_png(result)
        st.image(chart, width="stretch")
        st.caption(
            f"A caixa contém os 50% centrais e a mediana é {number(result['p50_ms'])} ms. "
            "Os círculos marcam observações fora de 1,5 × IQR. No violino, a largura "
            "representa a densidade estimada e os traços na base marcam as chamadas."
        )
        st.download_button("Baixar gráficos", chart, "latency_distribution.png", "image/png")
    else:
        st.warning(result["error"])

    started = result["started_at_utc"]
    if isinstance(started, str):
        try:
            date = datetime.fromisoformat(started).astimezone(ZoneInfo("America/Sao_Paulo"))
            st.caption(f"Coleta em {date:%d/%m/%Y às %H:%M:%S}, horário de Brasília.")
        except ValueError:
            st.caption(f"Início da coleta (UTC) · {started}")

    samples = pd.DataFrame(result["samples"]).rename(
        columns={"i": "Chamada", "status": "Status", "latency_ms": "Latência (ms)"}
    )
    st.subheader("Chamadas individuais")
    st.dataframe(samples.astype({"Status": str}), hide_index=True, width="stretch")
    st.download_button(
        "Baixar chamadas em CSV", samples.to_csv(index=False), "latency_samples.csv", "text/csv"
    )


def show_shift(run: Path) -> None:
    try:
        result = analyze(run / "reference", run / "production", min_samples=100)
    except (OSError, ValueError, KeyError) as exc:
        st.error(f"Comparação indisponível. {exc}")
        return

    summary = result["summary"]
    st.caption(
        f"Modelo {summary['model_version']} · {summary['events_reference']} respostas de "
        f"referência · {summary['events_current']} respostas de produção simulada"
    )
    input_col, output_col = st.columns(2)
    with input_col:
        st.subheader("Órgão destinatário")
        if summary["input_status"] == "measured":
            st.metric("PSI · entrada categórica", number(summary["input_psi"], 4))
            relation = "atingiu" if summary["input_psi_alert"] else "ficou abaixo de"
            threshold = number(summary["input_psi_threshold"], 1)
            st.write(f"O PSI {relation} {threshold}, limiar exploratório da entrada.")
        else:
            st.warning("O cálculo de PSI exige 100 observações do órgão por lote.")
        organs = result["categorical"][INPUT_VAR]
        st.dataframe(
            {
                "Órgão": organs["categories"],
                "Referência": organs["reference_counts"],
                "Produção": organs["current_counts"],
            },
            hide_index=True,
            width="stretch",
        )
    with output_col:
        st.subheader("Probabilidade de reencaminhamento")
        if summary["output_status"] == "measured":
            distance = summary["output_ks_d"]
            st.metric("KS D · saída contínua", number(distance, 4))
            st.write(
                f"A maior diferença entre as distribuições acumuladas foi de "
                f"{number(100 * distance, 1)} pontos percentuais."
            )
        else:
            st.warning("O cálculo de KS exige 100 escores válidos por lote.")
        scores = result["numeric"][OUTPUT_VAR]
        quantiles = ("q1", "median", "q3")
        st.dataframe(
            {
                "Escore bruto": ["p25", "Mediana", "p75"],
                "Referência": [number(scores["reference"][q], 4) for q in quantiles],
                "Produção": [number(scores["current"][q], 4) for q in quantiles],
            },
            hide_index=True,
            width="stretch",
        )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-dir", type=Path, default=ARCHIVE)
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    run = root / args.run_dir

    st.set_page_config(page_title="AV2 · Triagem LAI", layout="wide")
    st.title("Monitoramento da triagem LAI")
    st.caption(f"POST /score · Rodada {args.run_dir}")
    latency, shift = st.tabs(["Latência", "Referência e produção"])
    with latency:
        show_latency(run / "latency_percentiles.json")
    with shift:
        show_shift(run)


if __name__ == "__main__":
    main()
