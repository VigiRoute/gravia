"""Graphiques des résultats déjà publiés (benchmark, équité, dérive) — destinés aux slides et
vidéos de démonstration (CDC §15), pas au pipeline applicatif. Complète les tableaux markdown
déjà écrits (docs/ml_training_results.md, docs/model_fairness.md) par des visuels : plus lisibles
à l'oral qu'un tableau, cf. échange avec l'utilisateur sur ce qui manquait pour les vidéos.

Deux sources de données différentes, assumées :
- **Équité et dérive** : recalculées pour de vrai contre la stack dev (`ml.fairness.audit`,
  `ml.monitoring.drift`) — mêmes résultats que documentés, pas une resaisie manuelle qui pourrait
  diverger silencieusement des vrais calculs.
- **Benchmark de modèles** : chiffres repris tels quels de `docs/ml_training_results.md`
  (déjà validés à 0,001 près contre `notebooks/eda_baseline_baac.ipynb`), pas recalculés ici —
  relancer `ml.training.benchmark` prend plusieurs minutes (Random Forest, 300 arbres sur
  273 226 lignes) pour produire des nombres déjà connus et publiés.

Usage :
    python -m docs.generate_result_charts

Écrit dans docs/img/ (créé si absent).
"""

from __future__ import annotations

import sys
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import pandas as pd
import sqlalchemy as sa

from gravia.config import get_settings
from ml.fairness.audit import (
    AGE_BUCKET_EXCLUDED,
    SEXE_LABELS,
    build_audit_frame,
    compute_group_metrics,
)
from ml.features.gold_features import load_gold_features, prepare_features, split_train_valid_test
from ml.mlflow_env import configure_s3_artifact_env
from ml.monitoring.drift import PSI_THRESHOLD, compute_feature_drift
from ml.training.benchmark import _to_pandas, feature_columns

OUTPUT_DIR = Path(__file__).parent / "img"

#: Seuils CDC (cf. CLAUDE.md, seuils et métriques).
RECALL_THRESHOLD = 0.80
F1_THRESHOLD = 0.70

#: cf. docs/ml_training_results.md — déjà publiés et validés, pas recalculés (cf. docstring).
BENCHMARK_RESULTS: dict[str, dict[str, dict[str, float]]] = {
    "baseline": {
        "Régression\nlogistique": {"recall": 0.804, "f1_macro": 0.695},
        "Random\nForest": {"recall": 0.801, "f1_macro": 0.690},
        "LightGBM": {"recall": 0.807, "f1_macro": 0.707},
    },
    "enriched": {
        "Régression\nlogistique": {"recall": 0.806, "f1_macro": 0.708},
        "Random\nForest": {"recall": 0.810, "f1_macro": 0.711},
        "LightGBM": {"recall": 0.807, "f1_macro": 0.727},
        # Ajouté le 2026-09-20, testé sur "enriched" uniquement (pas "baseline") : sous le seuil
        # CDC de recall, cf. docs/ml_training_results.md, section "XGBoost : ajouté et écarté".
        "XGBoost": {"recall": 0.711, "f1_macro": 0.520},
    },
}


def plot_benchmark_comparison(output_dir: Path) -> Path:
    """Recall/F1 macro des modèles, config baseline vs enriched, avec les seuils CDC."""
    fig, axes = plt.subplots(1, 2, figsize=(11, 4.5), sharey=True)

    for ax, (config_name, results) in zip(axes, BENCHMARK_RESULTS.items(), strict=True):
        models = list(results.keys())
        x = range(len(models))
        width = 0.35
        recalls = [results[m]["recall"] for m in models]
        f1s = [results[m]["f1_macro"] for m in models]

        ax.bar([i - width / 2 for i in x], recalls, width, label="Recall (grave)", color="#4C72B0")
        ax.bar([i + width / 2 for i in x], f1s, width, label="F1 macro", color="#DD8452")
        ax.axhline(RECALL_THRESHOLD, color="#4C72B0", linestyle="--", linewidth=1, alpha=0.6)
        ax.axhline(F1_THRESHOLD, color="#DD8452", linestyle="--", linewidth=1, alpha=0.6)
        ax.set_xticks(list(x))
        ax.set_xticklabels(models)
        ax.set_title(f"Config « {config_name} »")
        ax.set_ylim(0.0, 0.85)

    axes[0].set_ylabel("Score")
    axes[0].legend(loc="lower right", fontsize=8)
    fig.suptitle("Benchmark de modèles, traits pointillés = seuils CDC (recall ≥ 0,80 / F1 ≥ 0,70)")
    fig.tight_layout()

    path = output_dir / "benchmark_recall_f1.png"
    fig.savefig(path, dpi=150)
    plt.close(fig)
    return path


def plot_fairness_gaps(output_dir: Path, engine: sa.Engine, settings) -> Path:
    """Rappel (TPR) et taux de faux positifs (FPR) par sexe et par tranche d'âge du conducteur."""
    audit = build_audit_frame(engine, settings)

    sexe_audit = audit[audit["sexe"].isin(SEXE_LABELS)].assign(
        sexe=lambda d: d["sexe"].map(SEXE_LABELS)
    )
    sexe_metrics = compute_group_metrics(sexe_audit, "sexe")

    age_audit = audit[audit["tranche_age"] != AGE_BUCKET_EXCLUDED]
    age_metrics = compute_group_metrics(age_audit, "tranche_age").sort_values("groupe")

    fig, axes = plt.subplots(1, 2, figsize=(12, 4.5))

    for ax, metrics, title in (
        (axes[0], sexe_metrics, "Par sexe du conducteur"),
        (axes[1], age_metrics, "Par tranche d'âge du conducteur"),
    ):
        x = range(len(metrics))
        width = 0.35
        ax.bar(
            [i - width / 2 for i in x],
            metrics["recall"],
            width,
            label="Rappel (TPR)",
            color="#55A868",
        )
        ax.bar(
            [i + width / 2 for i in x],
            metrics["fpr"],
            width,
            label="Taux de faux positifs (FPR)",
            color="#C44E52",
        )
        ax.set_xticks(list(x))
        ax.set_xticklabels(metrics["groupe"], rotation=0 if title.startswith("Par sexe") else 30)
        ax.set_title(title)
        ax.set_ylim(0, 1)

    axes[0].set_ylabel("Score")
    axes[0].legend(loc="upper right", fontsize=8)
    fig.suptitle("Équité du modèle @staging — conducteur unique identifié, test 2023")
    fig.tight_layout()

    path = output_dir / "fairness_gaps.png"
    fig.savefig(path, dpi=150)
    plt.close(fig)
    return path


def plot_drift_psi(output_dir: Path, engine: sa.Engine) -> Path:
    """PSI par feature, train 2019-2021 vs test 2023, avec le seuil CDC (0,2)."""
    categorical, other = feature_columns("enriched")
    df = prepare_features(load_gold_features(engine))
    train, _, test = split_train_valid_test(df)
    reference = _to_pandas(train, categorical, other)
    current = _to_pandas(test, categorical, other)

    psi_by_column = compute_feature_drift(reference, current, categorical, other)
    series = pd.Series(psi_by_column).sort_values()

    colors = ["#C44E52" if v >= PSI_THRESHOLD else "#4C72B0" for v in series]

    fig, ax = plt.subplots(figsize=(8, 8))
    ax.barh(series.index, series.values, color=colors)
    ax.axvline(
        PSI_THRESHOLD,
        color="black",
        linestyle="--",
        linewidth=1,
        label=f"Seuil CDC ({PSI_THRESHOLD})",
    )
    ax.set_xlabel("PSI (train 2019-2021 → test 2023)")
    ax.set_title("Dérive par feature — modèle @staging")
    ax.legend(loc="lower right", fontsize=8)
    fig.tight_layout()

    path = output_dir / "drift_psi.png"
    fig.savefig(path, dpi=150)
    plt.close(fig)
    return path


def main() -> None:
    """Génère les 3 graphiques dans docs/img/."""
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")

    OUTPUT_DIR.mkdir(exist_ok=True)

    settings = get_settings()
    configure_s3_artifact_env(settings)

    import mlflow

    mlflow.set_tracking_uri(settings.mlflow_tracking_uri)
    engine = sa.create_engine(settings.database.url)

    print("Benchmark...")
    print(f"  -> {plot_benchmark_comparison(OUTPUT_DIR)}")

    print("Équité...")
    print(f"  -> {plot_fairness_gaps(OUTPUT_DIR, engine, settings)}")

    print("Dérive...")
    print(f"  -> {plot_drift_psi(OUTPUT_DIR, engine)}")


if __name__ == "__main__":
    main()
