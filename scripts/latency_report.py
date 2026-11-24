"""Envia pedidos sequenciais a POST /score e reporta percentis empíricos de latência."""

from __future__ import annotations

import argparse
import json
import statistics
from datetime import UTC, datetime
from pathlib import Path
from time import perf_counter
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

PEDIDO = {
    "OrgaoDestinatario": "CC-PR – Casa Civil da Presidência da República",
    "DataRegistro": "15/09/2026",
}
SLA_P95_MS = 400.0
DEFAULT_OUTPUT = Path("reports/latency_percentiles.json")


def send_one(url: str, body: bytes, timeout: float = 30.0) -> dict:
    request = Request(url.rstrip("/") + "/score", body, {"Content-Type": "application/json"})
    started = perf_counter()
    try:
        with urlopen(request, timeout=timeout) as response:
            response.read()
            status: int | str = response.status
    except HTTPError as exc:
        exc.read()
        status = exc.code
    except TimeoutError:
        status = "timeout"
    except (URLError, OSError):
        status = "connection_error"
    latency_ms = (perf_counter() - started) * 1000
    return {"status": status, "latency_ms": latency_ms}


def summarize(rows: list[dict]) -> dict:
    ok = [row["latency_ms"] for row in rows if row["status"] == 200]
    failures = len(rows) - len(ok)
    report: dict = {
        "endpoint": "/score",
        "method": "POST",
        "requests": len(rows),
        "successes": len(ok),
        "failures": failures,
        "sequential": True,
        "quantile_method": "statistics.quantiles(n=100, method='inclusive')",
        "sla_p95_ms": SLA_P95_MS,
        "samples": [
            {"i": i + 1, "status": row["status"], "latency_ms": row["latency_ms"]}
            for i, row in enumerate(rows)
        ],
    }
    if len(ok) < 100:
        report["valid"] = False
        report["error"] = f"amostra insuficiente: {len(ok)} sucessos HTTP 200 (mínimo 100)"
        return report

    ordered = sorted(ok)
    cuts = statistics.quantiles(ordered, n=100, method="inclusive")
    # cuts[k-1] ≈ percentil k; Q1=p25, Q2=p50, Q3=p75
    p25, p50, p75 = cuts[24], cuts[49], cuts[74]
    p90, p95, p99 = cuts[89], cuts[94], cuts[98]
    report.update(
        {
            "valid": True,
            "min_ms": ordered[0],
            "p25_ms": p25,
            "p50_ms": p50,
            "p75_ms": p75,
            "iqr_ms": p75 - p25,
            "p90_ms": p90,
            "p95_ms": p95,
            "p99_ms": p99,
            "max_ms": ordered[-1],
            "sla_p95_met": p95 < SLA_P95_MS,
        }
    )
    return report


def format_report(report: dict) -> str:
    lines = [
        f"endpoint: {report['endpoint']} ({report['method']})",
        f"requests: {report['requests']} sequenciais",
        f"successes_200: {report['successes']}",
        f"failures: {report['failures']}",
    ]
    if not report.get("valid"):
        lines.append(report.get("error", "relatório inválido"))
        return "\n".join(lines)

    lines.extend(
        [
            f"min_ms: {report['min_ms']:.4f}",
            f"p25_ms (Q1): {report['p25_ms']:.4f}",
            f"p50_ms (Q2/mediana): {report['p50_ms']:.4f}",
            f"p75_ms (Q3): {report['p75_ms']:.4f}",
            f"iqr_ms (Q3-Q1): {report['iqr_ms']:.4f}",
            f"p90_ms: {report['p90_ms']:.4f}",
            f"p95_ms: {report['p95_ms']:.4f}",
            f"p99_ms: {report['p99_ms']:.4f}",
            f"max_ms: {report['max_ms']:.4f}",
            (
                f"Em {report['successes']} respostas HTTP 200 de POST /score, medidas no cliente "
                f"em sequência, os quartis foram Q1={report['p25_ms']:.2f} ms, "
                f"mediana/Q2={report['p50_ms']:.2f} ms e Q3={report['p75_ms']:.2f} ms "
                f"(IQR={report['iqr_ms']:.2f} ms); 90% chegaram em até {report['p90_ms']:.2f} ms "
                f"(p90), 95% em até {report['p95_ms']:.2f} ms (p95) e 99% em até "
                f"{report['p99_ms']:.2f} ms (p99). "
                f"Um SLA de p95 < {SLA_P95_MS:.0f} ms "
                f"{'seria cumprido' if report['sla_p95_met'] else 'não seria cumprido'} "
                f"nesta máquina e nesta carga (p95 = {report['p95_ms']:.2f} ms). "
                "O p99 depende das observações mais lentas e pode variar entre corridas."
            ),
        ]
    )
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", default="http://localhost:3000")
    parser.add_argument("--requests", type=int, default=100)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--timeout", type=float, default=30.0)
    args = parser.parse_args()
    if args.requests < 100:
        parser.error("--requests precisa ser >= 100")

    body = json.dumps({"pedido": PEDIDO}).encode()
    started_at = datetime.now(UTC)
    rows = [send_one(args.url, body, timeout=args.timeout) for _ in range(args.requests)]
    report = summarize(rows)
    report["started_at_utc"] = started_at.isoformat()
    report["payload"] = {"pedido": PEDIDO}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    for key in (
        "min_ms",
        "p25_ms",
        "p50_ms",
        "p75_ms",
        "iqr_ms",
        "p90_ms",
        "p95_ms",
        "p99_ms",
        "max_ms",
    ):
        if key in report and isinstance(report[key], float):
            report[key] = round(report[key], 4)
    args.output.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(format_report(report))
    print(f"wrote: {args.output}")
    if not report.get("valid"):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
