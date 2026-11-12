"""Gera carga determinística de sucessos e rejeições usando a biblioteca padrão."""

from __future__ import annotations

import argparse
import json
import random
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from time import perf_counter
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

BASE = {
    "OrgaoDestinatario": "CC-PR – Casa Civil da Presidência da República",
    "DataRegistro": "15/09/2026",
}
HISTORY = {
    "n_pedidos_previos": 80,
    "prev_reenc_solicitante": 3,
    "prev_reenc_solicitante_den": 80,
    "n_pedidos_previos_neste_orgao": 12,
    "prev_reenc_neste_orgao": 0,
    "prev_reenc_neste_orgao_den": 12,
    "n_orgaos_distintos_previos": 14,
    "dias_desde_ultimo_pedido": 5,
}


def scenarios() -> list[tuple[str, str, bytes]]:
    cases = [
        ("high_risk", "/score", BASE),
        ("low_risk", "/score", BASE | {"OrgaoDestinatario": "Prefeitura"}),
        ("unknown", "/score", BASE | {"OrgaoDestinatario": "Órgão sintético desconhecido"}),
        (
            "history",
            "/score",
            BASE
            | HISTORY
            | {
                "UF": "PE",
                "UF_sol": "PE",
                "Escolaridade": "Ensino Fundamental",
                "DataNascimento": "10/01/1990",
                "Municipio_sol": "Recife",
            },
        ),
        ("partial", "/score", BASE | {"n_pedidos_previos": 7}),
        ("baseline", "/score_baseline", BASE),
        ("invalid_date", "/score", BASE | {"DataRegistro": "31/02/2026"}),
        ("invalid_type", "/score", BASE | {"n_pedidos_previos": "inválido"}),
        ("missing", "/score", {"DataRegistro": "15/09/2026"}),
        ("extra", "/score", BASE | {"campo_extra": "ignorar"}),
        ("leakage", "/score", BASE | {"FoiReencaminhado": "Sim"}),
    ]
    result = [(name, route, json.dumps({"pedido": p}).encode()) for name, route, p in cases]
    return result + [("invalid_json", "/score", b'{"pedido":')]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", default="http://localhost:3000")
    parser.add_argument("--requests", type=int, default=120)
    parser.add_argument("--concurrency", type=int, default=4)
    parser.add_argument("--seed", type=int, default=42)
    args = parser.parse_args()
    if args.requests < 1 or args.concurrency < 1:
        parser.error("requests e concurrency precisam ser positivos")
    cases = scenarios()
    jobs = [cases[i % len(cases)] for i in range(args.requests)]
    random.Random(args.seed).shuffle(jobs)

    def send(job: tuple[str, str, bytes]) -> tuple[str, str]:
        name, route, body = job
        request = Request(args.url.rstrip("/") + route, body, {"Content-Type": "application/json"})
        try:
            with urlopen(request, timeout=30) as response:
                response.read()
                return name, str(response.status)
        except HTTPError as exc:
            exc.read()
            return name, str(exc.code)
        except (URLError, TimeoutError, OSError):
            return name, "connection_error"

    started = perf_counter()
    with ThreadPoolExecutor(max_workers=args.concurrency) as pool:
        results = list(pool.map(send, jobs))
    summary = {
        "requests": args.requests,
        "concurrency": args.concurrency,
        "seed": args.seed,
        "elapsed_seconds": round(perf_counter() - started, 3),
        "statuses": dict(Counter(status for _, status in results)),
        "scenarios": dict(Counter(name for name, _ in results)),
    }
    print(json.dumps(summary, indent=2))
    if any(status == "connection_error" or status.startswith("5") for _, status in results):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
