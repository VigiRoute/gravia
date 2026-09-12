"""Tests unitaires de `ml/monitoring/drift.py` — logique pure, aucune infra requise (Evidently
calcule le PSI en local, sans réseau)."""

from __future__ import annotations

import numpy as np
import pandas as pd

from ml.monitoring.drift import (
    _split_numeric_boolean,
    compute_feature_drift,
    compute_prediction_drift,
)


def test_split_numeric_boolean_separates_by_dtype() -> None:
    frame = pd.DataFrame(
        {"vitesse_max": [50.0, 90.0], "agglomeration": [True, False], "nb_voies": [2.0, 1.0]}
    )

    numeric, boolean = _split_numeric_boolean(frame, ["vitesse_max", "agglomeration", "nb_voies"])

    assert set(numeric) == {"vitesse_max", "nb_voies"}
    assert boolean == ["agglomeration"]


def test_compute_feature_drift_flags_a_clearly_shifted_column() -> None:
    rng = np.random.default_rng(42)
    reference = pd.DataFrame(
        {
            "vitesse_max": rng.normal(50, 5, 500),
            "meteo": pd.Categorical(rng.choice(["1", "2"], 500)),
        }
    )
    current = pd.DataFrame(
        {
            "vitesse_max": rng.normal(130, 5, 500),  # distribution totalement décalée
            "meteo": pd.Categorical(rng.choice(["1", "2"], 500)),  # même distribution
        }
    )

    psi_by_column = compute_feature_drift(
        reference, current, categorical=["meteo"], other=["vitesse_max"]
    )

    assert psi_by_column["vitesse_max"] >= 0.2
    assert psi_by_column["meteo"] < 0.2


def test_compute_prediction_drift_is_near_zero_for_identical_distributions() -> None:
    rng = np.random.default_rng(0)
    proba = pd.Series(rng.uniform(0, 1, 500))

    psi = compute_prediction_drift(proba, proba.copy())

    assert psi < 0.01
