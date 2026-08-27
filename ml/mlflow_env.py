"""Configuration de l'environnement MLflow, partagée entre `ml/training` et `ml/serving`.

Isolé dans son propre module plutôt que dupliqué (ou importé en `_privé`) entre les deux, car les
deux composants en ont besoin : `ml/training/benchmark.py` pour logger les artefacts de modèle,
`ml/serving/api.py` pour recharger le modèle `@staging` au démarrage du processus.
"""

from __future__ import annotations

import os
from typing import Any


def configure_s3_artifact_env(settings: Any) -> None:
    """Expose les identifiants MinIO aux variables d'environnement standard boto3/MLflow.

    MLflow (via boto3) lit ses identifiants S3 dans l'environnement du processus
    (`AWS_ACCESS_KEY_ID`/`AWS_SECRET_ACCESS_KEY`/`MLFLOW_S3_ENDPOINT_URL`), pas via un paramètre
    de son API Python — `mlflow.set_tracking_uri` ne suffit pas pour l'artifact store S3.

    Écrase ces variables plutôt que de ne les définir que si absentes (`setdefault`) : `.env`
    définit déjà `AWS_ACCESS_KEY_ID`/`AWS_SECRET_ACCESS_KEY` pour LocalStack/Terraform
    (identifiants `test`, sans rapport avec MinIO), chargées par `gravia.config` au démarrage —
    un `setdefault` ne les remplacerait jamais et `mlflow.*.log_model`/`mlflow.*.load_model`
    échoueraient avec `InvalidAccessKeyId` (constaté en testant `ml/training/benchmark.py`, cf.
    docs/ml_training_results.md). L'écrasement ne modifie que l'environnement de ce process, pas
    `.env` ni le shell appelant.

    Args:
        settings: Configuration applicative (`gravia.config.Settings`).
    """
    store = settings.object_store
    os.environ["AWS_ACCESS_KEY_ID"] = store.access_key
    os.environ["AWS_SECRET_ACCESS_KEY"] = store.secret_key
    os.environ["MLFLOW_S3_ENDPOINT_URL"] = store.endpoint
