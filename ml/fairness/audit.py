"""Tests d'équité du modèle `@staging` (CDC EC-6 : « IA éthique : tests d'équité (parité selon
âge/sexe, equalized odds), documentation et atténuation des biais »).

Le modèle ne reçoit ni l'âge ni le sexe en feature (cf. `ml/features/gold_features.py`) : ce
module vérifie que ses prédictions ne sont pas systématiquement moins fiables pour un
sous-groupe protégé malgré cela — l'équité peut se rompre via des corrélations indirectes (type
de véhicule, zone…) sans jamais utiliser l'attribut sensible en entrée.

Attribut sensible retenu : le conducteur (`catu == 1`, même lecture du code déjà vérifiée dans
`gravia.gold::aggregate_usagers`) de chaque accident. **Limité aux accidents à un seul
conducteur identifié** (38 % du test 2023, 20 830/54 822 — constaté en inspectant les données
réelles) : la majorité des accidents impliquent plusieurs véhicules donc plusieurs conducteurs,
dont le sexe/tranche d'âge peuvent différer, sans façon non arbitraire de n'en retenir un seul.
Retenir le conducteur le plus gravement blessé aurait biaisé le test en sélectionnant sur
l'issue même qu'on évalue (fuite méthodologique) — écarté pour cette raison.

Deux métriques par sous-groupe, sur le holdout test 2023 (jamais vu à l'entraînement, comme le
reste du benchmark, cf. CLAUDE.md, référence baseline) :
- **Parité démographique** : taux de prédiction « grave » par groupe.
- ***Equalized odds*** : rappel (TPR) et taux de faux positifs (FPR) par groupe, rapportés en
  écart absolu entre sous-groupes — pas de seuil pass/fail inventé : contrairement à
  PSI/recall/F1/couverture (cf. CLAUDE.md, seuils et métriques), le CDC ne fixe aucun seuil
  numérique pour l'équité ; en poser un ici serait arbitraire, pas une exigence retranscrite.

Usage :
    python -m ml.fairness.audit
"""

from __future__ import annotations

import pandas as pd
import polars as pl
import sqlalchemy as sa

from gravia.config import Settings, get_settings
from gravia.silver import silver_path
from ml.features.gold_features import (
    TEST_YEAR,
    load_gold_features,
    prepare_features,
    split_train_valid_test,
)
from ml.mlflow_env import configure_s3_artifact_env
from ml.serving.model import FEATURE_SET, load_staged_model
from ml.training.benchmark import _to_pandas, feature_columns

#: cf. dictionnaire ONISR (rubrique usagers) ; -1 = non renseigné, exclu de l'analyse.
SEXE_LABELS: dict[int, str] = {1: "Homme", 2: "Femme"}

#: Valeur manquante, pas une catégorie protégée (cf. `gravia.silver.AGE_BUCKET_UNKNOWN`).
AGE_BUCKET_EXCLUDED = "Inconnu"


def load_single_driver_attributes(settings: Settings) -> pl.DataFrame:
    """Sexe/tranche d'âge du conducteur, un par accident à conducteur unique identifié (2023).

    Args:
        settings: Configuration (chemins Silver).

    Returns:
        `accident_id`, `sexe`, `tranche_age` — une ligne par accident où exactement un usager a
        `catu == 1` (conducteur). Les accidents à plusieurs conducteurs (multi-véhicules,
        majoritaires, cf. docstring module) ou sans conducteur identifié sont exclus.
    """
    usagers = pl.read_parquet(silver_path("usagers", TEST_YEAR, settings))
    drivers = usagers.filter(pl.col("catu") == 1)
    single_driver_accidents = (
        drivers.group_by("Num_Acc")
        .agg(pl.len().alias("n_drivers"))
        .filter(pl.col("n_drivers") == 1)
    )
    return drivers.join(
        single_driver_accidents.select("Num_Acc"), on="Num_Acc", how="inner"
    ).select(pl.col("Num_Acc").alias("accident_id"), "sexe", "tranche_age")


def build_audit_frame(engine: sa.Engine, settings: Settings) -> pd.DataFrame:
    """Construit la table d'audit : prédictions du modèle `@staging` + attributs sensibles.

    Args:
        engine: Connexion PostgreSQL (Gold).
        settings: Configuration (chemins Silver, MLflow).

    Returns:
        Une ligne par accident à conducteur unique du test 2023, colonnes `is_grave` (réel),
        `pred_grave` (prédit), `sexe`, `tranche_age`.
    """
    categorical, other = feature_columns(FEATURE_SET)
    df = prepare_features(load_gold_features(engine))
    _, _, test = split_train_valid_test(df)

    drivers = load_single_driver_attributes(settings)
    test = test.join(drivers, on="accident_id", how="inner")

    frame = _to_pandas(test, categorical, other)
    model = load_staged_model()
    probability = model.booster.predict_proba(frame)[:, 1]

    audit = test.select("is_grave", "sexe", "tranche_age").to_pandas()
    audit["is_grave"] = audit["is_grave"].astype(int)
    audit["pred_grave"] = (probability >= model.threshold).astype(int)
    return audit


def compute_group_metrics(df: pd.DataFrame, group_col: str) -> pd.DataFrame:
    """Taux de prédiction positive, rappel (TPR) et taux de faux positifs (FPR) par sous-groupe.

    Args:
        df: Table d'audit (cf. `build_audit_frame`), colonnes `is_grave`/`pred_grave`/`group_col`.
        group_col: Colonne de regroupement (`sexe` ou `tranche_age`).

    Returns:
        Une ligne par valeur du groupe : `n`, `taux_grave_reel`, `taux_predit_grave`, `recall`,
        `fpr`.
    """
    rows = []
    for group, subset in df.groupby(group_col):
        y_true = subset["is_grave"]
        y_pred = subset["pred_grave"]
        positives = int(y_true.sum())
        negatives = len(y_true) - positives
        true_positive = int(((y_true == 1) & (y_pred == 1)).sum())
        false_positive = int(((y_true == 0) & (y_pred == 1)).sum())
        rows.append(
            {
                "groupe": group,
                "n": len(subset),
                "taux_grave_reel": y_true.mean(),
                "taux_predit_grave": y_pred.mean(),
                "recall": true_positive / positives if positives else float("nan"),
                "fpr": false_positive / negatives if negatives else float("nan"),
            }
        )
    return pd.DataFrame(rows)


def max_gap(metrics: pd.DataFrame, column: str) -> float:
    """Écart maximal entre sous-groupes pour une métrique (`recall` ou `fpr`).

    Args:
        metrics: Sortie de `compute_group_metrics`.
        column: Colonne à comparer.

    Returns:
        `max - min` sur les valeurs non manquantes, ou `nan` s'il y a moins de 2 groupes valides.
    """
    valid = metrics[column].dropna()
    return valid.max() - valid.min() if len(valid) >= 2 else float("nan")


def main() -> None:
    """Audite l'équité du modèle `@staging` par sexe et tranche d'âge du conducteur (test 2023)."""
    import sys

    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")

    settings = get_settings()
    configure_s3_artifact_env(settings)

    import mlflow

    mlflow.set_tracking_uri(settings.mlflow_tracking_uri)

    engine = sa.create_engine(settings.database.url)
    audit = build_audit_frame(engine, settings)

    print(f"Accidents audités (conducteur unique identifié, test {TEST_YEAR}) : {len(audit)}\n")

    print("=== Par sexe du conducteur ===")
    sexe_audit = audit[audit["sexe"].isin(SEXE_LABELS)].assign(
        sexe=lambda d: d["sexe"].map(SEXE_LABELS)
    )
    sexe_metrics = compute_group_metrics(sexe_audit, "sexe")
    print(sexe_metrics.to_string(index=False))
    print(f"Écart de rappel (TPR) : {max_gap(sexe_metrics, 'recall'):.3f}")
    print(f"Écart de faux positifs (FPR) : {max_gap(sexe_metrics, 'fpr'):.3f}")

    print("\n=== Par tranche d'âge du conducteur ===")
    age_audit = audit[audit["tranche_age"] != AGE_BUCKET_EXCLUDED]
    age_metrics = compute_group_metrics(age_audit, "tranche_age")
    print(age_metrics.to_string(index=False))
    print(f"Écart de rappel (TPR) : {max_gap(age_metrics, 'recall'):.3f}")
    print(f"Écart de faux positifs (FPR) : {max_gap(age_metrics, 'fpr'):.3f}")


if __name__ == "__main__":
    main()
