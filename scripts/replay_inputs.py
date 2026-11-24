"""Reenvia um JSONL de pedidos a POST /score e resume a telemetria observada."""

from __future__ import annotations

import argparse
import json
import math
from pathlib import Path
from time import monotonic, sleep
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from mlops_lai.monitoring.drift import load_events, valid_scores


def load_rows(path: Path) -> list[dict]:
    rows = []
    with path.open(encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, 1):
            if not line.strip():
                continue
            payload = json.loads(line)
            if "pedido" not in payload:
                raise ValueError(f"linha {line_number} sem campo pedido")
            rows.append(payload)
    return rows


def post_score(url: str, body: dict, timeout: float = 30.0) -> tuple[int, str | None]:
    request = Request(
        url.rstrip("/") + "/score",
        json.dumps(body).encode(),
        {"Content-Type": "application/json"},
    )
    try:
        with urlopen(request, timeout=timeout) as response:
            response.read()
            return response.status, response.headers.get("X-Request-ID")
    except HTTPError as exc:
        exc.read()
        return exc.code, exc.headers.get("X-Request-ID")
    except (URLError, TimeoutError, OSError):
        return -1, None


def score_events(path: Path) -> list[dict]:
    try:
        events = load_events(path)
    except FileNotFoundError:
        return []
    return [event for event in events if event.get("endpoint") == "/score"]


def claim_replay(path: Path) -> None:
    if score_events(path):
        raise ValueError(f"{path} já contém chamadas /score. Use uma rodada nova.")
    is_file = path.is_file() or (not path.exists() and path.suffix == ".jsonl")
    marker = path.with_suffix(".replay-started") if is_file else path / ".replay-started"
    marker.parent.mkdir(parents=True, exist_ok=True)
    try:
        marker.touch(exist_ok=False)
    except FileExistsError as exc:
        raise ValueError(f"{path} já foi reservado para replay. Use uma rodada nova.") from exc


def wait_for_scores(path: Path, request_ids: set[str]) -> list[dict]:
    deadline = monotonic() + 2
    while True:
        events = score_events(path)
        logged_ids = {event.get("request_id") for event in events}
        if logged_ids == request_ids and len(events) == len(request_ids):
            return events
        if logged_ids - request_ids or len(events) > len(request_ids):
            raise ValueError(f"{path} contém eventos que não correspondem ao replay.")
        if monotonic() >= deadline:
            raise ValueError(
                f"{path} contém {len(events)} de {len(request_ids)} respostas esperadas."
            )
        sleep(0.05)


def telemetry_summary(path: Path, request_ids: set[str]) -> dict:
    events = wait_for_scores(path, request_ids)
    successes = valid_scores(events)
    predictions = [
        event
        for event in successes
        if isinstance(event.get("prediction_raw"), (int, float))
        and not isinstance(event["prediction_raw"], bool)
        and math.isfinite(event["prediction_raw"])
        and isinstance(event.get("response"), dict)
        and event["response"]
        and event.get("http_status") == 200
    ]
    if len(predictions) != len(request_ids):
        raise ValueError(f"{path} precisa conter escores válidos e respostas HTTP 200 completas.")
    return {
        "telemetry_path": str(path),
        "score_events": len(events),
        "score_successes": len(successes),
        "prediction_raw_present": len(predictions),
        "orgao_conhecido_true": sum(
            1 for event in successes if (event.get("quality") or {}).get("orgao_conhecido") is True
        ),
        "historico_informado_true": sum(
            1
            for event in successes
            if (event.get("quality") or {}).get("historico_informado") is True
        ),
        "historico_parcial_ignorado_true": sum(
            1
            for event in successes
            if (event.get("quality") or {}).get("historico_parcial_ignorado") is True
        ),
        "idade_numeric": sum(
            1
            for event in successes
            if isinstance((event.get("features") or {}).get("idade"), (int, float))
        ),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--inputs", type=Path, required=True)
    parser.add_argument("--url", default="http://localhost:3000")
    parser.add_argument("--telemetry", type=Path, help="pasta ou arquivo JSONL da corrida")
    parser.add_argument("--timeout", type=float, default=30.0)
    args = parser.parse_args()

    rows = load_rows(args.inputs)
    if not rows:
        parser.error("o arquivo de pedidos está vazio")
    if args.telemetry is not None:
        try:
            claim_replay(args.telemetry)
        except ValueError as exc:
            parser.exit(1, f"{exc}\n")

    results = [post_score(args.url, body, timeout=args.timeout) for body in rows]
    statuses = [status for status, _ in results]

    ok = sum(status == 200 for status in statuses)
    summary = {
        "inputs": str(args.inputs),
        "requests": len(statuses),
        "http_200": ok,
        "non_200": len(statuses) - ok,
        "status_counts": {
            str(code): statuses.count(code) for code in sorted(set(statuses), key=str)
        },
    }
    if args.telemetry is not None and ok == len(rows):
        request_ids = {request_id for _, request_id in results if request_id}
        try:
            if len(request_ids) != ok:
                raise ValueError("o servidor precisa retornar um X-Request-ID único por pedido")
            summary["telemetry"] = telemetry_summary(args.telemetry, request_ids)
        except ValueError as exc:
            summary["telemetry_error"] = str(exc)
    print(json.dumps(summary, indent=2, ensure_ascii=False))
    if ok != len(rows) or "telemetry_error" in summary:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
