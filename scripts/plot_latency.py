"""Recalcula os percentis e desenha a distribuição a partir do JSON de latência."""

from __future__ import annotations

import argparse
import json
import math
from io import BytesIO
from pathlib import Path

from latency_report import summarize
from matplotlib.figure import Figure
from matplotlib.ticker import FuncFormatter, LogLocator

ARCHIVE = Path("reports/av2")


def load_latency(path: Path) -> dict:
    recorded = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(recorded, dict) or (recorded.get("method"), recorded.get("endpoint")) != (
        "POST", "/score"
    ):
        raise ValueError("esperado um relatório de latência de POST /score")
    samples = recorded.get("samples")
    if not isinstance(samples, list) or not samples:
        raise ValueError("o relatório de latência não contém amostras")
    for i, row in enumerate(samples, 1):
        if not isinstance(row, dict) or "status" not in row:
            raise ValueError(f"amostra {i} sem status")
        value = row.get("latency_ms")
        if type(value) not in (int, float) or not math.isfinite(value) or value < 0:
            raise ValueError(f"tempo inválido na amostra {i}")
    report = summarize(samples)
    report["started_at_utc"] = recorded.get("started_at_utc")
    return report


def latency_png(report: dict) -> bytes:
    times = [row["latency_ms"] for row in report["samples"] if row["status"] == 200]
    figure = Figure(figsize=(9.5, 3.9), layout="constrained", facecolor="white")
    box, violin = figure.subplots(2, 1, sharex=True)
    ink, blue, amber = "#163248", "#147c8b", "#b45309"

    box.boxplot(
        times,
        orientation="horizontal",
        widths=0.56,
        whis=1.5,
        patch_artist=True,
        boxprops={"facecolor": "#d6eef0", "edgecolor": blue, "linewidth": 1.5},
        medianprops={"color": ink, "linewidth": 2.2},
        whiskerprops={"color": blue, "linewidth": 1.4},
        capprops={"color": blue, "linewidth": 1.4},
        flierprops={"marker": "o", "markerfacecolor": "white", "markeredgecolor": amber},
    )
    box.set_title("Boxplot", loc="left", color=ink, fontweight="bold")

    density = violin.violinplot(
        times, orientation="horizontal", points=512, widths=0.75,
        showextrema=False, showmedians=True,
    )
    for body in density["bodies"]:
        body.set(facecolor="#9bd3d8", edgecolor=blue, linewidth=1.4, alpha=1)
    density["cmedians"].set(color=ink, linewidth=2.2)
    violin.vlines(times, 0.49, 0.56, color=blue, alpha=0.55, linewidth=0.8)
    violin.set_title("Gráfico de violino", loc="left", color=ink, fontweight="bold")

    log_axis = min(times) > 0 and max(times) / min(times) >= 10
    if log_axis:
        violin.set_xscale("log")
        violin.xaxis.set_major_locator(LogLocator(base=10, subs=(1, 2, 5)))
        violin.xaxis.set_major_formatter(FuncFormatter(lambda value, _: f"{value:g}"))
        violin.xaxis.set_minor_formatter(FuncFormatter(lambda *_: ""))
    violin.set_xlabel(f"Tempo de resposta (ms){' · escala logarítmica' if log_axis else ''}")

    p95 = report["p95_ms"]
    label = f"p95  {p95:.2f} ms".replace(".", ",")
    for axis in (box, violin):
        axis.axvline(p95, color=amber, linestyle="--", linewidth=1.4)
        axis.set(yticks=[], ylim=(0.44, 1.56), axisbelow=True)
        axis.grid(axis="x", color="#e0e8ed")
        axis.spines[["top", "left", "right"]].set_visible(False)
        axis.spines["bottom"].set_color("#b4c3cd")
        axis.tick_params(axis="x", colors=ink)
        axis.margins(x=0.09)
    box.annotate(
        label, (p95, 1), xycoords=("data", "axes fraction"), xytext=(5, -2),
        textcoords="offset points", va="top", color=amber, fontsize=9,
    )

    output = BytesIO()
    figure.savefig(output, format="png", dpi=160)
    return output.getvalue()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, default=ARCHIVE / "latency_percentiles.json")
    parser.add_argument("--output", type=Path, default=ARCHIVE / "latency_distribution.png")
    args = parser.parse_args()
    try:
        report = load_latency(args.input)
        if not report["valid"]:
            raise ValueError(report["error"])
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_bytes(latency_png(report))
    except (OSError, ValueError) as exc:
        parser.exit(1, f"{exc}\n")
    print(args.output)


if __name__ == "__main__":
    main()
