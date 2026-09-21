"""Benchmark de modèles pour la sévérité d'accident, tracké dans MLflow.

Compare quatre familles de modèles, comme prévu par docs/Architecture_GRAVIA.md §3 (« Benchmark :
régression logistique (baseline), Random Forest, LightGBM/XGBoost, modèle retenu selon les
métriques ») sur les splits produits par `ml/features/gold_features.py`. XGBoost a d'abord été
laissé de côté (le document cite « LightGBM/XGBoost » comme une alternative, LightGBM était déjà
la dépendance figée du projet, validée dans les notebooks à 0,001 près du baseline publié), puis
ajouté sur demande explicite pour comparer réellement les deux plutôt que de s'appuyer sur cette
justification seule.

**Servabilité, pas seulement performance** : `ml/serving/model.py::load_staged_model` charge le
modèle promu nativement via `mlflow.lightgbm.load_model` (nécessaire pour préserver le dtype
`category` pandas, cf. limitation ci-dessous) — il ne sait pas charger un modèle XGBoost. Si
XGBoost devait un jour battre LightGBM sur ce benchmark, `register_best` refuse de le promouvoir
tant que `ml/serving` ne sait pas le charger (cf. `SERVABLE_MODELS`), pour ne pas casser le
serving en production au premier réentraînement planifié qui tomberait sur ce cas.

Encodage : LightGBM consomme les colonnes catégorielles nativement (`category` pandas, comme dans
les notebooks) ; régression logistique et Random Forest n'ont pas de support catégoriel natif en
scikit-learn, elles passent par un `OneHotEncoder` dans un `ColumnTransformer`.

Hyperparamètres : recherche aléatoire (`RandomizedSearchCV`, `N_SEARCH_ITER` essais par modèle,
budget modeste choisi explicitement) validée par `TimeSeriesSplit` sur le train, pas un k-fold
aléatoire classique — les données sont chronologiques (2019-2021), un k-fold mélangerait les
années et validerait parfois sur du passé avec un modèle entraîné sur du futur. Appliquée aux
4 familles, y compris celles jamais promues (cf. servabilité ci-dessous) : `register_best` bloque
déjà toute promotion hors de `SERVABLE_MODELS`, donc tuner Random Forest/régression
logistique/XGBoost ne risque pas de promouvoir un modèle non servable, ça donne juste une
comparaison honnête entre familles à leur meilleur plutôt qu'à des réglages par défaut arbitraires.

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
point ouvert non résolu (CDC §13.7/§14, cf. notebooks/eval_seuil_par_zone.ipynb) — un seuil national
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
import mlflow.xgboost
import numpy as np
import pandas as pd
import polars as pl
import sqlalchemy as sa
import xgboost as xgb
from scipy.stats import loguniform, randint, uniform
from sklearn.compose import ColumnTransformer
from sklearn.ensemble import RandomForestClassifier
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import f1_score, recall_score
from sklearn.model_selection import RandomizedSearchCV, TimeSeriesSplit
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import OneHotEncoder

from ml.features.gold_features import (
    BASELINE_BOOLEAN_COLUMNS,
    BASELINE_CATEGORICAL_COLUMNS,
    BASELINE_NUMERIC_COLUMNS,
    ENRICHED_FLAG_COLUMNS,
    LABEL_COLUMN,
    YEAR_COLUMN,
    load_gold_features,
    prepare_features,
    split_train_valid_test,
)

RANDOM_STATE = 42

#: Seuils CDC (cf. CLAUDE.md, seuils et métriques).
TARGET_RECALL = 0.80
MIN_F1_MACRO = 0.70

#: Budget modeste (décision explicite de l'utilisateur, cf. AVANCEMENT_GRAVIA.md) : 15 essais par
#: modèle, pas un grid search exhaustif. Random Forest à 300 arbres est déjà le plus lent des 4 à
#: l'entraînement (cf. tests/integration/test_benchmark_mlflow.py) ; un budget plus large
#: multiplierait ce coût par le nombre de folds de validation croisée.
N_SEARCH_ITER = 15
N_SEARCH_SPLITS = 3

EXPERIMENT_NAME = "gravia-severity-classifier"
REGISTERED_MODEL_NAME = "gravia-severity-classifier"
STAGING_ALIAS = "staging"

#: Familles de modèles que `ml/serving/model.py::load_staged_model` sait charger nativement
#: (cf. docstring module). `register_best` ne promeut jamais un modèle hors de cet ensemble.
SERVABLE_MODELS = {"lightgbm"}

FeatureSet = Literal["baseline", "enriched"]


def feature_columns(feature_set: FeatureSet) -> tuple[list[str], list[str]]:
    """Colonnes catégorielles et numériques/booléennes pour une configuration de features.

    Args:
        feature_set: `"baseline"` reproduit exactement le protocole publié (recall 0,808 / F1
            macro 0,708). `"enriched"` y ajoute `ENRICHED_FLAG_COLUMNS` (meilleure configuration
            testée dans `notebooks/eval_enrichissement_vs_seuil.ipynb`, jamais promue en référence
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

    Repris de `notebooks/eda_baseline_baac.ipynb` : calibré sur la validation, jamais sur le test —
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


def _base_estimator(
    name: str, categorical: list[str], y_train: pd.Series
) -> Pipeline | lgb.LGBMClassifier | xgb.XGBClassifier:
    """Estimateur non ajusté par famille, avec seulement les paramètres structurels fixés.

    Les hyperparamètres de la famille (capacité du modèle, régularisation) restent au défaut
    scikit-learn/LightGBM/XGBoost ici : c'est `_search_hyperparameters` qui les fixe par
    recherche aléatoire, pas cette fonction.
    """
    if name in ("logistic_regression", "random_forest"):
        preprocessor = ColumnTransformer(
            [("cat", OneHotEncoder(handle_unknown="ignore"), categorical)], remainder="passthrough"
        )
        classifier = (
            LogisticRegression(class_weight="balanced", max_iter=1000, random_state=RANDOM_STATE)
            if name == "logistic_regression"
            else RandomForestClassifier(
                class_weight="balanced", random_state=RANDOM_STATE, n_jobs=-1
            )
        )
        return Pipeline([("preprocess", preprocessor), ("classify", classifier)])
    if name == "lightgbm":
        # Pas de `categorical_feature` explicite ici : les colonnes catégorielles sont déjà en
        # dtype `category` pandas (cf. `_to_pandas`), que LightGBM détecte nativement en mode
        # 'auto' (défaut), sans avoir besoin de le préciser à chaque appel de `.fit()` fait par
        # `RandomizedSearchCV` en interne.
        return lgb.LGBMClassifier(class_weight="balanced", random_state=RANDOM_STATE, verbosity=-1)
    if name == "xgboost":
        # scale_pos_weight = équivalent XGBoost de class_weight="balanced" (absent de son API) :
        # ratio négatifs/positifs, pour rééquilibrer sans sur-échantillonner.
        scale_pos_weight = (y_train == 0).sum() / (y_train == 1).sum()
        model = xgb.XGBClassifier(
            scale_pos_weight=scale_pos_weight,
            enable_categorical=True,
            tree_method="hist",
            random_state=RANDOM_STATE,
        )
        # scikit-learn 1.9 a retiré l'attribut de classe `_estimator_type` de `ClassifierMixin`
        # (remplacé par le système de tags `__sklearn_tags__`) ; XGBoost 3.0.5 s'appuie encore
        # dessus dans `save_model()` (`mlflow.xgboost.log_model` en dépend) et lève `TypeError:
        # _estimator_type undefined` sans ce contournement, constaté en testant.
        model._estimator_type = "classifier"
        return model
    raise ValueError(f"modèle inconnu : {name!r}")


#: Espace de recherche par famille. Clés préfixées `classify__` pour les deux modèles encapsulés
#: dans un `Pipeline` scikit-learn (cf. `_base_estimator`), noms d'attributs directs pour LightGBM
#: et XGBoost.
_PARAM_DISTRIBUTIONS: dict[str, dict[str, Any]] = {
    "logistic_regression": {
        "classify__C": loguniform(1e-3, 1e2),
    },
    "random_forest": {
        "classify__n_estimators": randint(100, 400),
        "classify__max_depth": randint(4, 30),
        "classify__min_samples_leaf": randint(1, 20),
        "classify__max_features": ["sqrt", "log2", None],
    },
    "lightgbm": {
        "n_estimators": randint(100, 500),
        "learning_rate": loguniform(0.01, 0.2),
        "num_leaves": randint(15, 127),
        "max_depth": randint(3, 15),
        "min_child_samples": randint(5, 100),
    },
    "xgboost": {
        "n_estimators": randint(100, 500),
        "learning_rate": loguniform(0.01, 0.2),
        "max_depth": randint(3, 12),
        "min_child_weight": randint(1, 10),
        "subsample": uniform(0.6, 0.4),  # borne haute exclusive : tire dans [0.6, 1.0)
    },
}

#: Familles de modèles comparées, dans l'ordre où elles sont entraînées/loggées dans MLflow.
MODEL_NAMES: tuple[str, ...] = ("logistic_regression", "random_forest", "lightgbm", "xgboost")

_MLFLOW_LOG_MODEL = {
    "logistic_regression": mlflow.sklearn.log_model,
    "random_forest": mlflow.sklearn.log_model,
    "lightgbm": mlflow.lightgbm.log_model,
    "xgboost": mlflow.xgboost.log_model,
}


def _search_hyperparameters(
    name: str, x_train: pd.DataFrame, y_train: pd.Series, categorical: list[str]
) -> tuple[Any, dict[str, Any]]:
    """Recherche aléatoire d'hyperparamètres, validée par split temporel, pas un k-fold aléatoire.

    `x_train`/`y_train` doivent déjà être triés chronologiquement (cf. `run_benchmark`) :
    `TimeSeriesSplit` découpe des blocs contigus par position de ligne, pas par valeur de date —
    un ordre aléatoire romprait la logique anti-fuite (validerait parfois sur du passé avec un
    modèle entraîné sur du futur).

    Args:
        name: Clé de `MODEL_NAMES`.
        x_train, y_train: Entraînement (2019-2021), trié chronologiquement.
        categorical: Colonnes catégorielles (pour construire l'estimateur de base).

    Returns:
        `(meilleur_estimateur_déjà_ajusté, meilleurs_hyperparamètres)`.
    """
    base = _base_estimator(name, categorical, y_train)
    search = RandomizedSearchCV(
        base,
        _PARAM_DISTRIBUTIONS[name],
        n_iter=N_SEARCH_ITER,
        scoring="average_precision",  # indépendant du seuil, recalibré séparément après coup
        cv=TimeSeriesSplit(n_splits=N_SEARCH_SPLITS),
        random_state=RANDOM_STATE,
        n_jobs=-1,
    )
    search.fit(x_train, y_train)
    best_estimator = search.best_estimator_
    if name == "xgboost":
        # `RandomizedSearchCV` reconstruit l'estimateur final par `clone()` + refit interne ;
        # `clone()` ne recopie que les paramètres du constructeur, pas les attributs d'instance
        # ajoutés après coup (constaté en testant : `best_estimator_` perdait le contournement
        # posé dans `_base_estimator`, faisant réapparaître `TypeError: _estimator_type
        # undefined` au moment du `mlflow.xgboost.log_model` qui suit). Reposé ici, sur l'objet
        # réellement retourné par la recherche.
        best_estimator._estimator_type = "classifier"
    return best_estimator, search.best_params_


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
        name: Clé de `MODEL_NAMES`.
        feature_set: Configuration de features utilisée (traçabilité du run MLflow).
        x_train, y_train: Entraînement (2019-2021), trié chronologiquement (cf. `run_benchmark`).
        x_valid, y_valid: Validation (2022) — sert uniquement à calibrer le seuil.
        x_test, y_test: Test (2023) — holdout, jamais vu avant l'évaluation finale.
        categorical: Colonnes catégorielles.
        other: Colonnes numériques/booléennes.

    Returns:
        Nom, seuil calibré, métriques sur le test, `run_id` MLflow et si les seuils CDC sont
        franchis.
    """
    with mlflow.start_run(run_name=f"{name}-{feature_set}") as run:
        model, best_params = _search_hyperparameters(name, x_train, y_train, categorical)

        mlflow.log_params(
            {
                "model": name,
                "feature_set": feature_set,
                "n_features": len(categorical) + len(other),
                "n_search_iter": N_SEARCH_ITER,
                "target_recall": TARGET_RECALL,
                "random_state": RANDOM_STATE,
                **best_params,
            }
        )

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
    """Entraîne et évalue les 4 familles de modèles, chacune trackée comme un run MLflow.

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
    # Ordre chronologique requis par `TimeSeriesSplit` dans `_search_hyperparameters` : `train`
    # n'est pas garanti trié en sortie de `load_gold_features` (ordre de la requête SQL, pas de
    # l'accident). `annee` seule suffirait à éviter la fuite ; `mois`/`jour_semaine` affinent
    # l'ordre à l'intérieur d'une même année sans coût supplémentaire.
    train = train.sort([YEAR_COLUMN, "mois", "jour_semaine"])

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
        for name in MODEL_NAMES
    ]


def register_best(best: dict[str, Any]) -> str | None:
    """Enregistre le meilleur modèle dans le Model Registry MLflow s'il franchit les seuils CDC.

    Utilise l'alias `staging`, pas l'API de stages dépréciée depuis MLflow 2.9 (cf. docstring
    module).

    Args:
        best: Sortie de `select_best`.

    Returns:
        Le numéro de version enregistrée, ou `None` si les seuils CDC ne sont pas franchis
        (promotion bloquée, cf. CLAUDE.md — « Bloquer la promotion en production ») ou si
        `ml/serving` ne sait pas charger cette famille de modèle (cf. `SERVABLE_MODELS`).
    """
    if not best["meets_gates"]:
        print(
            f"  [bloqué] {best['name']} : recall={best['recall']:.3f} / "
            f"f1_macro={best['f1_macro']:.3f}, sous les seuils CDC (0.80 / 0.70), "
            "non enregistré."
        )
        return None

    if best["name"] not in SERVABLE_MODELS:
        print(
            f"  [bloqué] {best['name']} franchit les seuils CDC mais ml/serving/model.py ne sait "
            f"pas encore le charger nativement (seul {sorted(SERVABLE_MODELS)} l'est) : non "
            "enregistré, pour ne pas casser le serving en production. Mettre à jour "
            "ml/serving/model.py avant de promouvoir cette famille de modèle."
        )
        return None

    model_uri = f"runs:/{best['run_id']}/model"
    version = mlflow.register_model(model_uri, REGISTERED_MODEL_NAME)
    mlflow.MlflowClient().set_registered_model_alias(
        REGISTERED_MODEL_NAME, STAGING_ALIAS, version.version
    )
    return version.version


def main() -> None:
    """Point d'entrée en ligne de commande."""
    import argparse
    import sys

    from gravia.config import get_settings
    from ml.mlflow_env import configure_s3_artifact_env

    sys.stdout.reconfigure(encoding="utf-8")

    # Défaut "enriched", pas "baseline" : c'est la config réellement déployée à l'alias
    # `staging` (cf. ml/serving/model.py::FEATURE_SET, docs/ml_training_results.md). Trouvé en
    # auditant : ce point d'entrée est aussi celui appelé par .github/workflows/retrain.yml —
    # un réentraînement réel avec l'ancien défaut "baseline" aurait promu un modèle à 20
    # features par-dessus le modèle à 24 features attendu par le serving, cassant la prédiction.
    parser = argparse.ArgumentParser(description="Benchmark et (ré)entraînement GRAVIA.")
    parser.add_argument(
        "--feature-set",
        choices=["baseline", "enriched"],
        default="enriched",
        help="Configuration de features (défaut : enriched, la config réellement déployée). "
        "'baseline' ne reproduit que la référence historique (CLAUDE.md) — l'utiliser pour un "
        "réentraînement réel promouvrait un modèle à 20 features par-dessus celui à 24 features "
        "attendu par ml/serving/model.py.",
    )
    args = parser.parse_args()

    settings = get_settings()
    configure_s3_artifact_env(settings)
    mlflow.set_tracking_uri(settings.mlflow_tracking_uri)
    engine = sa.create_engine(settings.database.url)

    print(
        f"Entraînement (train 2019-2021 / validation 2022 / test 2023, "
        f"config={args.feature_set}, recherche d'hyperparamètres : {N_SEARCH_ITER} essais "
        f"par modèle)..."
    )
    results = run_benchmark(engine, feature_set=args.feature_set)

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
