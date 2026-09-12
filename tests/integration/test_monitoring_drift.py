"""Test d'intégration de `ml/monitoring/drift.py` contre les vraies données Gold et le vrai
modèle `@staging` (MLflow réel) — vérifie la mécanique de bout en bout sur données réelles, pas
la valeur du PSI elle-même (qui dépend du millésime réellement chargé, pas figée par ce test)."""

from __future__ import annotations

import pandas as pd
import pytest
import sqlalchemy as sa

from gravia.config import get_settings
from ml.features.gold_features import load_gold_features, prepare_features, split_train_valid_test
from ml.mlflow_env import configure_s3_artifact_env
from ml.monitoring.drift import compute_feature_drift, compute_prediction_drift
from ml.serving.model import FEATURE_SET, load_staged_model
from ml.training.benchmark import _to_pandas, feature_columns


def _stack_reachable() -> bool:
    settings = get_settings()
    try:
        import mlflow

        mlflow.set_tracking_uri(settings.mlflow_tracking_uri)
        mlflow.MlflowClient().search_experiments(max_results=1)
        sa.create_engine(settings.database.url).connect().close()
    except Exception:
        return False
    return True


def test_feature_and_prediction_drift_run_end_to_end_on_real_data() -> None:
    if not _stack_reachable():
        pytest.skip("MLflow/PostgreSQL non joignables (stack dev non démarrée, cf. `make dev`)")

    settings = get_settings()
    configure_s3_artifact_env(settings)

    import mlflow

    mlflow.set_tracking_uri(settings.mlflow_tracking_uri)

    engine = sa.create_engine(settings.database.url)
    categorical, other = feature_columns(FEATURE_SET)
    df = prepare_features(load_gold_features(engine))
    train, valid, test = split_train_valid_test(df)

    reference = _to_pandas(train, categorical, other)
    current = _to_pandas(test, categorical, other)

    feature_drift = compute_feature_drift(reference, current, categorical, other)

    assert set(feature_drift) == set(categorical) | set(other)
    assert all(psi >= 0.0 for psi in feature_drift.values())

    model = load_staged_model()
    valid_frame = _to_pandas(valid, categorical, other)
    reference_proba = pd.Series(model.booster.predict_proba(valid_frame)[:, 1])
    current_proba = pd.Series(model.booster.predict_proba(current)[:, 1])

    prediction_psi = compute_prediction_drift(reference_proba, current_proba)

    assert prediction_psi >= 0.0
