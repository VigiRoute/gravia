"""Test d'intégration de l'API de serving contre le vrai modèle `@staging` (MLflow réel).

Pas de données synthétiques ici, contrairement à tests/integration/test_benchmark_mlflow.py : ce
test vérifie que l'API charge et sert *le* modèle réellement promu (cf.
docs/ml_training_results.md), pas un modèle jouet — c'est le comportement à vérifier, l'objectif
n'est pas d'entraîner quoi que ce soit ici (rapide : un seul chargement de modèle, pas de fit).
"""

from __future__ import annotations

import json
import logging

import pytest
import sqlalchemy as sa

from gravia.config import get_settings
from ml.serving.schemas import EXAMPLE_REQUEST


def _mlflow_reachable() -> bool:
    settings = get_settings()
    try:
        import mlflow

        mlflow.set_tracking_uri(settings.mlflow_tracking_uri)
        mlflow.MlflowClient().search_experiments(max_results=1)
    except Exception:
        return False
    try:
        sa.create_engine(settings.database.url).connect().close()
    except Exception:
        return False
    return True


@pytest.fixture
def client():
    if not _mlflow_reachable():
        pytest.skip("MLflow/PostgreSQL non joignables (stack dev non démarrée, cf. `make dev`)")

    from fastapi.testclient import TestClient

    from ml.serving.api import app

    with TestClient(app) as test_client:
        yield test_client


# Réutilise l'exemple affiché sur /docs plutôt que d'en maintenir un second en parallèle.
_VALID_PAYLOAD = EXAMPLE_REQUEST


def test_health_reports_model_loaded(client) -> None:
    response = client.get("/health")

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert body["modele_version"]


def test_predict_severity_returns_estimation_with_explanation(client) -> None:
    response = client.post("/v1/predict-severity", json=_VALID_PAYLOAD)

    assert response.status_code == 200
    body = response.json()
    assert body["gravite_predite"] in {"grave", "non_grave"}
    assert 0.0 <= body["probabilite"] <= 1.0
    assert 0.0 <= body["seuil_decision"] <= 1.0
    assert len(body["top_contributions"]) == 5
    for contribution in body["top_contributions"]:
        assert "feature" in contribution
        assert isinstance(contribution["contribution"], float)


def test_predict_severity_logs_a_structured_json_prediction_record(client, caplog) -> None:
    """EF-7 (cf. ml/serving/api.py, docstring module) : la prédiction est journalisée en JSON
    structuré, pas en texte libre — vérifié ici sur le vrai logger configuré par le module, pas
    seulement sur le formatter en isolation (cf. tests/unit/test_serving_api_logging.py)."""
    from ml.serving.api import JsonFormatter

    with caplog.at_level(logging.INFO, logger="gravia.serving"):
        client.post("/v1/predict-severity", json=_VALID_PAYLOAD)

    prediction_records = [r for r in caplog.records if getattr(r, "event", None) == "prediction"]
    assert len(prediction_records) == 1
    record = prediction_records[0]
    assert record.departement == _VALID_PAYLOAD["departement"]
    assert record.gravite_predite in {"grave", "non_grave"}
    assert 0.0 <= record.probabilite <= 1.0

    payload = json.loads(JsonFormatter().format(record))
    assert payload["event"] == "prediction"
    assert payload["departement"] == _VALID_PAYLOAD["departement"]


def test_predict_severity_rejects_missing_required_field(client) -> None:
    incomplete_payload = dict(_VALID_PAYLOAD)
    del incomplete_payload["departement"]

    response = client.post("/v1/predict-severity", json=incomplete_payload)

    assert response.status_code == 422


def test_metrics_exposes_prometheus_format(client) -> None:
    """Cible scrapée par Prometheus (`infra/prometheus/prometheus.yml`, job `gravia-api`),
    consommée par le dashboard Grafana (`infra/grafana/provisioning/dashboards/`)."""
    client.post("/v1/predict-severity", json=_VALID_PAYLOAD)

    response = client.get("/metrics")

    assert response.status_code == 200
    assert 'handler="/v1/predict-severity"' in response.text
    assert "http_request_duration_seconds_bucket" in response.text
