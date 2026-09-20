"""API de prédiction temps réel — `POST /v1/predict-severity` (cf. CDC_GRAVIA.md, EF-5).

Le modèle est chargé une fois au démarrage (`lifespan`), pas à chaque requête : recharger depuis
le registry MLflow à chaque appel coûterait largement le budget de latence p95 < 300 ms visé par
le CDC (ENF-1). `GET /health` expose l'état du chargement pour le healthcheck Docker Compose.

Journalisation (CDC_GRAVIA.md, EF-7 — « le système journalise les prédictions pour audit et
traçabilité ») : chaque prédiction est journalisée (logger `gravia.serving`, niveau INFO) avec la
probabilité, la décision et le seuil utilisé. Une trace de niveau requête HTTP, pas encore un
stockage persistant et interrogeable (table dédiée, etc.) — au-delà du périmètre de ce premier
serving, à réévaluer si l'audit doit être requêtable après coup.

Ce endpoint **assiste**, ne décide pas (CDC_GRAVIA.md, EC-7, human-in-the-loop) : la réponse est
une estimation destinée à l'opérateur, ce endpoint ne déclenche aucune action de dispatching.
"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import mlflow
from fastapi import FastAPI, HTTPException
from prometheus_fastapi_instrumentator import Instrumentator

from gravia.config import get_settings
from ml.mlflow_env import configure_s3_artifact_env
from ml.serving.model import LoadedModel, load_staged_model, predict_severity
from ml.serving.schemas import PredictSeverityRequest, PredictSeverityResponse
from ml.training.benchmark import REGISTERED_MODEL_NAME

# Sans ceci, le logger reste à son niveau effectif par défaut (WARNING, hérité de la racine
# sans handler) et les logger.info() ci-dessous n'émettent jamais rien — constaté en audit sur
# le conteneur réel : une prédiction ne produisait que la ligne d'accès uvicorn, jamais la ligne
# "prédiction ...". uvicorn configure ses propres loggers nommés (uvicorn.error/access) sans
# toucher à celui-ci.
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logger = logging.getLogger("gravia.serving")

_state: dict[str, LoadedModel] = {}


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Configure MLflow puis charge le modèle `@staging` une fois au démarrage du processus.

    `mlflow.set_tracking_uri` et les identifiants S3 (MinIO, cf.
    `ml.mlflow_env.configure_s3_artifact_env`) ne sont pas déjà en place par défaut —
    contrairement à un notebook ou un script lancé après les avoir positionnés à la main, ce
    processus démarre à froid.
    """
    settings = get_settings()
    configure_s3_artifact_env(settings)
    mlflow.set_tracking_uri(settings.mlflow_tracking_uri)

    _state["model"] = load_staged_model()
    logger.info(
        "Modèle chargé : %s v%s (seuil=%.2f)",
        REGISTERED_MODEL_NAME,
        _state["model"].version,
        _state["model"].threshold,
    )
    yield
    _state.clear()


app = FastAPI(
    title="GRAVIA — prédiction de gravité d'accident",
    description="Aide à la décision pour la priorisation des secours routiers.",
    version="1.0.0",
    lifespan=lifespan,
)

# Expose /metrics (format Prometheus) : latence par endpoint, volume de requêtes, codes de
# statut — déjà scrapé par `infra/prometheus/prometheus.yml` (job `gravia-api`) depuis la mise
# en place de la stack dev, mais resté sans cible tant que ce endpoint n'existait pas (cf.
# Architecture_GRAVIA.md §9, tableau de bord Grafana latence/erreurs/disponibilité promis mais
# jamais livré). `instrument()` avant `expose()` : sans ça, les requêtes vers /metrics
# elles-mêmes ne seraient pas instrumentées, `expose()` seul ne fait qu'ajouter la route.
Instrumentator().instrument(app).expose(app)


@app.get("/health")
def health() -> dict[str, str]:
    """Vérifie que le modèle est chargé (healthcheck Docker Compose)."""
    if "model" not in _state:
        raise HTTPException(status_code=503, detail="Modèle non chargé.")
    return {"status": "ok", "modele_version": _state["model"].version}


def _request_fields_reference() -> str:
    """Tableau markdown (valeurs possibles par champ) affiché sur `/docs`.

    Généré depuis les `description` des champs de `PredictSeverityRequest` plutôt que dupliqué
    à la main : les valeurs affichées dans Swagger ne peuvent alors pas diverger du schéma réel.
    """
    rows = ["| Champ | Valeurs possibles |", "|---|---|"]
    for name, field in PredictSeverityRequest.model_fields.items():
        rows.append(f"| `{name}` | {field.description or ''} |")
    return "\n".join(rows)


_PREDICT_DESCRIPTION = f"""Estime la gravité probable d'un accident à partir des caractéristiques
connues au moment du signalement.

**Assiste, ne décide pas** (human-in-the-loop, CDC_GRAVIA.md EC-7) : cette réponse est une
estimation destinée à l'opérateur, pas une action de dispatching.

### Valeurs possibles par champ

Les codes numériques reprennent le dictionnaire officiel ONISR (cf. CLAUDE.md) ; `-1` signifie
« non renseigné » pour la quasi-totalité d'entre eux.

{_request_fields_reference()}
"""


@app.post(
    "/v1/predict-severity",
    response_model=PredictSeverityResponse,
    summary="Estimer la gravité probable d'un accident",
    description=_PREDICT_DESCRIPTION,
)
def predict(request: PredictSeverityRequest) -> PredictSeverityResponse:
    """Estime la gravité probable d'un accident à partir des caractéristiques du signalement.

    Args:
        request: Caractéristiques connues au moment du signalement.

    Returns:
        Une estimation (pas une décision, cf. docstring module) avec son explication SHAP.

    Raises:
        HTTPException: 503 si le modèle n'est pas chargé.
    """
    model = _state.get("model")
    if model is None:
        raise HTTPException(status_code=503, detail="Modèle non chargé.")

    response = predict_severity(model, request)
    logger.info(
        "prédiction departement=%s gravite=%s probabilite=%.3f seuil=%.2f modele_version=%s",
        request.departement,
        response.gravite_predite,
        response.probabilite,
        response.seuil_decision,
        response.modele_version,
    )
    return response
