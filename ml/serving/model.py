"""Chargement du modèle promu et construction des prédictions avec explication SHAP.

Charge le modèle nativement (`mlflow.lightgbm.load_model`), pas via le scoring REST générique de
MLflow : celui-ci sérialise l'exemple en JSON puis le redéserialise, ce qui perd le dtype
`category` pandas des colonnes catégorielles — LightGBM refuse alors de prédire (constaté en
testant `ml/training/benchmark.py`, cf. docs/ml_training_results.md). Ici, la requête est
convertie directement en `DataFrame` avec les dtypes `category` explicitement posés, comme à
l'entraînement.

Le seuil de décision n'est pas codé en dur : récupéré depuis les métriques du run MLflow associé
au modèle `@staging` (celui qui l'a calibré sur la validation, cf.
`ml/training/benchmark.py::evaluate_model`), pas recalculé ici.

Explicabilité (CDC_GRAVIA.md, EF-4/EC-8) : `shap.TreeExplainer`, adapté aux modèles à arbres et
rapide (SHAP exact, pas une approximation par échantillonnage) — nécessaire pour tenir la
latence p95 < 300 ms visée par le CDC (ENF-1).
"""

from __future__ import annotations

from dataclasses import dataclass

import mlflow
import pandas as pd
import shap

from gravia.gold import COLLISION_LABEL_UNKNOWN, COLLISION_LABELS
from ml.serving.schemas import FeatureContribution, PredictSeverityRequest, PredictSeverityResponse
from ml.training.benchmark import REGISTERED_MODEL_NAME, STAGING_ALIAS, feature_columns

#: Configuration de features du modèle actuellement promu à `staging` (cf.
#: docs/ml_training_results.md). À revérifier si un autre modèle est promu par la suite : rien ne
#: garantit qu'un futur modèle @staging utilise la même configuration.
FEATURE_SET = "enriched"

MODEL_URI = f"models:/{REGISTERED_MODEL_NAME}@{STAGING_ALIAS}"

#: Nombre de contributions SHAP les plus fortes renvoyées dans la réponse.
TOP_CONTRIBUTIONS = 5


@dataclass(frozen=True)
class LoadedModel:
    """Modèle chargé, prêt à prédire, avec son seuil calibré et son explainer SHAP."""

    booster: object
    threshold: float
    explainer: shap.TreeExplainer
    categorical_columns: tuple[str, ...]
    other_columns: tuple[str, ...]
    version: str


def load_staged_model() -> LoadedModel:
    """Charge le modèle `@staging` nativement, avec son seuil calibré et un explainer SHAP.

    Returns:
        Le modèle prêt à l'emploi.
    """
    client = mlflow.MlflowClient()
    model_version = client.get_model_version_by_alias(REGISTERED_MODEL_NAME, STAGING_ALIAS)
    run = client.get_run(model_version.run_id)
    threshold = run.data.metrics["threshold"]

    booster = mlflow.lightgbm.load_model(MODEL_URI)
    categorical, other = feature_columns(FEATURE_SET)

    return LoadedModel(
        booster=booster,
        threshold=threshold,
        explainer=shap.TreeExplainer(booster),
        categorical_columns=tuple(categorical),
        other_columns=tuple(other),
        version=model_version.version,
    )


def build_feature_frame(
    request: PredictSeverityRequest, categorical: tuple[str, ...], other: tuple[str, ...]
) -> pd.DataFrame:
    """Convertit une requête en `DataFrame` un accident, dtypes conformes à l'entraînement.

    `type_collision` est décodé en libellé texte via `gravia.gold.COLLISION_LABELS` — c'est ce
    même dictionnaire qui a construit la colonne `type_collision` de `gold_dim_collision`, seule
    source de vérité pour cette correspondance code -> libellé (cf. `gravia.gold`).

    Args:
        request: Requête validée.
        categorical: Colonnes catégorielles attendues par le modèle
            (`LoadedModel.categorical_columns`).
        other: Colonnes numériques/booléennes attendues (`LoadedModel.other_columns`).

    Returns:
        Une table d'une ligne, colonnes dans l'ordre `categorical + other`, catégorielles en
        dtype `category` (cf. `ml.features.gold_features.prepare_features` — même transformation
        qu'à l'entraînement).
    """
    row = {
        "luminosite": str(request.luminosite),
        "departement": request.departement,
        "intersection": str(request.intersection),
        "meteo": str(request.meteo),
        "type_collision": COLLISION_LABELS.get(request.type_collision, COLLISION_LABEL_UNKNOWN),
        "categorie_route": str(request.categorie_route),
        "regime_circulation": str(request.regime_circulation),
        "voie_reservee": str(request.voie_reservee),
        "profil_route": str(request.profil_route),
        "trace_plan": str(request.trace_plan),
        "etat_surface": str(request.etat_surface),
        "infrastructure": str(request.infrastructure),
        "situation": str(request.situation),
        "nb_voies": float(request.nb_voies),
        "vitesse_max": float(request.vitesse_max),
        "heure": float(request.moment.hour),
        "nb_vehicules": float(request.nb_vehicules),
        "mois": float(request.moment.month),
        "agglomeration": request.agglomeration,
        "flag_2roues_motorise": request.flag_2roues_motorise,
        "flag_poids_lourd": request.flag_poids_lourd,
        "flag_velo_edp": request.flag_velo_edp,
        "flag_pieton": request.flag_pieton,
    }
    frame = pd.DataFrame([row])
    for column in categorical:
        frame[column] = frame[column].astype("category")
    return frame[list(categorical) + list(other)]


def _positive_class_shap_values(explainer: shap.TreeExplainer, frame: pd.DataFrame):
    """Valeurs SHAP de la classe positive (grave), quel que soit le format renvoyé par SHAP.

    `TreeExplainer.shap_values` sur un classifieur binaire renvoie soit un tableau `(n, features)`
    déjà pour la classe positive, soit une liste `[classe_0, classe_1]` selon la version de
    SHAP/LightGBM (avertissement observé en testant, cf. docs/ml_training_results.md) — les deux
    formats sont gérés plutôt que de supposer l'un des deux.
    """
    shap_values = explainer.shap_values(frame)
    if isinstance(shap_values, list):
        return shap_values[1][0]
    return shap_values[0]


def predict_severity(
    model: LoadedModel, request: PredictSeverityRequest
) -> PredictSeverityResponse:
    """Prédit la gravité d'un accident et explique la prédiction (contributions SHAP).

    Args:
        model: Modèle chargé (cf. `load_staged_model`).
        request: Caractéristiques de l'accident.

    Returns:
        La prédiction, sa probabilité, le seuil utilisé et les variables les plus contributives.
    """
    frame = build_feature_frame(request, model.categorical_columns, model.other_columns)

    probability = float(model.booster.predict_proba(frame)[0, 1])
    is_grave = probability >= model.threshold

    contributions = _positive_class_shap_values(model.explainer, frame)
    ranked = sorted(
        zip(frame.columns, contributions, strict=True),
        key=lambda pair: abs(pair[1]),
        reverse=True,
    )[:TOP_CONTRIBUTIONS]

    return PredictSeverityResponse(
        gravite_predite="grave" if is_grave else "non_grave",
        probabilite=probability,
        seuil_decision=model.threshold,
        modele_version=model.version,
        top_contributions=[
            FeatureContribution(feature=name, contribution=float(value)) for name, value in ranked
        ],
    )
