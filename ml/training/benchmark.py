"""Benchmark de modèles pour la sévérité d'accident, tracké dans MLflow.

Compare trois familles de modèles, comme prévu par docs/Architecture_GRAVIA.md §3 (« Benchmark :
régression logistique (baseline), Random Forest, LightGBM/XGBoost — modèle retenu selon les
métriques ») sur les splits produits par `ml/features/gold_features.py`. XGBoost n'est pas ajouté
en plus de LightGBM : le document cite « LightGBM/XGBoost » comme une alternative, pas les deux à
la fois, et LightGBM est déjà la dépendance figée du projet, déjà validée dans les notebooks
(reproduction à 0,001 près du baseline publié, cf. `ml/features/gold_features.py`).

Encodage : LightGBM consomme les colonnes catégorielles nativement (`category` pandas, comme dans
les notebooks) ; régression logistique et Random Forest n'ont pas de support catégoriel natif en
scikit-learn, elles passent par un `OneHotEncoder` dans un `ColumnTransformer`.

Sélection du meilleur modèle — priorité au recall (coût asymétrique d'un accident grave raté,
cf. CDC) : parmi les modèles atteignant le seuil CDC recall >= 0,80 sur le test, celui avec le
meilleur F1 macro ; si aucun ne l'atteint, celui au recall le plus élevé.

Registry MLflow : les stages (Staging/Production) sont dépréciés depuis MLflow 2.9 au profit des
alias de modèle — cf. Architecture_GRAVIA.md §3 (« registry Staging/Prod »), traduit ici par
l'alias `staging` plutôt que par l'API de stages dépréciée. Seul un modèle qui franchit les deux
seuils CDC (recall >= 0,80, F1 macro >= 0,70) est enregistré et promu à cet alias ; sinon le run
reste tracké dans MLflow mais n'est pas enregistré (cf. CLAUDE.md, seuils et métriques —
« Bloquer la promotion en production »).

Hors périmètre, volontairement : la calibration de seuil par sous-groupe (département) reste un
point ouvert non résolu (CDC §13.7/§14, cf. notebooks/eval_seuil_par_zone.py) — un seuil national
unique est calibré ici sur la validation, pas une politique de seuils par zone.

Limitation connue, constatée en testant — à traiter par `ml/serving`, pas ici : le chemin de
service générique de MLflow (auto-validation `pyfunc` sur l'exemple d'entrée, scoring REST JSON)
sérialise l'exemple en JSON puis le redéserialise, ce qui perd le dtype `category` pandas des
colonnes catégorielles. LightGBM refuse alors de prédire (`ValueError: train and valid dataset
categorical_feature do not match`) car les colonnes ne sont plus reconnues comme catégorielles.
Le modèle lui-même n'est pas cassé : chargé nativement (`mlflow.lightgbm.load_model`) avec le
dtype `category` explicitement remis sur les colonnes concernées (comme à l'entraînement), il
prédit normalement — vérifié manuellement sur le modèle enregistré. `ml/serving` devra charger le
modèle de cette façon, pas via le scoring REST générique de MLflow.
"""

from __future__ import annotations

from typing import Any, Literal

import lightgbm as lgb
import mlflow
import mlflow.lightgbm
import mlflow.sklearn
import numpy as np
import pandas as pd
import polars as pl
import sqlalchemy as sa
from sklearn.compose import ColumnTransformer
from sklearn.ensemble import RandomForestClassifier
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import f1_score, recall_score
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import OneHotEncoder

from ml.features.gold_features import (
    BASELINE_BOOLEAN_COLUMNS,
    BASELINE_CATEGORICAL_COLUMNS,
    BASELINE_NUMERIC_COLUMNS,
    ENRICHED_FLAG_COLUMNS,
    LABEL_COLUMN,
    load_gold_features,
    prepare_features,
    split_train_valid_test,
)

RANDOM_STATE = 42
N_ESTIMATORS = 300
LEARNING_RATE = 0.05  # LightGBM uniquement (cf. notebooks/eda_baseline_baac.py)

#: Seuils CDC (cf. CLAUDE.md, seuils et métriques).
TARGET_RECALL = 0.80
MIN_F1_MACRO = 0.70

EXPERIMENT_NAME = "gravia-severity-classifier"
REGISTERED_MODEL_NAME = "gravia-severity-classifier"
STAGING_ALIAS = "staging"

FeatureSet = Literal["baseline", "enriched"]


def feature_columns(feature_set: FeatureSet) -> tuple[list[str], list[str]]:
    """Colonnes catégorielles et numériques/booléennes pour une configuration de features.

    Args:
        feature_set: `"baseline"` reproduit exactement le protocole publié (recall 0,808 / F1
            macro 0,708). `"enriched"` y ajoute `ENRICHED_FLAG_COLUMNS` (meilleure configuration
            testée dans `notebooks/eval_enrichissement_vs_seuil.py`, jamais promue en référence
            officielle).

    Returns:
        `(colonnes_categorielles, colonnes_numeriques_et_booleennes)`.

    Raises:
        ValueError: Si `feature_set` n'est ni `"baseline"` ni `"enriched"`.
    """
    categorical = list(BASELINE_CATEGORICAL_COLUMNS)
    other = list(BASELINE_NUMERIC_COLUMNS) + list(BASELINE_BOOLEAN_COLUMNS)
    if feature_set == "baseline":
        return categorical, other
    if feature_set == "enriched":
        return categorical, other + list(ENRICHED_FLAG_COLUMNS)
    raise ValueError(f"feature_set inconnu : {feature_set!r} (attendu 'baseline' ou 'enriched')")


def _to_pandas(df: pl.DataFrame, categorical: list[str], other: list[str]) -> pd.DataFrame:
    """Convertit un sous-ensemble de colonnes en pandas, catégorielles en dtype `category`."""
    frame = df.select(categorical + other).to_pandas()
    for column in categorical:
        frame[column] = frame[column].astype("category")
    return frame


def calibrate_threshold(y_true: pd.Series, proba: np.ndarray, target_recall: float) -> float:
    """Choisit le plus grand seuil qui atteint le recall cible.

    Repris de `notebooks/eda_baseline_baac.py` : calibré sur la validation, jamais sur le test —
    calibrer sur le holdout final serait de la fuite de méthodologie.

    Args:
        y_true: Étiquettes réelles (validation).
        proba: Probabilités prédites de la classe positive (validation).
        target_recall: Recall cible (seuil CDC : 0,80).

    Returns:
        Le seuil retenu : le plus permissif qui atteint encore `target_recall`, ou à défaut celui
        qui maximise le recall.
    """
    thresholds = np.linspace(0.01, 0.99, 99)
    recalls = [recall_score(y_true, (proba >= t).astype(int)) for t in thresholds]
    eligible = [t for t, r in zip(thresholds, recalls, strict=True) if r >= target_recall]
    return max(eligible) if eligible else thresholds[int(np.argmax(recalls))]


def _fit_logistic_regression(
    x_train: pd.DataFrame, y_train: pd.Series, categorical: list[str], other: list[str]
) -> Pipeline:
    preprocessor = ColumnTransformer(
        [("cat", OneHotEncoder(handle_unknown="ignore"), categorical)], remainder="passthrough"
    )
    model = Pipeline(
        [
            ("preprocess", preprocessor),
            (
                "classify",
                LogisticRegression(
                    class_weight="balanced", max_iter=1000, random_state=RANDOM_STATE
                ),
            ),
        ]
    )
    model.fit(x_train, y_train)
    return model


def _fit_random_forest(
    x_train: pd.DataFrame, y_train: pd.Series, categorical: list[str], other: list[str]
) -> Pipeline:
    preprocessor = ColumnTransformer(
        [("cat", OneHotEncoder(handle_unknown="ignore"), categorical)], remainder="passthrough"
    )
    model = Pipeline(
        [
            ("preprocess", preprocessor),
            (
                "classify",
                RandomForestClassifier(
                    n_estimators=N_ESTIMATORS,
                    class_weight="balanced",
                    random_state=RANDOM_STATE,
                    n_jobs=-1,
                ),
            ),
        ]
    )
    model.fit(x_train, y_train)
    return model


def _fit_lightgbm(
    x_train: pd.DataFrame, y_train: pd.Series, categorical: list[str], other: list[str]
) -> lgb.LGBMClassifier:
    model = lgb.LGBMClassifier(
        n_estimators=N_ESTIMATORS,
        learning_rate=LEARNING_RATE,
        class_weight="balanced",
        random_state=RANDOM_STATE,
        verbosity=-1,
    )
    model.fit(x_train, y_train, categorical_feature=categorical)
    return model


#: Un « builder » par famille de modèle : même signature `(x_train, y_train, categorical,
#: other) -> estimateur ajusté`, exposant `.predict_proba` — assez uniforme pour un `evaluate_model`
#: commun, sans forcer LightGBM dans le même `Pipeline` scikit-learn que les deux autres (son
#: encodage catégoriel natif est fondamentalement différent d'un `OneHotEncoder`).
MODEL_BUILDERS = {
    "logistic_regression": _fit_logistic_regression,
    "random_forest": _fit_random_forest,
    "lightgbm": _fit_lightgbm,
}

_MLFLOW_LOG_MODEL = {
    "logistic_regression": mlflow.sklearn.log_model,
    "random_forest": mlflow.sklearn.log_model,
    "lightgbm": mlflow.lightgbm.log_model,
}


def evaluate_model(
    name: str,
    feature_set: FeatureSet,
    x_train: pd.DataFrame,
    y_train: pd.Series,
    x_valid: pd.DataFrame,
    y_valid: pd.Series,
    x_test: pd.DataFrame,
    y_test: pd.Series,
    categorical: list[str],
    other: list[str],
) -> dict[str, Any]:
    """Entraîne un modèle, calibre son seuil sur la validation, évalue sur le test, log dans MLflow.

    Args:
        name: Clé de `MODEL_BUILDERS`.
        feature_set: Configuration de features utilisée (traçabilité du run MLflow).
        x_train, y_train: Entraînement (2019-2021).
        x_valid, y_valid: Validation (2022) — sert uniquement à calibrer le seuil.
        x_test, y_test: Test (2023) — holdout, jamais vu avant l'évaluation finale.
        categorical: Colonnes catégorielles.
        other: Colonnes numériques/booléennes.

    Returns:
        Nom, seuil calibré, métriques sur le test, `run_id` MLflow et si les seuils CDC sont
        franchis.
    """
    with mlflow.start_run(run_name=f"{name}-{feature_set}") as run:
        mlflow.log_params(
            {
                "model": name,
                "feature_set": feature_set,
                "n_features": len(categorical) + len(other),
                "n_estimators": N_ESTIMATORS,
                "target_recall": TARGET_RECALL,
                "random_state": RANDOM_STATE,
            }
        )

        model = MODEL_BUILDERS[name](x_train, y_train, categorical, other)

        proba_valid = model.predict_proba(x_valid)[:, 1]
        threshold = calibrate_threshold(y_valid, proba_valid, TARGET_RECALL)

        proba_test = model.predict_proba(x_test)[:, 1]
        y_pred_test = (proba_test >= threshold).astype(int)
        recall = recall_score(y_test, y_pred_test)
        f1_macro = f1_score(y_test, y_pred_test, average="macro")
        meets_gates = recall >= TARGET_RECALL and f1_macro >= MIN_F1_MACRO

        mlflow.log_metrics(
            {
                "threshold": threshold,
                "recall_grave_test": recall,
                "f1_macro_test": f1_macro,
                "meets_cdc_gates": float(meets_gates),
            }
        )
        _MLFLOW_LOG_MODEL[name](model, artifact_path="model", input_example=x_train.head(5))

        run_id = run.info.run_id

    return {
        "name": name,
        "feature_set": feature_set,
        "run_id": run_id,
        "threshold": threshold,
        "recall": recall,
        "f1_macro": f1_macro,
        "meets_gates": meets_gates,
    }


def select_best(results: list[dict[str, Any]]) -> dict[str, Any]:
    """Sélectionne le meilleur modèle — priorité au recall (CDC), F1 macro en second critère.

    Args:
        results: Sortie de `evaluate_model` pour chaque candidat.

    Returns:
        Le résultat retenu : le meilleur F1 macro parmi ceux qui atteignent le recall cible, ou
        à défaut le meilleur recall.
    """
    eligible = [r for r in results if r["recall"] >= TARGET_RECALL]
    if eligible:
        return max(eligible, key=lambda r: r["f1_macro"])
    return max(results, key=lambda r: r["recall"])


def run_benchmark(engine: sa.Engine, feature_set: FeatureSet = "baseline") -> list[dict[str, Any]]:
    """Entraîne et évalue les 3 familles de modèles, chacune trackée comme un run MLflow.

    Args:
        engine: Connexion SQLAlchemy vers PostgreSQL (Gold).
        feature_set: Cf. `feature_columns`. Par défaut `"baseline"`, pour rester comparable au
            protocole déjà publié (CLAUDE.md, référence baseline).

    Returns:
        Un résultat (cf. `evaluate_model`) par famille de modèle.
    """
    categorical, other = feature_columns(feature_set)
    df = prepare_features(load_gold_features(engine))
    train, valid, test = split_train_valid_test(df)

    x_train = _to_pandas(train, categorical, other)
    x_valid = _to_pandas(valid, categorical, other)
    x_test = _to_pandas(test, categorical, other)
    y_train = train[LABEL_COLUMN].to_pandas().astype(int)
    y_valid = valid[LABEL_COLUMN].to_pandas().astype(int)
    y_test = test[LABEL_COLUMN].to_pandas().astype(int)

    mlflow.set_experiment(EXPERIMENT_NAME)

    return [
        evaluate_model(
            name,
            feature_set,
            x_train,
            y_train,
            x_valid,
            y_valid,
            x_test,
            y_test,
            categorical,
            other,
        )
        for name in MODEL_BUILDERS
    ]


def register_best(best: dict[str, Any]) -> str | None:
    """Enregistre le meilleur modèle dans le Model Registry MLflow s'il franchit les seuils CDC.

    Utilise l'alias `staging`, pas l'API de stages dépréciée depuis MLflow 2.9 (cf. docstring
    module).

    Args:
        best: Sortie de `select_best`.

    Returns:
        Le numéro de version enregistrée, ou `None` si les seuils CDC ne sont pas franchis
        (promotion bloquée, cf. CLAUDE.md — « Bloquer la promotion en production »).
    """
    if not best["meets_gates"]:
        print(
            f"  [bloqué] {best['name']} : recall={best['recall']:.3f} / "
            f"f1_macro={best['f1_macro']:.3f} — sous les seuils CDC (0.80 / 0.70), "
            "non enregistré."
        )
        return None

    model_uri = f"runs:/{best['run_id']}/model"
    version = mlflow.register_model(model_uri, REGISTERED_MODEL_NAME)
    mlflow.MlflowClient().set_registered_model_alias(
        REGISTERED_MODEL_NAME, STAGING_ALIAS, version.version
    )
    return version.version


def _configure_s3_artifact_env(settings: Any) -> None:
    """Expose les identifiants MinIO aux variables d'environnement standard boto3/MLflow.

    MLflow (via boto3) lit ses identifiants S3 dans l'environnement du processus
    (`AWS_ACCESS_KEY_ID`/`AWS_SECRET_ACCESS_KEY`/`MLFLOW_S3_ENDPOINT_URL`), pas via un paramètre
    de son API Python — `mlflow.set_tracking_uri` ne suffit pas pour l'artifact store S3.

    Écrase ces variables plutôt que de ne les définir que si absentes (`setdefault`) : `.env`
    définit déjà `AWS_ACCESS_KEY_ID`/`AWS_SECRET_ACCESS_KEY` pour LocalStack/Terraform
    (identifiants `test`, sans rapport avec MinIO), chargées par `gravia.config` au démarrage —
    un `setdefault` ne les remplacerait jamais et `mlflow.*.log_model` échouerait à l'upload avec
    `InvalidAccessKeyId` (constaté en testant). L'écrasement ne modifie que l'environnement de ce
    process, pas `.env` ni le shell appelant.

    Args:
        settings: Configuration applicative (`gravia.config.Settings`).
    """
    import os

    store = settings.object_store
    os.environ["AWS_ACCESS_KEY_ID"] = store.access_key
    os.environ["AWS_SECRET_ACCESS_KEY"] = store.secret_key
    os.environ["MLFLOW_S3_ENDPOINT_URL"] = store.endpoint


def main() -> None:
    """Point d'entrée en ligne de commande."""
    import sys

    from gravia.config import get_settings

    sys.stdout.reconfigure(encoding="utf-8")

    settings = get_settings()
    _configure_s3_artifact_env(settings)
    mlflow.set_tracking_uri(settings.mlflow_tracking_uri)
    engine = sa.create_engine(settings.database.url)

    print("Entraînement (train 2019-2021 / validation 2022 / test 2023)...")
    results = run_benchmark(engine, feature_set="baseline")

    print("\nRésultats (seuil calibré sur validation, évalué sur le holdout 2023) :")
    for r in results:
        gate = "OK" if r["meets_gates"] else "sous seuil CDC"
        print(
            f"  {r['name']:20s} recall={r['recall']:.3f}  f1_macro={r['f1_macro']:.3f}  "
            f"seuil={r['threshold']:.2f}  [{gate}]"
        )

    best = select_best(results)
    print(
        f"\nMeilleur modèle : {best['name']} "
        f"(recall={best['recall']:.3f}, f1_macro={best['f1_macro']:.3f})"
    )

    version = register_best(best)
    if version:
        print(
            f"Enregistré dans le registry MLflow : {REGISTERED_MODEL_NAME} v{version} "
            f"(alias '{STAGING_ALIAS}')"
        )


if __name__ == "__main__":
    main()
