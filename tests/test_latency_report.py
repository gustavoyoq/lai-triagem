from __future__ import annotations

import importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "latency_report", ROOT / "scripts" / "latency_report.py"
)
assert SPEC is not None and SPEC.loader is not None
latency_report = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(latency_report)


def test_summarize_inclusive_percentiles_on_1_to_100() -> None:
    rows = [{"status": 200, "latency_ms": float(i)} for i in range(1, 101)]
    report = latency_report.summarize(rows)
    assert report["valid"] is True
    assert report["successes"] == 100
    assert report["failures"] == 0
    assert report["p25_ms"] == 25.75
    assert report["p50_ms"] == 50.5
    assert report["p75_ms"] == 75.25
    assert report["iqr_ms"] == 49.5
    assert report["p90_ms"] == 90.1
    assert report["p95_ms"] == 95.05
    assert report["p99_ms"] == 99.01
    assert report["min_ms"] == 1.0
    assert report["max_ms"] == 100.0
    assert report["sla_p95_met"] is True


def test_summarize_excludes_non_200_from_percentiles() -> None:
    rows = [{"status": 200, "latency_ms": float(i)} for i in range(1, 101)]
    rows.append({"status": 500, "latency_ms": 9999.0})
    report = latency_report.summarize(rows)
    assert report["valid"] is True
    assert report["successes"] == 100
    assert report["failures"] == 1
    assert report["p99_ms"] == 99.01
    assert report["max_ms"] == 100.0


def test_summarize_rejects_fewer_than_100_successes() -> None:
    rows = [{"status": 200, "latency_ms": float(i)} for i in range(1, 100)]
    report = latency_report.summarize(rows)
    assert report["valid"] is False
    assert report["successes"] == 99
    assert "p95_ms" not in report


def test_summarize_keeps_original_sample_precision() -> None:
    latency_ms = 0.123456789
    report = latency_report.summarize([{"status": 200, "latency_ms": latency_ms}] * 100)
    assert report["samples"][0]["latency_ms"] == latency_ms
