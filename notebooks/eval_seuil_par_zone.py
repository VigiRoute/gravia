"""Corrige l'angle mort de sécurité trouvé dans eval_trafic_gain_national.py (branche
explo/trafic-vs-baseline-paris) : le modèle national, avec un seuil de décision UNIQUE
calibré à 0,44 sur la validation 2022, donne un recall global de 0,808 (seuil CDC validé) mais
un recall de seulement 0,007 sur le sous-ensemble parisien (3 accidents graves détectés sur
431). Cause diagnostiquée : Paris a un taux de gravité structurellement plus faible (~9 % vs
~36 % national), donc le modèle y prédit des probabilités systématiquement plus basses, qui ne
franchissent quasiment jamais un seuil calibré sur la distribution nationale.

Hypothèse testée ici : calibrer un seuil de décision SÉPARÉ par zone (agg = en agglomération vs
hors agglomération, champ BAAC déjà disponible, pas besoin du département) répare-t-il le
recall sur les zones à faible taux de base (dont Paris), sans casser le recall national global ?

Protocole : même modèle, même split (train 2019-2021 / validation 2022 / test 2023 holdout)
que eda_baseline_baac.py. Sur la validation, on calibre DEUX seuils indépendants (un pour
agg=1 hors agglomération, un pour agg=2 en agglomération), chacun visant recall >= 0,80 DANS
SA ZONE. On applique ensuite le seuil correspondant à chaque ligne du test 2023 selon sa
valeur de `agg`, et on compare au seuil unique national (ligne de base).

Extension : seuils PAR DÉPARTEMENT (repli/shrinkage vers le seuil de zone `agg` si un
département a moins de 30 cas graves en validation, pour éviter un seuil bruité sur les
départements peu peuplés).

RÉSULTATS (holdout 2023, 54 822 accidents) :
  Taux de gravité par zone : hors agglo (agg=1) 51,8 %, en agglo (agg=2) 26,8 %. Paris (dep=75)
  7,4 % — un cas extrême même parmi les zones "en agglo".

  | Approche                | Recall national | F1 macro national | Recall Paris |
  |--------------------------|-----------------|--------------------|--------------|
  | Seuil unique             | 0,808           | 0,708              | 0,007        |
  | Seuils par zone (agg)    | 0,805           | 0,671              | 0,067        |
  | Seuils par département   | 0,809           | 0,573              | 0,777        |

  Le découpage par département (102 départements calibrés individuellement, 5 en repli sur le
  seuil de zone) RÉPARE le recall Paris (0,777, proche de la cible) mais au prix d'un
  effondrement du F1 macro national à 0,573 (sous le seuil CDC de 0,70) — le modèle
  sur-déclenche massivement pour capter les cas graves dans les zones à faible taux de base.

  CONCLUSION : ce n'est plus un simple problème de calibration de seuil, c'est une VRAIE
  TENSION entre les deux métriques du CDC (recall >= 0,80 partout vs F1 macro >= 0,70 global).
  Le modèle actuel (features BAAC seules) ne semble pas avoir assez de pouvoir discriminant
  dans les contextes à faible taux de base pour obtenir un recall élevé sans une explosion de
  faux positifs. Piste à explorer : enrichir les features plutôt que recalibrer les seuils, ou
  trancher explicitement en gouvernance quelle métrique prime en cas de conflit (et pour quels
  territoires).
"""

import sys
from pathlib import Path

import lightgbm as lgb
import numpy as np
import polars as pl
from sklearn.metrics import classification_report, f1_score, recall_score

sys.stdout.reconfigure(encoding="utf-8")

BAAC_DIR = Path(__file__).resolve().parent.parent / "data" / "raw" / "baac"
TARGET_RECALL = 0.80

CARACT_FILES = {
    2019: "caracteristiques-2019.csv",
    2020: "caracteristiques-2020.csv",
    2021: "carcteristiques-2021.csv",
    2022: "carcteristiques-2022.csv",
    2023: "caract-2023.csv",
}

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
    return df.select(
        pl.col("Num_Acc").alias("num_acc"), *[pl.col(c) for c in LIEUX_FEATURES]
    ).unique(subset=["num_acc"], keep="first")


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
            pl.date(pl.col("annee"), pl.col("mois"), pl.col("jour")).dt.weekday().alias("jour_semaine")
        )
    )


def prepare_features(df: pl.DataFrame, feature_cols: list[str]) -> pl.DataFrame:
    return df.with_columns(
        [pl.col(c).cast(pl.Categorical) for c in CATEGORICAL_FEATURES if c in feature_cols]
    ).with_columns(
        [pl.col(c).cast(pl.Float64, strict=False) for c in NUMERIC_FEATURES if c in feature_cols]
    )


def calibrate_threshold(y_true, proba, target_recall: float) -> float:
    thresholds = np.linspace(0.01, 0.99, 99)
    recalls = [recall_score(y_true, (proba >= t).astype(int)) for t in thresholds]
    eligible = [t for t, r in zip(thresholds, recalls) if r >= target_recall]
    return max(eligible) if eligible else thresholds[int(np.argmax(recalls))]


def main() -> None:
    print("Chargement 2019-2023...")
    years = [load_year(y) for y in range(2019, 2024)]
    full = pl.concat(years, how="diagonal_relaxed").drop_nulls("is_grave")

    feature_cols = CARACT_FEATURES + LIEUX_FEATURES + ["nb_vehicules", "heure", "jour_semaine", "mois"]
    full = prepare_features(full, feature_cols)

    train = full.filter(pl.col("annee") < 2022)
    valid = full.filter(pl.col("annee") == 2022)
    test = full.filter(pl.col("annee") == 2023)

    print("Taux de gravité par zone (agg=1 hors agglo, agg=2 en agglo) :")
    print(full.group_by("agg").agg(pl.len().alias("n"), pl.col("is_grave").mean().alias("taux_grave")))
    print(f"Paris (dep=75), pour référence : {full.filter(pl.col('dep')=='75')['is_grave'].mean():.1%} de graves")

    def to_pandas_X(subset: pl.DataFrame):
        X = subset.select(feature_cols).to_pandas()
        for c in CATEGORICAL_FEATURES:
            X[c] = X[c].astype("category")
        return X

    X_train, y_train = to_pandas_X(train), train["is_grave"].to_pandas().astype(int)
    X_valid, y_valid = to_pandas_X(valid), valid["is_grave"].to_pandas().astype(int)
    X_test, y_test = to_pandas_X(test), test["is_grave"].to_pandas().astype(int)

    model = lgb.LGBMClassifier(
        n_estimators=300, learning_rate=0.05, class_weight="balanced", random_state=42, verbosity=-1,
    )
    model.fit(X_train, y_train, categorical_feature=CATEGORICAL_FEATURES)

    proba_valid = model.predict_proba(X_valid)[:, 1]
    proba_test = model.predict_proba(X_test)[:, 1]

    agg_valid = valid["agg"].to_pandas().to_numpy()
    agg_test = test["agg"].to_pandas().to_numpy()
    is_paris_test = (test["dep"].to_pandas() == "75").to_numpy()

    # --- Baseline : seuil unique national ---
    threshold_global = calibrate_threshold(y_valid, proba_valid, TARGET_RECALL)
    y_pred_global = (proba_test >= threshold_global).astype(int)

    print("\n" + "=" * 60)
    print("BASELINE : seuil unique national")
    print("=" * 60)
    print(f"Seuil : {threshold_global:.2f}")
    print(f"Recall national : {recall_score(y_test, y_pred_global):.3f}")
    print(f"F1 macro national : {f1_score(y_test, y_pred_global, average='macro'):.3f}")
    print(f"Recall Paris (dep=75) : {recall_score(y_test.to_numpy()[is_paris_test], y_pred_global[is_paris_test]):.3f}")

    # --- Seuils calibrés par zone (agg) ---
    print("\n" + "=" * 60)
    print("SEUILS CALIBRÉS PAR ZONE (agg)")
    print("=" * 60)
    y_pred_zoned = np.zeros_like(y_pred_global)
    for agg_value in np.unique(agg_valid):
        mask_valid = agg_valid == agg_value
        mask_test = agg_test == agg_value
        th = calibrate_threshold(y_valid.to_numpy()[mask_valid], proba_valid[mask_valid], TARGET_RECALL)
        y_pred_zoned[mask_test] = (proba_test[mask_test] >= th).astype(int)
        recall_zone = recall_score(y_test.to_numpy()[mask_test], y_pred_zoned[mask_test])
        print(f"agg={agg_value} : seuil={th:.2f}, n_valid={mask_valid.sum()}, "
              f"recall test dans la zone={recall_zone:.3f}")

    print()
    print(f"Recall national (seuils par zone) : {recall_score(y_test, y_pred_zoned):.3f}")
    print(f"F1 macro national (seuils par zone) : {f1_score(y_test, y_pred_zoned, average='macro'):.3f}")
    print(f"Recall Paris (dep=75, seuils par zone) : "
          f"{recall_score(y_test.to_numpy()[is_paris_test], y_pred_zoned[is_paris_test]):.3f}")

    print()
    print("Rapport détaillé (seuils par zone) :")
    print(classification_report(y_test, y_pred_zoned, target_names=["non_grave", "grave"]))

    # --- Seuils par département, avec repli (shrinkage) vers le seuil de zone (agg) quand un
    # département a trop peu de cas graves en validation pour un seuil fiable. ---
    print("\n" + "=" * 60)
    print("SEUILS PAR DÉPARTEMENT (repli vers le seuil de zone si < 30 cas graves en validation)")
    print("=" * 60)
    MIN_GRAVE_CASES = 30
    dep_valid = valid["dep"].to_pandas().to_numpy()
    dep_test = test["dep"].to_pandas().to_numpy()
    y_valid_np = y_valid.to_numpy()

    # seuil de repli par zone (déjà calibré ci-dessus, un par valeur de agg)
    fallback_threshold = {}
    for agg_value in np.unique(agg_valid):
        mask_valid = agg_valid == agg_value
        fallback_threshold[agg_value] = calibrate_threshold(
            y_valid_np[mask_valid], proba_valid[mask_valid], TARGET_RECALL
        )

    y_pred_dep = np.zeros_like(y_pred_global)
    n_dep_calibres, n_dep_replies = 0, 0
    for dep_value in np.unique(dep_test):
        mask_valid = dep_valid == dep_value
        mask_test = dep_test == dep_value
        n_grave_valid = y_valid_np[mask_valid].sum()
        if n_grave_valid >= MIN_GRAVE_CASES:
            th = calibrate_threshold(y_valid_np[mask_valid], proba_valid[mask_valid], TARGET_RECALL)
            n_dep_calibres += 1
        else:
            # repli : seuil de la zone agg dominante de ce département
            agg_dominant = agg_test[mask_test][0] if mask_test.any() else 2
            th = fallback_threshold.get(agg_dominant, threshold_global)
            n_dep_replies += 1
        y_pred_dep[mask_test] = (proba_test[mask_test] >= th).astype(int)

    print(f"Départements calibrés individuellement (>= {MIN_GRAVE_CASES} graves en validation) : {n_dep_calibres}")
    print(f"Départements en repli sur le seuil de zone : {n_dep_replies}")
    print()
    print(f"Recall national (seuils par département) : {recall_score(y_test, y_pred_dep):.3f}")
    print(f"F1 macro national (seuils par département) : {f1_score(y_test, y_pred_dep, average='macro'):.3f}")
    print(f"Recall Paris (dep=75, seuils par département) : "
          f"{recall_score(y_test.to_numpy()[is_paris_test], y_pred_dep[is_paris_test]):.3f}")

    print()
    print("Rapport détaillé (seuils par département) :")
    print(classification_report(y_test, y_pred_dep, target_names=["non_grave", "grave"]))

    print("\n" + "=" * 60)
    print("COMPARAISON FINALE (3 approches)")
    print("=" * 60)
    print(f"{'':25s} {'Recall national':>16s} {'F1 macro national':>20s} {'Recall Paris':>14s}")
    for nom, y_pred_variant in [
        ("Seuil unique", y_pred_global),
        ("Seuils par zone (agg)", y_pred_zoned),
        ("Seuils par département", y_pred_dep),
    ]:
        print(f"{nom:25s} {recall_score(y_test, y_pred_variant):16.3f} "
              f"{f1_score(y_test, y_pred_variant, average='macro'):20.3f} "
              f"{recall_score(y_test.to_numpy()[is_paris_test], y_pred_variant[is_paris_test]):14.3f}")


if __name__ == "__main__":
    main()
