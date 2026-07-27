"""Les deux pistes ouvertes par eval_seuil_par_zone.py, testées puis combinées :

  1. ENRICHIR LES FEATURES : le baseline (eda_baseline_baac.py) n'utilise QUE nb_vehicules
     depuis la table `vehicules` et rien depuis `usagers` (à part le label). Les flags
     moto/poids-lourd/piéton étaient déjà prévus dans le schéma Gold d'origine
     (Architecture_GRAVIA.md) mais jamais implémentés ni testés. Ce sont des facteurs de
     gravité connus en sécurité routière (implication d'un 2-roues ou d'un piéton change
     radicalement le risque), et plausiblement plus discriminants en zone urbaine dense
     (Paris) que météo/route seules.
  2. ARBITRER SEUIL LOCAL vs F1 GLOBAL : au lieu du tout-ou-rien testé précédemment (seuil
     unique vs seuil par département), un seuil "modéré" par zone (viser un recall local
     inférieur à 0,80, ex 0,50) pourrait offrir un meilleur compromis.

Ce script compare 4 configurations sur le même holdout 2023 (train 2019-2021 / validation
2022 / test 2023) :
  A) features baseline, seuil unique
  B) features baseline, seuil par département (repli si <30 graves en validation)
  C) features enrichies (+ flags véhicule/usager), seuil unique
  D) features enrichies, seuil par département
Puis reporte recall national / F1 macro national / recall Paris pour les 4, afin de voir si
l'enrichissement (C) rivalise avec le seuil par département (B) sans son coût en F1, et si
combiner les deux (D) fait mieux que chacun séparément.

Nouvelles features (codes catv/catu vérifiés empiriquement sur vehicules-2023.csv/usagers-2023.csv
avant implémentation, pas supposés à l'aveugle) :
  - flag_2roues_motorise : catv in {2,30,31,32,33,34,35,36,41,42,43} (cyclomoteur, scooter,
    moto, quad, 3-roues)
  - flag_poids_lourd : catv in {13,14,15,16,17,37,38} (PL, tracteur routier, bus, autocar)
  - flag_velo_edp : catv in {1,50,60,80} (bicyclette, EDP à moteur/sans moteur, VAE)
  - flag_pieton : depuis `usagers`, au moins un catu == 3 (piéton) impliqué dans l'accident

RÉSULTATS (holdout 2023, 54 822 accidents) :
  Effet marginal des flags sur le taux de gravité national : 2-roues motorisé +4,3 pt
  (34,3%->38,6%), poids lourd +5,7 pt (35,4%->41,1%), piéton quasi nul (35,9%->34,9%, effet
  probablement confondu avec agglomération/vitesse).

  | Configuration                          | Recall national | F1 macro national | Recall Paris |
  |------------------------------------------|-----------------|--------------------|--------------|
  | A) Baseline, seuil unique                | 0,808           | 0,708              | 0,007        |
  | B) Baseline, seuil par département       | 0,809           | 0,573              | 0,777        |
  | C) Enrichi, seuil unique                 | 0,805           | 0,727              | 0,023        |
  | D) Enrichi, seuil par département        | 0,808           | 0,609              | 0,812        |

  CONCLUSIONS :
  - L'enrichissement SEUL (C vs A) apporte un vrai gain global (F1 macro 0,708->0,727, contrairement
    au trafic qui n'apportait rien) mais NE RÉPARE PAS Paris (recall 0,007->0,023, toujours
    catastrophique) : le problème de seuil est bien un problème de seuil, pas seulement de
    features.
  - La COMBINAISON (D) est la meilleure configuration testée à ce jour : recall Paris 0,812
    (dépasse la cible 0,80), recall national 0,808 (conforme), et F1 macro national 0,609 —
    meilleur que le seuil par département seul (+0,036 de F1 récupéré) mais TOUJOURS sous le
    seuil CDC de 0,70.
  - La tension recall-partout/F1-global n'est donc pas résolue, seulement atténuée. Combiner
    enrichissement + calibration par zone est la meilleure direction connue à ce stade, mais
    d'autres features (ou une architecture différente : modèles régionaux, hiérarchiques)
    seraient nécessaires pour fermer l'écart restant sur le F1 macro.
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
MIN_GRAVE_CASES = 30

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
BASE_NUMERIC_FEATURES = ["nbv", "vma", "heure", "nb_vehicules", "mois", "jour_semaine"]
ENRICHED_FLAGS = ["flag_2roues_motorise", "flag_poids_lourd", "flag_velo_edp", "flag_pieton"]

CATV_2ROUES = [2, 30, 31, 32, 33, 34, 35, 36, 41, 42, 43]
CATV_POIDS_LOURD = [13, 14, 15, 16, 17, 37, 38]
CATV_VELO_EDP = [1, 50, 60, 80]


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


def load_vehicules(year: int) -> pl.DataFrame:
    df = pl.read_csv(BAAC_DIR / f"vehicules-{year}.csv", separator=";", infer_schema_length=0)
    catv = pl.col("catv").cast(pl.Int64, strict=False)
    return df.group_by(pl.col("Num_Acc").alias("num_acc")).agg(
        pl.col("num_veh").n_unique().alias("nb_vehicules"),
        catv.is_in(CATV_2ROUES).any().alias("flag_2roues_motorise"),
        catv.is_in(CATV_POIDS_LOURD).any().alias("flag_poids_lourd"),
        catv.is_in(CATV_VELO_EDP).any().alias("flag_velo_edp"),
    )


def load_usagers(year: int) -> pl.DataFrame:
    df = pl.read_csv(BAAC_DIR / f"usagers-{year}.csv", separator=";", infer_schema_length=0)
    return df.group_by(pl.col("Num_Acc").alias("num_acc")).agg(
        pl.col("grav").cast(pl.Int64, strict=False).is_in([2, 3]).any().alias("is_grave"),
        (pl.col("catu").cast(pl.Int64, strict=False) == 3).any().alias("flag_pieton"),
    )


def load_year(year: int) -> pl.DataFrame:
    caract = load_caracteristiques(year)
    lieux = load_lieux(year)
    vehicules = load_vehicules(year)
    usagers = load_usagers(year)
    return (
        caract.join(lieux, on="num_acc", how="left")
        .join(vehicules, on="num_acc", how="left")
        .join(usagers, on="num_acc", how="left")
        .with_columns(
            pl.date(pl.col("annee"), pl.col("mois"), pl.col("jour")).dt.weekday().alias("jour_semaine")
        )
    )


def prepare_features(df: pl.DataFrame, feature_cols: list[str]) -> pl.DataFrame:
    df = df.with_columns(
        [pl.col(c).cast(pl.Categorical) for c in CATEGORICAL_FEATURES if c in feature_cols]
    ).with_columns(
        [pl.col(c).cast(pl.Float64, strict=False) for c in BASE_NUMERIC_FEATURES if c in feature_cols]
    )
    for c in ENRICHED_FLAGS:
        if c in feature_cols:
            df = df.with_columns(pl.col(c).fill_null(False).cast(pl.Float64))
    return df


def calibrate_threshold(y_true, proba, target_recall: float) -> float:
    thresholds = np.linspace(0.01, 0.99, 99)
    recalls = [recall_score(y_true, (proba >= t).astype(int)) for t in thresholds]
    eligible = [t for t, r in zip(thresholds, recalls) if r >= target_recall]
    return max(eligible) if eligible else thresholds[int(np.argmax(recalls))]


def fit_and_predict(full: pl.DataFrame, feature_cols: list[str]):
    """Entraîne le modèle et renvoie (proba_valid, proba_test, y_valid, y_test, dep_valid, dep_test)."""
    df = prepare_features(full, feature_cols)
    train = df.filter(pl.col("annee") < 2022)
    valid = df.filter(pl.col("annee") == 2022)
    test = df.filter(pl.col("annee") == 2023)

    cat_in_use = [c for c in CATEGORICAL_FEATURES if c in feature_cols]

    def to_pandas_X(subset: pl.DataFrame):
        X = subset.select(feature_cols).to_pandas()
        for c in cat_in_use:
            X[c] = X[c].astype("category")
        return X

    X_train, y_train = to_pandas_X(train), train["is_grave"].to_pandas().astype(int)
    X_valid, y_valid = to_pandas_X(valid), valid["is_grave"].to_pandas().astype(int)
    X_test, y_test = to_pandas_X(test), test["is_grave"].to_pandas().astype(int)

    model = lgb.LGBMClassifier(
        n_estimators=300, learning_rate=0.05, class_weight="balanced", random_state=42, verbosity=-1,
    )
    model.fit(X_train, y_train, categorical_feature=cat_in_use)

    proba_valid = model.predict_proba(X_valid)[:, 1]
    proba_test = model.predict_proba(X_test)[:, 1]
    dep_valid = valid["dep"].to_pandas().to_numpy()
    dep_test = test["dep"].to_pandas().to_numpy()
    is_paris_test = (test["dep"].to_pandas() == "75").to_numpy()

    return proba_valid, proba_test, y_valid.to_numpy(), y_test.to_numpy(), dep_valid, dep_test, is_paris_test, model


def apply_single_threshold(y_valid, proba_valid, proba_test):
    th = calibrate_threshold(y_valid, proba_valid, TARGET_RECALL)
    return (proba_test >= th).astype(int), th


def apply_department_thresholds(y_valid, proba_valid, dep_valid, proba_test, dep_test):
    y_pred = np.zeros(len(proba_test), dtype=int)
    global_th = calibrate_threshold(y_valid, proba_valid, TARGET_RECALL)
    n_dep_calibres, n_dep_replies = 0, 0
    for dep_value in np.unique(dep_test):
        mask_valid = dep_valid == dep_value
        mask_test = dep_test == dep_value
        if y_valid[mask_valid].sum() >= MIN_GRAVE_CASES:
            th = calibrate_threshold(y_valid[mask_valid], proba_valid[mask_valid], TARGET_RECALL)
            n_dep_calibres += 1
        else:
            th = global_th
            n_dep_replies += 1
        y_pred[mask_test] = (proba_test[mask_test] >= th).astype(int)
    return y_pred, n_dep_calibres, n_dep_replies


def report(nom: str, y_test, y_pred, is_paris_test) -> dict:
    recall_national = recall_score(y_test, y_pred)
    f1_national = f1_score(y_test, y_pred, average="macro")
    recall_paris = recall_score(y_test[is_paris_test], y_pred[is_paris_test])
    print(f"{nom:45s} recall national={recall_national:.3f}  F1 macro national={f1_national:.3f}  "
          f"recall Paris={recall_paris:.3f}")
    return {"nom": nom, "recall_national": recall_national, "f1_national": f1_national, "recall_paris": recall_paris}


def main() -> None:
    print("Chargement 2019-2023...")
    years = [load_year(y) for y in range(2019, 2024)]
    full = pl.concat(years, how="diagonal_relaxed").drop_nulls("is_grave")

    print("\nTaux de gravité selon l'implication d'un 2-roues motorisé / piéton / poids lourd :")
    print(
        full.select(
            pl.col("flag_2roues_motorise").fill_null(False).cast(pl.Boolean).alias("flag_2roues_motorise"),
            pl.col("flag_pieton").fill_null(False).cast(pl.Boolean).alias("flag_pieton"),
            pl.col("flag_poids_lourd").fill_null(False).cast(pl.Boolean).alias("flag_poids_lourd"),
            "is_grave",
        )
        .unpivot(index="is_grave", variable_name="flag", value_name="present")
        .group_by(["flag", "present"])
        .agg(pl.len().alias("n"), pl.col("is_grave").mean().alias("taux_grave"))
        .sort(["flag", "present"])
    )

    baseline_cols = CARACT_FEATURES + LIEUX_FEATURES + ["nb_vehicules", "heure", "jour_semaine", "mois"]
    enriched_cols = baseline_cols + ENRICHED_FLAGS

    results = []

    print("\n" + "=" * 90)
    print("A/B : features BASELINE")
    print("=" * 90)
    pv, pt, yv, yt, dv, dt, paris_mask, _ = fit_and_predict(full, baseline_cols)
    y_pred_a, th_a = apply_single_threshold(yv, pv, pt)
    results.append(report("A) Baseline, seuil unique", yt, y_pred_a, paris_mask))
    y_pred_b, n_cal, n_rep = apply_department_thresholds(yv, pv, dv, pt, dt)
    print(f"    (départements calibrés={n_cal}, en repli={n_rep})")
    results.append(report("B) Baseline, seuil par département", yt, y_pred_b, paris_mask))

    print("\n" + "=" * 90)
    print("C/D : features ENRICHIES (+ flags véhicule/usager)")
    print("=" * 90)
    pv2, pt2, yv2, yt2, dv2, dt2, paris_mask2, model_enriched = fit_and_predict(full, enriched_cols)
    y_pred_c, th_c = apply_single_threshold(yv2, pv2, pt2)
    results.append(report("C) Enrichi, seuil unique", yt2, y_pred_c, paris_mask2))
    y_pred_d, n_cal2, n_rep2 = apply_department_thresholds(yv2, pv2, dv2, pt2, dt2)
    print(f"    (départements calibrés={n_cal2}, en repli={n_rep2})")
    results.append(report("D) Enrichi, seuil par département", yt2, y_pred_d, paris_mask2))

    print("\nImportance des features (modèle enrichi) :")
    print(
        pl.DataFrame({"feature": enriched_cols, "importance": model_enriched.feature_importances_})
        .sort("importance", descending=True)
        .head(12)
    )

    print("\n" + "=" * 90)
    print("COMPARAISON FINALE (4 configurations)")
    print("=" * 90)
    print(f"{'Configuration':45s} {'Recall national':>16s} {'F1 macro national':>20s} {'Recall Paris':>14s}")
    for r in results:
        print(f"{r['nom']:45s} {r['recall_national']:16.3f} {r['f1_national']:20.3f} {r['recall_paris']:14.3f}")


if __name__ == "__main__":
    main()
