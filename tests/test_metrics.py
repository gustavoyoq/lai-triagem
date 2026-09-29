import numpy as np
import pytest

from mlops_lai.metrics import precision_at_k


def test_precision_at_k_is_invariant_to_tie_order() -> None:
    labels = np.array([1, 0, 1, 0])
    scores = np.array([0.9, 0.8, 0.8, 0.1])
    first = precision_at_k(labels, scores, 0.5)
    second = precision_at_k(labels[[0, 2, 1, 3]], scores[[0, 2, 1, 3]], 0.5)
    assert first == second == 0.75


def test_precision_at_k_rejects_invalid_fraction() -> None:
    with pytest.raises(ValueError):
        precision_at_k(np.array([1]), np.array([0.5]), 0)
