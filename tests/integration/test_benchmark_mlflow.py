"""Test d'intégration de ml/training/benchmark.py contre un MLflow réel.

Utilise un jeu de données synthétique minuscule (~300 lignes, pas les 273 226 accidents réels)
pour rester rapide : ce test vérifie la mécanique MLflow (tracking, log de modèle, registry), pas
la qualité du modèle — celle-ci est vérifiée manuellement (cf. docstring du module : reproduction
du baseline publié à 0,001 près) et entraîner sur les données réelles dans une suite de tests
automatisée prendrait plusieurs minutes (constaté : Random Forest à 300 arbres sur 163k lignes).

Isolé de l'expérience et du modèle enregistré réels via `EXPERIMENT_NAME`/`REGISTERED_MODEL_NAME`
monkeypatchés sur des noms de test ; le run et la version enregistrée sont supprimés en sortie
pour ne pas polluer le tracking MLflow partagé de la stack dev.
"""

from __future__ import annotations

import numpy as np
import polars as pl
import pytest

from ml.training import benchmark as benchmark_module
from ml.training.benchmark import (
    LABEL_COLUMN,
    feature_columns,
    prepare_features,
    register_best,
    split_train_valid_test,
)

TEST_EXPERIMENT_NAME = "test-gravia-severity-classifier"
TEST_REGISTERED_MODEL_NAME = "test-gravia-severity-classifier"

CATEGORICAL_LEVELS: dict[str, list[str]] = {
    "luminosite": ["-1", "1", "2", "3"],
    "departement": ["75", "13", "69"],
    "intersection": ["-1", "1", "2"],
    "meteo": ["-1", "1", "2"],
    "type_collision": ["Sans collision", "Deux véhicules - frontale", "Autre collision"],
    "categorie_route": ["1", "3", "4"],
    "regime_circulation": ["-1", "1", "2"],
    "voie_reservee": ["-1", "0"],
    "profil_route": ["-1", "1", "2"],
    "trace_plan": ["-1", "1"],
    "etat_surface": ["1", "2"],
    "infrastructure": ["-1", "0"],
    "situation": ["-1", "1"],
}


def _synthetic_gold_features(n_per_year: int = 60) -> pl.DataFrame:
    """Reconstruit la forme de `load_gold_features` sans PostgreSQL, pour un test rapide."""
    rng = np.random.default_rng(42)
    years = [2019, 2020, 2021, 2022, 2023]
    n = n_per_year * len(years)

    columns: dict[str, list] = {"annee": [year for year in years for _ in range(n_per_year)]}
    for name, levels in CATEGORICAL_LEVELS.items():
        columns[name] = list(rng.choice(levels, size=n))
    columns["nb_voies"] = list(rng.integers(1, 5, size=n))
    columns["vitesse_max"] = list(rng.choice([30, 50, 90, 130], size=n))
    columns["heure"] = list(rng.integers(0, 24, size=n))
    columns["nb_vehicules"] = list(rng.integers(1, 4, size=n))
    columns["nb_usagers"] = list(rng.integers(1, 5, size=n))
    columns["mois"] = list(rng.integers(1, 13, size=n))
    columns["jour_semaine"] = list(rng.integers(1, 8, size=n))
    columns["agglomeration"] = list(rng.choice([True, False], size=n))
    columns[LABEL_COLUMN] = list(rng.choice([True, False], size=n, p=[0.35, 0.65]))

    return pl.DataFrame(columns)


@pytest.fixture
def isolated_mlflow_names(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(benchmark_module, "EXPERIMENT_NAME", TEST_EXPERIMENT_NAME)
    monkeypatch.setattr(benchmark_module, "REGISTERED_MODEL_NAME", TEST_REGISTERED_MODEL_NAME)


def _mlflow_reachable() -> bool:
    import mlflow

    try:
        mlflow.set_tracking_uri("http://localhost:5000")
        mlflow.MlflowClient().search_experiments(max_results=1)
    except Exception:
        return False
    return True


def test_run_benchmark_and_register_best_on_synthetic_data(isolated_mlflow_names: None) -> None:
    import mlflow

    if not _mlflow_reachable():
        pytest.skip("MLflow non joignable (stack dev non démarrée, cf. `make dev`)")

    import os

    os.environ["AWS_ACCESS_KEY_ID"] = "minioadmin"
    os.environ["AWS_SECRET_ACCESS_KEY"] = "minioadmin"
    os.environ["MLFLOW_S3_ENDPOINT_URL"] = "http://localhost:9000"
    mlflow.set_tracking_uri("http://localhost:5000")

    df = prepare_features(_synthetic_gold_features())
    categorical, other = feature_columns("baseline")
    train, valid, test = split_train_valid_test(df)

    x_train = benchmark_module._to_pandas(train, categorical, other)
    x_valid = benchmark_module._to_pandas(valid, categorical, other)
    x_test = benchmark_module._to_pandas(test, categorical, other)
    y_train = train[LABEL_COLUMN].to_pandas().astype(int)
    y_valid = valid[LABEL_COLUMN].to_pandas().astype(int)
    y_test = test[LABEL_COLUMN].to_pandas().astype(int)

    mlflow.set_experiment(TEST_EXPERIMENT_NAME)
    result = benchmark_module.evaluate_model(
        "logistic_regression",
        "baseline",
        x_train,
        y_train,
        x_valid,
        y_valid,
        x_test,
        y_test,
        categorical,
        other,
    )

    try:
        assert result["name"] == "logistic_regression"
        assert 0.0 <= result["recall"] <= 1.0
        assert 0.0 <= result["f1_macro"] <= 1.0
        assert result["run_id"]

        run = mlflow.get_run(result["run_id"])
        assert run.data.params["feature_set"] == "baseline"
        assert "recall_grave_test" in run.data.metrics

        # Force le franchissement des seuils pour exercer le vrai chemin d'enregistrement MLflow,
        # sans dépendre de la performance (non pertinente ici) d'un modèle jouet sur données
        # synthétiques.
        result["meets_gates"] = True
        version = register_best(result)

        assert version is not None
        client = mlflow.MlflowClient()
        registered = client.get_model_version_by_alias(TEST_REGISTERED_MODEL_NAME, "staging")
        assert registered.version == version
    finally:
        client = mlflow.MlflowClient()
        try:
            client.delete_registered_model(TEST_REGISTERED_MODEL_NAME)
        except Exception:
            pass
        try:
            client.delete_run(result["run_id"])
        except Exception:
            pass
