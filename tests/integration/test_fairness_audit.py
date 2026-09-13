"""Test d'intégration de `ml/fairness/audit.py` contre les vraies données Gold/Silver et le
vrai modèle `@staging` (MLflow réel)."""

from __future__ import annotations

import pytest
import sqlalchemy as sa

from gravia.config import get_settings
from ml.fairness.audit import build_audit_frame, compute_group_metrics
from ml.mlflow_env import configure_s3_artifact_env


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


def test_build_audit_frame_and_compute_metrics_on_real_data() -> None:
    if not _stack_reachable():
        pytest.skip("MLflow/PostgreSQL non joignables (stack dev non démarrée, cf. `make dev`)")

    settings = get_settings()
    configure_s3_artifact_env(settings)
    engine = sa.create_engine(settings.database.url)

    audit = build_audit_frame(engine, settings)

    assert len(audit) > 0
    assert set(audit.columns) >= {"is_grave", "pred_grave", "sexe", "tranche_age"}
    assert audit["pred_grave"].isin([0, 1]).all()

    metrics = compute_group_metrics(audit, "sexe")

    assert len(metrics) > 0
    assert metrics["n"].sum() == len(audit)
