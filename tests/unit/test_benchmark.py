"""Tests unitaires de ml/training/benchmark.py (logique pure, sans MLflow ni PostgreSQL)."""

import numpy as np
import polars as pl
import pytest

from ml.training.benchmark import (
    ENRICHED_FLAG_COLUMNS,
    TARGET_RECALL,
    _to_pandas,
    calibrate_threshold,
    feature_columns,
    register_best,
    select_best,
)


def test_feature_columns_baseline_excludes_enriched_flags() -> None:
    categorical, other = feature_columns("baseline")

    assert "luminosite" in categorical
    assert "departement" in categorical
    for flag in ENRICHED_FLAG_COLUMNS:
        assert flag not in other


def test_feature_columns_enriched_adds_flags_on_top_of_baseline() -> None:
    baseline_categorical, baseline_other = feature_columns("baseline")
    enriched_categorical, enriched_other = feature_columns("enriched")

    assert enriched_categorical == baseline_categorical
    assert set(enriched_other) == set(baseline_other) | set(ENRICHED_FLAG_COLUMNS)


def test_feature_columns_rejects_unknown_set() -> None:
    with pytest.raises(ValueError, match="feature_set inconnu"):
        feature_columns("unknown")  # type: ignore[arg-type]


def test_calibrate_threshold_picks_most_permissive_threshold_meeting_target() -> None:
    # Score élevé -> grave, score faible -> non grave : le seuil 0.80 recall doit rester bas
    # (les deux positifs bien séparés se détectent même à un seuil peu exigeant).
    y_true = np.array([0, 0, 0, 1, 1, 1, 1, 1])
    proba = np.array([0.05, 0.10, 0.15, 0.60, 0.65, 0.70, 0.90, 0.95])

    threshold = calibrate_threshold(y_true, proba, TARGET_RECALL)

    predicted = (proba >= threshold).astype(int)
    from sklearn.metrics import recall_score

    assert recall_score(y_true, predicted) >= TARGET_RECALL


def test_calibrate_threshold_falls_back_to_best_recall_when_target_unreachable() -> None:
    # Un des deux positifs a un score si bas qu'aucun seuil balayé (>= 0.01) ne le capture
    # jamais : un recall de 100% est structurellement hors de portée quel que soit le seuil.
    y_true = np.array([1, 1, 0, 0])
    proba = np.array([0.9, 0.001, 0.05, 0.02])

    threshold = calibrate_threshold(y_true, proba, target_recall=1.0)

    from sklearn.metrics import recall_score

    achieved_recall = recall_score(y_true, (proba >= threshold).astype(int))
    assert achieved_recall == pytest.approx(0.5)  # meilleur repli possible : 1 positif sur 2


def test_select_best_prefers_highest_f1_among_recall_eligible() -> None:
    results = [
        {"name": "a", "recall": 0.85, "f1_macro": 0.60},
        {"name": "b", "recall": 0.82, "f1_macro": 0.75},
        {"name": "c", "recall": 0.70, "f1_macro": 0.90},  # sous le seuil recall -> écarté
    ]

    best = select_best(results)

    assert best["name"] == "b"


def test_select_best_falls_back_to_highest_recall_when_none_eligible() -> None:
    results = [
        {"name": "a", "recall": 0.60, "f1_macro": 0.50},
        {"name": "b", "recall": 0.75, "f1_macro": 0.40},
    ]

    best = select_best(results)

    assert best["name"] == "b"


def test_to_pandas_casts_categorical_columns() -> None:
    df = pl.DataFrame({"cat_col": ["a", "b"], "num_col": [1, 2]})

    result = _to_pandas(df, categorical=["cat_col"], other=["num_col"])

    assert str(result["cat_col"].dtype) == "category"
    assert list(result.columns) == ["cat_col", "num_col"]


def test_register_best_returns_none_without_calling_mlflow_when_gates_not_met() -> None:
    """Promotion bloquée sous les seuils CDC (cf. CLAUDE.md) : ne doit rien enregistrer."""
    best = {"name": "logistic_regression", "recall": 0.75, "f1_macro": 0.60, "meets_gates": False}

    assert register_best(best) is None
