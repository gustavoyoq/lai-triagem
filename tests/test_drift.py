import json

import numpy as np
import pytest

from mlops_lai.monitoring.drift import compare_numeric, load_events, report


def test_legacy_reader_and_compatible_score_psi(tmp_path):
    path = tmp_path / "legacy.jsonl"
    events = [{"score_bin": i % 2, "model_version": "v1"} for i in range(8)]
    path.write_text("".join(json.dumps(e) + "\n" for e in events), encoding="utf-8")
    result = report(path, 4, min_samples=4)
    assert result["score_psi"] == 0
    assert result["events_reference"] == result["events_current"] == 4
    assert result["status"] == "ok"


def test_numeric_comparison_preserves_out_of_reference_values():
    reference = [1.0] * 20
    shifted = [-100.0] * 10 + [100.0] * 10
    result = compare_numeric(reference, shifted)
    assert sum(result["reference_counts"]) == sum(result["current_counts"]) == 20
    assert result["psi"] > 0
    assert result["ks_d"] == 0.5
    assert compare_numeric(reference, reference)["ks_d"] == 0


def test_directory_and_fixed_reference_filter_errors_and_prevent_overlap(tmp_path):
    reference = tmp_path / "reference.jsonl"
    current = tmp_path / "current"
    current.mkdir()
    sample = {
        "schema_version": 2,
        "endpoint": "/score",
        "outcome": "success",
        "model_version": "v1",
        "score_bin": 2,
        "request_id": "reference-id",
        "timestamp_utc": "2026-11-11T10:00:00.000000+00:00",
        "features": {"x": 1.0, "code": 10.0, "default": -1},
        "categorical_features": ["code"],
        "quality": {"default_features": ["default"]},
        "response": {"probabilidade_reencaminhamento": 0.2},
        "input": {"UF": "PE"},
    }
    reference.write_text(json.dumps(sample) + "\n", encoding="utf-8")
    now = sample | {"request_id": "current-id", "input": {"UF": "SP"}}
    error = sample | {"request_id": "error-id", "outcome": "validation_error"}
    (current / "worker.jsonl").write_text(
        json.dumps(now) + "\n" + json.dumps(error) + "\n", encoding="utf-8"
    )
    result = report(current, reference_path=reference)
    assert result["events_current"] == 1
    assert result["status"] == "inconclusive"
    assert result["numeric"]["features.x"]["ks_d"] == 0
    assert "features.code" not in result["numeric"]
    assert "features.default" not in result["numeric"]
    assert result["categorical"]["UF"]["psi"] > 0
    assert len(result["reference_id"]) == 64
    now["request_id"] = "reference-id"
    (current / "worker.jsonl").write_text(json.dumps(now) + "\n", encoding="utf-8")
    with pytest.raises(ValueError, match="em comum"):
        report(current, reference_path=reference)


def test_malformed_lines_and_version_mixtures_are_explicit(tmp_path):
    path = tmp_path / "scoring.jsonl"
    path.write_text('{"unfinished":', encoding="utf-8")
    with pytest.raises(ValueError, match="linha 1"):
        load_events(path)
    path.write_text(
        json.dumps({"score_bin": 2, "model_version": "v1"})
        + "\n"
        + json.dumps({"score_bin": 2, "model_version": "v2"})
        + "\n",
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="mistura"):
        report(path, 1)
    assert np.isfinite(compare_numeric([0, 1], [2, 3])["psi"])
