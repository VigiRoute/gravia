"""API de prédiction temps réel — `POST /v1/predict-severity` (cf. CDC_GRAVIA.md, EF-5).

Le modèle est chargé une fois au démarrage (`lifespan`), pas à chaque requête : recharger depuis
le registry MLflow à chaque appel coûterait largement le budget de latence p95 < 300 ms visé par
le CDC (ENF-1). `GET /health` expose l'état du chargement pour le healthcheck Docker Compose.

Journalisation (CDC_GRAVIA.md, EF-7 — « le système journalise les prédictions pour audit et
traçabilité ») : chaque prédiction est journalisée (logger `gravia.serving`, niveau INFO) avec la
probabilité, la décision et le seuil utilisé, **en JSON structuré** (une ligne = un objet, champs
nommés), pas en texte libre à parser au regex — interrogeable par `jq`/`grep -P` ou par un futur
agrégateur de logs (Loki, CloudWatch Logs Insights) sans adaptation. Une trace de niveau requête
HTTP, pas encore un stockage persistant et indexé (table dédiée, etc.) — au-delà du périmètre de
ce premier serving, à réévaluer si l'audit doit être requêtable après coup.

Ce endpoint **assiste**, ne décide pas (CDC_GRAVIA.md, EC-7, human-in-the-loop) : la réponse est
une estimation destinée à l'opérateur, ce endpoint ne déclenche aucune action de dispatching.
"""

from __future__ import annotations

import json
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime

import mlflow
from fastapi import FastAPI, HTTPException
from prometheus_fastapi_instrumentator import Instrumentator

from gravia.config import get_settings
from ml.mlflow_env import configure_s3_artifact_env
from ml.serving.model import LoadedModel, load_staged_model, predict_severity
from ml.serving.schemas import PredictSeverityRequest, PredictSeverityResponse
from ml.training.benchmark import REGISTERED_MODEL_NAME

#: Champs déjà présents sur tout `LogRecord` standard (calculé dynamiquement plutôt que recopié à
#: la main : couvre les champs propres à la version de Python en place, ex. `taskName` en 3.12).
#: Sert à isoler dans `JsonFormatter` les seuls champs ajoutés via `logger.info(..., extra=...)`.
_STANDARD_LOG_RECORD_FIELDS = frozenset(logging.LogRecord("", 0, "", 0, "", (), None).__dict__)


class JsonFormatter(logging.Formatter):
    """Une ligne de log = un objet JSON, pas du texte libre à parser au regex (EF-7, cf. docstring
    module) : `extra={...}` passé à `logger.info` devient des champs du JSON, pas une chaîne
    interpolée dans le message.
    """

    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "timestamp": datetime.fromtimestamp(record.created, tz=UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        extra = {
            key: value
            for key, value in record.__dict__.items()
            if key not in _STANDARD_LOG_RECORD_FIELDS
        }
        payload.update(extra)
        return json.dumps(payload, ensure_ascii=False)


# Sans ceci, le logger reste à son niveau effectif par défaut (WARNING, hérité de la racine
# sans handler) et les logger.info() ci-dessous n'émettent jamais rien — constaté en audit sur
# le conteneur réel : une prédiction ne produisait que la ligne d'accès uvicorn, jamais la ligne
# "prédiction ...". uvicorn configure ses propres loggers nommés (uvicorn.error/access) sans
# toucher à celui-ci.
_handler = logging.StreamHandler()
_handler.setFormatter(JsonFormatter())
logging.basicConfig(level=logging.INFO, handlers=[_handler])
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
        "modele_charge",
        extra={
            "event": "model_loaded",
            "modele": REGISTERED_MODEL_NAME,
            "modele_version": _state["model"].version,
            "seuil": _state["model"].threshold,
        },
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

**Assiste, ne décide pas** (human-in-the-loop) : cette réponse est une estimation destinée à
l'opérateur, pas une action de dispatching.

### Valeurs possibles par champ

Les codes numériques reprennent le dictionnaire officiel ONISR ; `-1` signifie « non renseigné »
pour la quasi-totalité d'entre eux.

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
        "prediction",
        extra={
            "event": "prediction",
            "departement": request.departement,
            "gravite_predite": response.gravite_predite,
            "probabilite": response.probabilite,
            "seuil_decision": response.seuil_decision,
            "modele_version": response.modele_version,
        },
    )
    return response
