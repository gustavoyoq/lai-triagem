"""Métricas determinísticas para uma fila operacional com empates."""

from __future__ import annotations

import numpy as np


def precision_at_k(y_true: np.ndarray, scores: np.ndarray, fraction: float) -> float:
    """Precisão esperada no topo, sem depender da ordem das linhas empatadas."""
    if not 0 < fraction <= 1:
        raise ValueError("fraction deve estar em (0, 1]")
    if len(y_true) == 0 or len(y_true) != len(scores):
        raise ValueError("y_true e scores devem ter mesmo tamanho não vazio")
    k = min(max(1, round(fraction * len(y_true))), len(y_true))
    order = np.argsort(-scores, kind="stable")
    ranked_scores, ranked_labels = scores[order], y_true[order]
    cutoff = ranked_scores[k - 1]
    above = ranked_scores > cutoff
    ties = ranked_scores == cutoff
    expected_positives = ranked_labels[above].sum()
    expected_positives += (k - above.sum()) * ranked_labels[ties].mean()
    return float(expected_positives / k)
