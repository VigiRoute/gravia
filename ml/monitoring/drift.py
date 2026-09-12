"""Détection de dérive (Evidently), pour EF-6/ENF-7 (cf. CLAUDE.md, seuils et métriques :
« PSI (dérive) < 0,2 → déclencher réentraînement »).

Deux dérives mesurées séparément — le CDC distingue explicitement qualité des données (Great
Expectations) et dérive du modèle (Evidently) :

1. **Dérive des features** (entrée) : distribution de chaque colonne consommée par le modèle
   `@staging`, comparée entre la référence d'entraînement (2019-2021) et le millésime le plus
   récent disponible (holdout test 2023 — jamais entraîné dessus, le meilleur proxy réel
   aujourd'hui pour « un nouveau millésime arrive », en attendant 2024, cf.
   docs/AVANCEMENT_GRAVIA.md « Pistes à évaluer plus tard »).
2. **Dérive de la prédiction** (sortie) : distribution des probabilités prédites par le modèle
   `@staging` sur la validation (2022, jamais vu à l'entraînement) vs le test (2023) — détecte une
   dégradation du modèle même en l'absence de vérité terrain immédiatement disponible pour un
   nouveau millésime réel.

PSI calculé par `evidently.presets.DataDriftPreset(method="psi")`, pas une implémentation maison :
c'est la même métrique que celle citée par le CDC, pas une approximation.

Pas un test pytest (même famille que `tests/performance/*`) : script à lancer à la main contre la
stack dev démarrée (PostgreSQL + MLflow), résultat dépendant de données réelles, pas une assertion
à faire tourner en CI à chaque commit.

Usage :
    python -m ml.monitoring.drift
"""

from __future__ import annotations

import pandas as pd
import sqlalchemy as sa
from evidently import DataDefinition, Dataset, Report
from evidently.presets import DataDriftPreset

from ml.features.gold_features import load_gold_features, prepare_features, split_train_valid_test
from ml.mlflow_env import configure_s3_artifact_env
from ml.serving.model import FEATURE_SET, load_staged_model
from ml.training.benchmark import _to_pandas, feature_columns

#: Seuil CDC (cf. CLAUDE.md, seuils et métriques) : au-delà, une colonne (ou la prédiction) est
#: considérée en dérive et un réentraînement est recommandé (EF-6).
PSI_THRESHOLD = 0.2

_VALUE_DRIFT_METRIC_TYPE = "evidently:metric_v2:ValueDrift"


def _split_numeric_boolean(frame: pd.DataFrame, other: list[str]) -> tuple[list[str], list[str]]:
    """Sépare les colonnes numériques des colonnes booléennes au sein de `other`.

    `feature_columns()` (cf. `ml/training/benchmark.py`) mélange volontairement numériques et
    booléennes (même traitement à l'entraînement : ni catégorielles ni castées). Pour Evidently,
    un booléen se modélise mieux comme catégoriel à 2 niveaux qu'une variable continue — d'où la
    séparation ici, par dtype réel plutôt qu'en recopiant une liste de noms de colonnes qui
    dupliquerait une connaissance déjà posée ailleurs.
    """
    boolean = [c for c in other if frame[c].dtype == bool]
    numeric = [c for c in other if c not in boolean]
    return numeric, boolean


def _run_psi_report(
    reference: pd.DataFrame,
    current: pd.DataFrame,
    categorical: list[str],
    numeric: list[str],
) -> dict[str, float]:
    """Calcule le PSI par colonne entre `reference` et `current` via Evidently.

    Args:
        reference: Distribution de référence (ex. période d'entraînement).
        current: Distribution courante à comparer (ex. millésime le plus récent).
        categorical: Colonnes catégorielles (dont les booléennes, cf. `_split_numeric_boolean`).
        numeric: Colonnes numériques continues.

    Returns:
        `{nom_de_colonne: psi}`, une entrée par colonne de `categorical + numeric`.
    """
    definition = DataDefinition(numerical_columns=numeric, categorical_columns=categorical)
    reference_dataset = Dataset.from_pandas(reference, data_definition=definition)
    current_dataset = Dataset.from_pandas(current, data_definition=definition)

    report = Report([DataDriftPreset(method="psi", threshold=PSI_THRESHOLD)])
    snapshot = report.run(current_data=current_dataset, reference_data=reference_dataset)

    psi_by_column: dict[str, float] = {}
    for metric in snapshot.dict()["metrics"]:
        config = metric["config"]
        if config.get("type") == _VALUE_DRIFT_METRIC_TYPE:
            psi_by_column[config["column"]] = metric["value"]
    return psi_by_column


def compute_feature_drift(
    reference: pd.DataFrame, current: pd.DataFrame, categorical: list[str], other: list[str]
) -> dict[str, float]:
    """PSI par feature du modèle entre référence d'entraînement et millésime courant.

    Args:
        reference: Features période d'entraînement (ex. train 2019-2021, cf.
            `split_train_valid_test`).
        current: Features du millésime à surveiller (ex. test 2023).
        categorical: Colonnes catégorielles du modèle (cf. `feature_columns`).
        other: Colonnes numériques/booléennes du modèle (cf. `feature_columns`).

    Returns:
        `{nom_de_feature: psi}`.
    """
    numeric, boolean = _split_numeric_boolean(reference, other)
    return _run_psi_report(reference, current, categorical + boolean, numeric)


def compute_prediction_drift(reference_proba: pd.Series, current_proba: pd.Series) -> float:
    """PSI entre deux distributions de probabilités prédites par le modèle `@staging`.

    Args:
        reference_proba: Probabilités prédites sur un jeu de référence (ex. validation 2022).
        current_proba: Probabilités prédites sur le jeu courant (ex. test 2023).

    Returns:
        Le PSI de la colonne unique `"probabilite"`.
    """
    reference_frame = pd.DataFrame({"probabilite": reference_proba})
    current_frame = pd.DataFrame({"probabilite": current_proba})
    psi_by_column = _run_psi_report(reference_frame, current_frame, [], ["probabilite"])
    return psi_by_column["probabilite"]


def main() -> None:
    """Calcule et affiche la dérive des features et de la prédiction sur les données réelles."""
    import sys

    sys.stdout.reconfigure(encoding="utf-8")

    from gravia.config import get_settings

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

    print("=== Dérive des features (train 2019-2021 → test 2023) ===")
    feature_drift = compute_feature_drift(reference, current, categorical, other)
    any_drift = False
    for column, psi in sorted(feature_drift.items(), key=lambda item: item[1], reverse=True):
        verdict = "DÉRIVE" if psi >= PSI_THRESHOLD else "ok"
        any_drift = any_drift or psi >= PSI_THRESHOLD
        print(f"  {column:25s} psi={psi:6.3f}  [{verdict}]")

    print("\n=== Dérive de la prédiction (validation 2022 → test 2023) ===")
    model = load_staged_model()
    valid_frame = _to_pandas(valid, categorical, other)
    reference_proba = pd.Series(model.booster.predict_proba(valid_frame)[:, 1])
    current_proba = pd.Series(model.booster.predict_proba(current)[:, 1])
    prediction_psi = compute_prediction_drift(reference_proba, current_proba)
    prediction_drift = prediction_psi >= PSI_THRESHOLD
    print(
        f"  probabilite               psi={prediction_psi:6.3f}  "
        f"[{'DÉRIVE' if prediction_drift else 'ok'}]"
    )

    print(f"\nSeuil CDC : PSI < {PSI_THRESHOLD}")
    if any_drift or prediction_drift:
        print("→ DÉRIVE DÉTECTÉE : réentraînement recommandé (CDC EF-6).")
    else:
        print("→ Aucune dérive au-delà du seuil CDC.")


if __name__ == "__main__":
    main()
