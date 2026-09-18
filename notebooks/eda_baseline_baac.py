"""EDA + modèle baseline sur le BAAC (2019-2023), SANS enrichissement externe.

Ce script fournit la référence à laquelle comparer tout enrichissement (trafic, bulletins,
cf. CDC_GRAVIA.md §13.6) avant de l'intégrer : uniquement les 4 tables BAAC natives
(caractéristiques, lieux, véhicules, usagers), sans météo/géo/trafic/bulletins. L'EDA elle-même
(fonction `run_eda`) est volontairement légère — distribution de la cible par année, taux de
valeurs nulles — pas une exploration visuelle : aucune bibliothèque de graphiques n'est utilisée
ici (les seuls graphiques du projet, générés par `docs/generate_result_charts.py`, portent sur
des résultats déjà obtenus, pas sur l'exploration des données).

Périmètre : 2019-2023 (schéma BAAC stable sur cette période — cf. piège ci-dessous), toute la
France (pas de restriction géographique, contrairement à l'exploration trafic Paris qui reste
un enrichissement optionnel séparé).

PIÈGES DE SCHÉMA constatés en comparant les 5 millésimes (BAAC change de convention chaque
année, ce n'est pas anecdotique) :
  - `Num_Acc` renommé `Accident_Id` dans carcteristiques-2022.csv uniquement.
  - Fichier caractéristiques 2021/2022 orthographié "carcteristiques" (sans le "a") par le
    producteur — pas une typo de notre part, le nom réel du fichier sur data.gouv.fr.
  - `jour`/`mois` : zéro-paddés certaines années ("05"), pas d'autres ("5") -> toujours caster
    en Int64, ne jamais dépendre du format texte.
  - `id_usager` absent des usagers 2019-2020 (ajouté à partir de 2021) -> non utilisé ici de
    toute façon (jointure sur Num_Acc uniquement).

ANTI-LEAKAGE (cf. CLAUDE.md) : on exclut explicitement `secu1/2/3` (équipement de sécurité,
connu seulement après enquête) et `manv` (manœuvre détaillée, reconstituée après enquête). Le
lat/long brut n'est PAS utilisé comme feature (donnée personnelle à agréger/pseudonymiser
plus tard, cf. Gouvernance_GRAVIA.md) ; seul le département (`dep`) sert de granularité
géographique pour ce baseline.

SIMPLIFICATION ASSUMÉE : les caractéristiques véhicule (catv, choc, obs, obsm...) ne sont PAS
encore agrégées au niveau accident dans ce premier baseline — seul le nombre de véhicules
impliqués est retenu. Le detail véhicule est un candidat pour une itération ultérieure de
feature engineering (ml/features/), pas pour cette référence.

RÉSULTATS (273 226 accidents France entière, 2019-2023) :
  - Taux de gravité (is_grave = tué ou hospitalisé) stable autour de 35-36 % chaque année.
    Bien plus élevé que le ~9 % observé sur le sous-ensemble Paris (cf. exploration trafic) :
    cohérent, le mix national inclut les accidents ruraux/autoroutiers à vitesse élevée,
    plus sévères que la moyenne urbaine dense parisienne.
  - 0 % de valeurs NULL SQL sur les features retenues (contraste net avec les sources trafic
    externes, où le bruit/manquant était le problème central) — mais ce chiffre ne compte pas
    la sentinelle BAAC `" -1"` (non renseigné, cf. CLAUDE.md, pièges de schéma) puisque les
    colonnes sont lues en texte sans nettoyage ici : le non-renseigné explicite atteint jusqu'à
    5,9 % sur `circ`, ~2 % sur `vma`, ~1 % sur `vosp`/`infra` (mesuré sur Gold). Écart assumé :
    ce baseline n'a pas besoin de distinguer "non renseigné" de "absent" pour ses résultats,
    contrairement à `ml/features` et `Great Expectations` qui, eux, la traitent explicitement.
  - Split temporel à 3 voies : train 2019-2021, validation 2022 (calibrage du seuil de
    décision), test 2023 (holdout jamais vu, y compris pour le calibrage).
      * Seuil par défaut (0,5)      : recall grave 0,760, F1 macro 0,721 — recall sous le
        seuil CDC.
      * Seuil calibré sur 2022 (0,44), appliqué tel quel à 2023 : **recall grave 0,808** et
        **F1 macro 0,708** → LES DEUX SEUILS CDC SONT ATTEINTS (recall >= 0,80, F1 macro
        >= 0,70), avec une méthodologie propre (aucune fuite : le seuil n'est jamais choisi
        sur les données d'évaluation finale).
  - Feature la plus importante de loin : `dep` (département) — cohérent avec l'hétérogénéité
    géographique de la sévérité (urbain/rural, vitesses autorisées différentes par zone).
  → Référence baseline posée SANS aucun enrichissement externe. Le trafic Paris (signal
    confirmé, cf. explo_trafic_paris_correlation_annuel.py) est un candidat pour améliorer ce
    baseline sur le sous-ensemble parisien ; reste à mesurer le gain réel par rapport à cette
    référence avant de l'intégrer en production.
"""

import sys
from pathlib import Path

import lightgbm as lgb
import numpy as np
import polars as pl
from sklearn.metrics import classification_report, f1_score, recall_score

TARGET_RECALL = 0.80  # seuil CDC, priorité au recall (coût asymétrique : rater un grave coûte
# plus cher qu'une fausse alerte, cf. Presentation_Projet_GRAVIA_Jedha.md).

sys.stdout.reconfigure(encoding="utf-8")

BAAC_DIR = Path(__file__).resolve().parent.parent / "data" / "raw" / "baac"

# (année -> nom de fichier caractéristiques, coquilles/renommages inclus)
CARACT_FILES = {
    2019: "caracteristiques-2019.csv",
    2020: "caracteristiques-2020.csv",
    2021: "carcteristiques-2021.csv",
    2022: "carcteristiques-2022.csv",
    2023: "caract-2023.csv",
}

# Features anti-leakage retenues pour ce baseline (connues au moment du signalement).
CARACT_FEATURES = ["lum", "dep", "agg", "int", "atm", "col"]
LIEUX_FEATURES = ["catr", "circ", "nbv", "vosp", "prof", "plan", "surf", "infra", "situ", "vma"]
CATEGORICAL_FEATURES = [
    "lum", "dep", "agg", "int", "atm", "col", "catr", "circ", "vosp", "prof", "plan", "surf",
    "infra", "situ",
]
NUMERIC_FEATURES = ["nbv", "vma", "heure", "nb_vehicules", "mois", "jour_semaine"]


def load_caracteristiques(year: int) -> pl.DataFrame:
    df = pl.read_csv(BAAC_DIR / CARACT_FILES[year], separator=";", infer_schema_length=0)
    id_col = "Accident_Id" if "Accident_Id" in df.columns else "Num_Acc"
    return df.select(
        pl.col(id_col).alias("num_acc"),
        pl.lit(year).alias("annee"),
        pl.col("mois").cast(pl.Int64, strict=False),
        pl.col("jour").cast(pl.Int64, strict=False),
        pl.col("hrmn").str.slice(0, 2).cast(pl.Int64, strict=False).alias("heure"),
        *[pl.col(c) for c in CARACT_FEATURES],
    )


def load_lieux(year: int) -> pl.DataFrame:
    df = pl.read_csv(BAAC_DIR / f"lieux-{year}.csv", separator=";", infer_schema_length=0)
    return (
        df.select(pl.col("Num_Acc").alias("num_acc"), *[pl.col(c) for c in LIEUX_FEATURES])
        .unique(subset=["num_acc"], keep="first")  # un accident peut avoir plusieurs lignes
    )


def load_vehicules_count(year: int) -> pl.DataFrame:
    df = pl.read_csv(BAAC_DIR / f"vehicules-{year}.csv", separator=";", infer_schema_length=0)
    return df.group_by(pl.col("Num_Acc").alias("num_acc")).agg(
        pl.col("num_veh").n_unique().alias("nb_vehicules")
    )


def load_label(year: int) -> pl.DataFrame:
    df = pl.read_csv(BAAC_DIR / f"usagers-{year}.csv", separator=";", infer_schema_length=0)
    return df.group_by(pl.col("Num_Acc").alias("num_acc")).agg(
        pl.col("grav").cast(pl.Int64, strict=False).is_in([2, 3]).any().alias("is_grave")
    )


def load_year(year: int) -> pl.DataFrame:
    caract = load_caracteristiques(year)
    lieux = load_lieux(year)
    vehicules = load_vehicules_count(year)
    label = load_label(year)

    return (
        caract.join(lieux, on="num_acc", how="left")
        .join(vehicules, on="num_acc", how="left")
        .join(label, on="num_acc", how="left")
        .with_columns(
            pl.date(pl.col("annee"), pl.col("mois"), pl.col("jour"))
            .dt.weekday()
            .alias("jour_semaine")
        )
    )


def run_eda(df: pl.DataFrame) -> None:
    print("=" * 60)
    print("EDA")
    print("=" * 60)
    print(f"Accidents totaux (2019-2023) : {df.height}")
    print()
    print("Distribution de la cible par année :")
    print(
        df.group_by("annee")
        .agg(pl.len().alias("n"), pl.col("is_grave").mean().alias("taux_grave"))
        .sort("annee")
    )
    print()
    print("Taux de valeurs manquantes par feature :")
    n = df.height
    missing = df.select(
        [(pl.col(c).is_null().sum() / n).alias(c) for c in CARACT_FEATURES + LIEUX_FEATURES + ["nb_vehicules"]]
    )
    for c in missing.columns:
        print(f"  {c:15s} {missing[c][0]:.1%}")


def prepare_features(df: pl.DataFrame) -> pl.DataFrame:
    """Cast les catégorielles en category polars (LightGBM les gère nativement)."""
    return df.with_columns(
        [pl.col(c).cast(pl.Categorical) for c in CATEGORICAL_FEATURES]
    ).with_columns(
        [pl.col(c).cast(pl.Float64, strict=False) for c in NUMERIC_FEATURES]
    )


def main() -> None:
    print("Chargement 2019-2023...")
    years = [load_year(y) for y in range(2019, 2024)]
    full = pl.concat(years, how="diagonal_relaxed").drop_nulls("is_grave")

    run_eda(full)

    feature_cols = CARACT_FEATURES + LIEUX_FEATURES + ["nb_vehicules", "heure", "jour_semaine", "mois"]
    feature_cols = list(dict.fromkeys(feature_cols))  # dédoublonne en gardant l'ordre
    full = prepare_features(full)

    # Split temporel en 3 : train (2019-2021) / validation (2022, calibrage du seuil de
    # décision) / test (2023, holdout jamais vu). Calibrer le seuil sur le test aurait été de
    # la fuite de méthodologie (le seuil ferait indûment mieux sur les données qu'il a servi à
    # choisir).
    train = full.filter(pl.col("annee") < 2022)
    valid = full.filter(pl.col("annee") == 2022)
    test = full.filter(pl.col("annee") == 2023)

    print()
    print("=" * 60)
    print("BASELINE LightGBM (train 2019-2021 / validation 2022 / test 2023)")
    print("=" * 60)
    print(f"Train      : {train.height} accidents ({train['is_grave'].mean():.1%} graves)")
    print(f"Validation : {valid.height} accidents ({valid['is_grave'].mean():.1%} graves)")
    print(f"Test       : {test.height} accidents ({test['is_grave'].mean():.1%} graves)")

    def to_pandas_X(subset: pl.DataFrame):
        X = subset.select(feature_cols).to_pandas()
        for c in CATEGORICAL_FEATURES:
            X[c] = X[c].astype("category")
        return X

    X_train, y_train = to_pandas_X(train), train["is_grave"].to_pandas().astype(int)
    X_valid, y_valid = to_pandas_X(valid), valid["is_grave"].to_pandas().astype(int)
    X_test, y_test = to_pandas_X(test), test["is_grave"].to_pandas().astype(int)

    model = lgb.LGBMClassifier(
        n_estimators=300,
        learning_rate=0.05,
        class_weight="balanced",  # classe grave minoritaire (~10% à Paris, ~36% national)
        random_state=42,
        verbosity=-1,
    )
    model.fit(X_train, y_train, categorical_feature=CATEGORICAL_FEATURES)

    # Seuil par défaut (0.5) à titre de comparaison.
    proba_test = model.predict_proba(X_test)[:, 1]
    y_pred_default = (proba_test >= 0.5).astype(int)

    # Calibrage du seuil sur la VALIDATION (2022) pour viser le recall cible du CDC.
    proba_valid = model.predict_proba(X_valid)[:, 1]
    thresholds = np.linspace(0.01, 0.99, 99)
    valid_recalls = [recall_score(y_valid, (proba_valid >= t).astype(int)) for t in thresholds]
    eligible = [t for t, r in zip(thresholds, valid_recalls) if r >= TARGET_RECALL]
    tuned_threshold = max(eligible) if eligible else thresholds[int(np.argmax(valid_recalls))]

    y_pred_tuned = (proba_test >= tuned_threshold).astype(int)

    print()
    print(f"Seuil par défaut (0.5)   : recall={recall_score(y_test, y_pred_default):.3f}, "
          f"F1 macro={f1_score(y_test, y_pred_default, average='macro'):.3f}")
    print(f"Seuil calibré sur 2022 ({tuned_threshold:.2f}) appliqué à 2023 (holdout) :")
    recall_grave = recall_score(y_test, y_pred_tuned)
    f1_macro = f1_score(y_test, y_pred_tuned, average="macro")
    print(f"  Recall classe grave : {recall_grave:.3f}  (seuil CDC >= 0.80)")
    print(f"  F1-score macro       : {f1_macro:.3f}  (seuil CDC >= 0.70)")
    print()
    print(classification_report(y_test, y_pred_tuned, target_names=["non_grave", "grave"]))

    print("Importance des features (top 10) :")
    importances = (
        pl.DataFrame({"feature": feature_cols, "importance": model.feature_importances_})
        .sort("importance", descending=True)
        .head(10)
    )
    print(importances)


if __name__ == "__main__":
    main()
