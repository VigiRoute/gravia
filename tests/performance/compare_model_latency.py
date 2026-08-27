"""Compare la latence de prédiction pure des 3 modèles candidats (config `"enriched"`).

Pas un test pytest (même raison que `load_test_serving.py` : script à lancer à la main contre la
stack dev démarrée, résultat dépendant de la machine hôte, pas une assertion à faire tourner en
CI). Répond à une question distincte de `load_test_serving.py` : celui-là mesure la capacité de
*l'API déployée* (LightGBM) sous charge concurrente ; celui-ci isole le coût de calcul du
*modèle lui-même*, sans HTTP ni serveur, pour comparer les 3 candidats du benchmark — un modèle
moins bon en recall/F1 (cf. docs/ml_training_results.md) pourrait rester un choix pertinent s'il
était nettement plus rapide, question jamais posée pendant le benchmark lui-même.

Charge chaque modèle directement depuis son run MLflow (`runs:/<run_id>/model`), pas depuis le
registry : seul LightGBM y est enregistré (modèle retenu), les deux autres restent des runs
trackés mais non promus.

Résultat (2026-08-27, sur le holdout 2023, une prédiction à la fois — ce que fait l'API à chaque
requête) :

| Modèle               | predict p50 | predict p95 | SHAP p50 | SHAP p95 |
|----------------------|------------:|------------:|---------:|---------:|
| logistic_regression   |      2,9 ms |      3,4 ms |      n/a |      n/a |
| random_forest          |     30,1 ms |     44,0 ms |      n/a |      n/a |
| **lightgbm (déployé)** |      3,9 ms |      5,1 ms |   4,6 ms |   6,3 ms |

→ **LightGBM n'est pas seulement le meilleur modèle du benchmark (recall 0,807 / F1 macro 0,727,
cf. docs/ml_training_results.md) : c'est aussi l'un des plus rapides**, quasiment à égalité avec
la régression logistique et ~8× plus rapide que Random Forest (300 arbres, chacun parcouru en
entier à chaque prédiction). Choisir LightGBM n'a donc pas sacrifié de performance brute pour la
justesse — les deux vont dans le même sens ici, pas de compromis à arbitrer.

SHAP non mesuré pour `logistic_regression`/`random_forest` : tous deux enveloppés dans un
`sklearn.Pipeline` (`OneHotEncoder` + classifieur, cf. `ml/training/benchmark.py`) pour
l'encodage catégoriel — `shap.TreeExplainer` ne s'applique ni à un `Pipeline` tel quel
(`random_forest`) ni à un modèle linéaire (`logistic_regression`). `ml/serving` ne câble une
explication SHAP que pour LightGBM aujourd'hui (cf. `ml/serving/model.py`), cohérent avec le
seul modèle réellement déployé.

Usage :
    python -m tests.performance.compare_model_latency
"""

from __future__ import annotations

import time

import mlflow
import shap
import sqlalchemy as sa

from gravia.config import get_settings
from ml.features.gold_features import load_gold_features, prepare_features, split_train_valid_test
from ml.mlflow_env import configure_s3_artifact_env
from ml.training.benchmark import _to_pandas, feature_columns

#: Runs MLflow des 3 candidats du benchmark enriched (cf. docs/ml_training_results.md). Figés en
#: dur : ce script compare des runs déjà produits, il n'en relance pas de nouveaux.
RUN_IDS = {
    "logistic_regression": "11cb6fde042f41ee8553c38da2961e0d",
    "random_forest": "c3d70d29f8414ab39b2dfbec6cef95ef",
    "lightgbm": "9f505d654d484c9d96639086979fd0d5",
}

#: Prédictions répétées pour mesurer une médiane/p95 stables.
N_ITERATIONS = 300


def _load_model(name: str, run_id: str):
    """Charge un modèle directement depuis son run MLflow (pas depuis le registry)."""
    uri = f"runs:/{run_id}/model"
    if name == "lightgbm":
        return mlflow.lightgbm.load_model(uri)
    return mlflow.sklearn.load_model(uri)


def _time_calls(fn, n: int = N_ITERATIONS) -> tuple[float, float]:
    """Exécute `fn` `n` fois et renvoie `(p50, p95)` en millisecondes."""
    durations = []
    for _ in range(n):
        start = time.perf_counter()
        fn()
        durations.append((time.perf_counter() - start) * 1000)
    durations.sort()
    return durations[len(durations) // 2], durations[int(len(durations) * 0.95)]


def main() -> None:
    """Charge les 3 modèles candidats et compare leur latence de prédiction (et SHAP)."""
    import sys

    sys.stdout.reconfigure(encoding="utf-8")

    settings = get_settings()
    configure_s3_artifact_env(settings)
    mlflow.set_tracking_uri(settings.mlflow_tracking_uri)

    engine = sa.create_engine(settings.database.url)
    categorical, other = feature_columns("enriched")
    df = prepare_features(load_gold_features(engine))
    _, _, test = split_train_valid_test(df)
    one_row = _to_pandas(test, categorical, other).iloc[[0]]

    header = (
        f"{'modèle':22s} {'predict p50':>12s} {'predict p95':>12s} "
        f"{'SHAP p50':>10s} {'SHAP p95':>10s}"
    )
    print(header)
    for name, run_id in RUN_IDS.items():
        model = _load_model(name, run_id)
        predict_p50, predict_p95 = _time_calls(lambda m=model: m.predict_proba(one_row))

        if name == "lightgbm":
            explainer = shap.TreeExplainer(model)
            shap_p50, shap_p95 = _time_calls(lambda e=explainer: e.shap_values(one_row))
            shap_cols = f"{shap_p50:8.1f}ms {shap_p95:8.1f}ms"
        else:
            shap_cols = f"{'n/a':>10s} {'n/a':>10s}"

        print(f"{name:22s} {predict_p50:9.2f}ms {predict_p95:9.2f}ms {shap_cols}")


if __name__ == "__main__":
    main()
