"""Tests unitaires de `ml/fairness/audit.py` — logique pure, aucune infra requise."""

from __future__ import annotations

import pandas as pd

from ml.fairness.audit import compute_group_metrics, max_gap


def test_compute_group_metrics_identical_groups_have_zero_gap() -> None:
    df = pd.DataFrame(
        {
            "groupe": ["A", "A", "A", "A", "B", "B", "B", "B"],
            "is_grave": [1, 1, 0, 0, 1, 1, 0, 0],
            "pred_grave": [1, 0, 1, 0, 1, 0, 1, 0],
        }
    )

    metrics = compute_group_metrics(df, "groupe")

    assert len(metrics) == 2
    assert max_gap(metrics, "recall") == 0.0
    assert max_gap(metrics, "fpr") == 0.0


def test_compute_group_metrics_detects_a_clear_recall_gap() -> None:
    df = pd.DataFrame(
        {
            "groupe": ["A"] * 4 + ["B"] * 4,
            "is_grave": [1, 1, 1, 1, 1, 1, 1, 1],
            # A : rappel parfait (4/4) ; B : rappel nul (0/4).
            "pred_grave": [1, 1, 1, 1, 0, 0, 0, 0],
        }
    )

    metrics = compute_group_metrics(df, "groupe")

    assert max_gap(metrics, "recall") == 1.0


def test_compute_group_metrics_detects_a_clear_fpr_gap() -> None:
    df = pd.DataFrame(
        {
            "groupe": ["A"] * 4 + ["B"] * 4,
            "is_grave": [0, 0, 0, 0, 0, 0, 0, 0],
            # A : aucun faux positif ; B : tous faux positifs.
            "pred_grave": [0, 0, 0, 0, 1, 1, 1, 1],
        }
    )

    metrics = compute_group_metrics(df, "groupe")

    assert max_gap(metrics, "fpr") == 1.0


def test_max_gap_is_nan_with_fewer_than_two_valid_groups() -> None:
    metrics = pd.DataFrame({"groupe": ["A"], "recall": [0.5]})

    result = max_gap(metrics, "recall")

    assert pd.isna(result)
