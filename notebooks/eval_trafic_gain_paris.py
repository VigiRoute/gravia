"""Le trafic Paris améliore-t-il le baseline déjà solide, sur le sous-ensemble parisien ?

Deux résultats précédents, pas encore combinés :
  - notebooks/eda_baseline_baac.py : baseline national (BAAC seul) atteint déjà recall grave
    0,808 et F1 macro 0,708 (seuils CDC validés), sans aucun enrichissement.
  - notebooks/explo_trafic_paris_correlation_annuel.py : le trafic Paris corrèle
    significativement avec la gravité (test de Welch, p≈0,03), mais ce n'est qu'un test
    statistique univarié — pas une mesure de gain pour un modèle multivarié qui a déjà accès
    à l'heure, la météo, le type de route, etc.

Ce script entraîne DEUX modèles sur EXACTEMENT le même sous-ensemble (accidents parisiens,
dep=75) et le même split temporel que le baseline national (train 2019-2021 / validation 2022
pour calibrer le seuil / test 2023 holdout) :
  A) features BAAC seules (mêmes que le baseline national)
  B) features BAAC + trafic (débit, taux d'occupation du capteur le plus proche à l'heure de
     l'accident, comme dans explo_trafic_paris_correlation_annuel.py)

Pour que le modèle B apprenne réellement à exploiter le trafic (pas juste le voir au moment du
test), la feature doit être peuplée aussi sur les années d'entraînement -> nécessite le trafic
Paris 2019-2022 en plus de 2023 (déjà en local depuis l'exploration précédente).

Prérequis données (déjà en local si les notebooks précédents ont tourné) :
  - data/raw/baac/{caracteristiques,carcteristiques,caract}-<année>.csv (+ lieux/usagers/vehicules)
  - data/raw/trafic_paris/referentiel-comptages-routiers.csv
  - data/raw/trafic_paris/<année>_full/trafic_capteurs_<année>_W##_*.txt (2019-2023)

CORRECTION IMPORTANTE sur explo_trafic_paris_correlation_annuel.py : le "93 % de couverture"
annoncé là-bas est le taux de RATTACHEMENT TEMPOREL (une ligne de mesure existe pour le
capteur/heure), pas le taux de VALEUR EXPLOITABLE. En creusant ici, environ la moitié des
lignes rattachées ont un débit/taux_occupation NULL (capteur en panne/gap de données à cette
heure précise) — vérifié stable sur les 5 années (46-53 %). La vraie couverture utilisable est
donc ~46-53 %, pas 93 %.

RÉSULTATS : sur ce sous-ensemble Paris (train 2019-2021 / validation 2022 / test 2023, même
protocole que le baseline national) :
  A) BAAC seul     : recall grave 0,708, F1 macro 0,385
  B) BAAC + trafic : recall grave 0,715, F1 macro 0,372
  Gain recall : +0,007 (négligeable) — Gain F1 macro : -0,013 (négligeable/légèrement négatif)
  Le trafic (débit + taux_occupation) ressort pourtant comme la feature la plus importante du
  modèle B, mais ça ne se traduit pas en meilleure performance globale : `heure`/`mois`/
  `jour_semaine` (déjà dans le modèle A) capturent probablement déjà l'essentiel de ce que le
  trafic explique, puisque le niveau de trafic est en grande partie prévisible à partir de
  l'heure et du jour. Le signal statistique trouvé précédemment (Welch p≈0,03) était réel mais
  redondant avec des features déjà présentes.

  Constat plus large : les DEUX modèles Paris (A et B) plafonnent très en dessous du baseline
  national (F1 macro ~0,37-0,39 contre 0,708, cf. eda_baseline_baac.py) — pas un bug, juste
  beaucoup moins de données d'entraînement (15 405 lignes dont ~9 % de graves, contre 218 404
  dont ~36 % au national). Restreindre l'entraînement à Paris coûte cher en qualité de modèle,
  bien avant même de parler de trafic. Voir eval_trafic_gain_national.py pour le test suivant
  (trafic comme feature sparse sur le modèle national complet, sans ce biais).
"""

import sys
from pathlib import Path

import lightgbm as lgb
import numpy as np
import polars as pl
from sklearn.metrics import classification_report, f1_score, recall_score

sys.stdout.reconfigure(encoding="utf-8")

RAW_DIR = Path(__file__).resolve().parent.parent / "data" / "raw"
BAAC_DIR = RAW_DIR / "baac"
TRAFFIC_DIR = RAW_DIR / "trafic_paris"
SENSOR_REF_FILE = TRAFFIC_DIR / "referentiel-comptages-routiers.csv"

TARGET_RECALL = 0.80
MATCH_RADIUS_M = 300.0
EARTH_RADIUS_M = 6_371_000.0

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
TRAFFIC_FEATURES = ["debit", "taux_occupation"]


def haversine_m(lat1, lon1, lat2, lon2) -> pl.Expr:
    lat1_r, lat2_r = lat1.radians(), lat2.radians()
    dlat = (lat2 - lat1).radians()
    dlon = (lon2 - lon1).radians()
    a = (dlat / 2).sin() ** 2 + lat1_r.cos() * lat2_r.cos() * (dlon / 2).sin() ** 2
    return 2 * EARTH_RADIUS_M * a.sqrt().arcsin()


def load_caracteristiques(year: int) -> pl.DataFrame:
    df = pl.read_csv(BAAC_DIR / CARACT_FILES[year], separator=";", infer_schema_length=0)
    id_col = "Accident_Id" if "Accident_Id" in df.columns else "Num_Acc"
    return df.filter(pl.col("dep") == "75").select(
        pl.col(id_col).alias("num_acc"),
        pl.lit(year).alias("annee"),
        pl.col("mois").cast(pl.Int64, strict=False),
        pl.col("jour").cast(pl.Int64, strict=False),
        pl.col("hrmn").str.slice(0, 2).cast(pl.Int64, strict=False).alias("heure"),
        pl.col("hrmn"),
        pl.col("lat").str.replace(",", ".").cast(pl.Float64, strict=False).alias("acc_lat"),
        pl.col("long").str.replace(",", ".").cast(pl.Float64, strict=False).alias("acc_lon"),
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


def load_baac_paris_year(year: int) -> pl.DataFrame:
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
        .drop_nulls(["acc_lat", "acc_lon", "is_grave"])
        .with_columns(
            pl.datetime(pl.col("annee"), pl.col("mois"), pl.col("jour"))
            .dt.combine(pl.col("hrmn").str.strptime(pl.Time, "%H:%M", strict=False))
            .dt.truncate("1h")
            .alias("acc_hour")
        )
        .drop_nulls(["acc_hour"])
    )


def load_sensors() -> pl.DataFrame:
    df = pl.read_csv(SENSOR_REF_FILE, separator=";", infer_schema_length=0)
    latlon = pl.col("geo_point_2d").str.split_exact(", ", 1)
    return df.select(
        pl.col("Identifiant arc").alias("iu_ac"),
        latlon.struct.field("field_0").cast(pl.Float64).alias("sensor_lat"),
        latlon.struct.field("field_1").cast(pl.Float64).alias("sensor_lon"),
    ).unique(subset=["iu_ac"])


def nearest_sensor_per_accident(accidents: pl.DataFrame, sensors: pl.DataFrame) -> pl.DataFrame:
    return (
        accidents.join(sensors, how="cross")
        .with_columns(
            haversine_m(
                pl.col("acc_lat"), pl.col("acc_lon"), pl.col("sensor_lat"), pl.col("sensor_lon")
            ).alias("dist_m")
        )
        .filter(pl.col("dist_m") <= MATCH_RADIUS_M)
        .sort("dist_m")
        .unique(subset=["num_acc"], keep="first")
    )


def collect_traffic_for_year(year: int, candidates: pl.DataFrame) -> pl.DataFrame:
    year_dir = TRAFFIC_DIR / f"{year}_full"
    week_files = sorted(year_dir.glob(f"trafic_capteurs_{year}_*.txt"))
    if not week_files:
        print(f"  [!] Aucun fichier trafic trouvé pour {year} dans {year_dir}")
        return pl.DataFrame(schema={"iu_ac": pl.Utf8, "t_1h": pl.Datetime, "debit": pl.Float64, "taux_occupation": pl.Float64})

    target_sensors = candidates.select("iu_ac").unique().to_series().to_list()
    target_hours = candidates.select("acc_hour").unique().to_series().to_list()

    frames = []
    for week_file in week_files:
        filtered = (
            pl.scan_csv(week_file, separator=";", infer_schema_length=0)
            .filter(pl.col("iu_ac").is_in(target_sensors))
            .select(
                pl.col("iu_ac"),
                pl.col("t_1h").str.strptime(pl.Datetime, "%Y-%m-%d %H:%M:%S", strict=False),
                pl.col("q").cast(pl.Float64, strict=False).alias("debit"),
                pl.col("k").cast(pl.Float64, strict=False).alias("taux_occupation"),
            )
            .filter(pl.col("t_1h").is_in(target_hours))
            .collect()
        )
        if filtered.height:
            frames.append(filtered)
    return pl.concat(frames) if frames else pl.DataFrame(
        schema={"iu_ac": pl.Utf8, "t_1h": pl.Datetime, "debit": pl.Float64, "taux_occupation": pl.Float64}
    )


def build_dataset_with_traffic(sensors: pl.DataFrame) -> pl.DataFrame:
    """Charge 2019-2023, ajoute (quand disponible) débit/occupation du capteur le plus proche."""
    all_years = []
    for year in range(2019, 2024):
        print(f"Année {year}...")
        accidents = load_baac_paris_year(year)
        nearest = nearest_sensor_per_accident(accidents, sensors)
        traffic = collect_traffic_for_year(year, nearest)
        # INNER d'abord (matches réels uniquement), dédoublonné en gardant explicitement la
        # 1re occurrence, PUIS re-jointure LEFT sur la liste complète des accidents pour
        # réintroduire les non-matchés comme null. Un `left` direct entre `nearest` et le
        # grand DataFrame `traffic` produit des doublons (créneaux hebdomadaires qui se
        # chevauchent en frontière de semaine) dont certains ont une valeur non-nulle et
        # d'autres nulle pour la même heure ; `.unique()` sans `keep="first"` explicite peut
        # alors garder arbitrairement la ligne nulle et sous-compter la couverture réelle.
        with_traffic = nearest.join(
            traffic, left_on=["iu_ac", "acc_hour"], right_on=["iu_ac", "t_1h"], how="inner"
        ).unique(subset=["num_acc"], keep="first")
        # ré-attache les accidents sans capteur proche (dist_m > 300m) : trafic = null
        merged = accidents.join(
            with_traffic.select("num_acc", *TRAFFIC_FEATURES), on="num_acc", how="left"
        )
        all_years.append(merged)
    return pl.concat(all_years, how="diagonal_relaxed")


def prepare_features(df: pl.DataFrame, feature_cols: list[str]) -> pl.DataFrame:
    return df.with_columns(
        [pl.col(c).cast(pl.Categorical) for c in CATEGORICAL_FEATURES if c in feature_cols]
    ).with_columns(
        [
            pl.col(c).cast(pl.Float64, strict=False)
            for c in NUMERIC_FEATURES + TRAFFIC_FEATURES
            if c in feature_cols
        ]
    )


def train_and_evaluate(df: pl.DataFrame, feature_cols: list[str], label: str) -> dict:
    df = prepare_features(df, feature_cols)
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
        n_estimators=300, learning_rate=0.05, class_weight="balanced",
        random_state=42, verbosity=-1,
    )
    model.fit(X_train, y_train, categorical_feature=cat_in_use)

    proba_valid = model.predict_proba(X_valid)[:, 1]
    thresholds = np.linspace(0.01, 0.99, 99)
    valid_recalls = [recall_score(y_valid, (proba_valid >= t).astype(int)) for t in thresholds]
    eligible = [t for t, r in zip(thresholds, valid_recalls) if r >= TARGET_RECALL]
    tuned_threshold = max(eligible) if eligible else thresholds[int(np.argmax(valid_recalls))]

    proba_test = model.predict_proba(X_test)[:, 1]
    y_pred = (proba_test >= tuned_threshold).astype(int)

    recall = recall_score(y_test, y_pred)
    f1_macro = f1_score(y_test, y_pred, average="macro")

    print(f"\n--- Modèle {label} ---")
    print(f"Train {train.height} / Valid {valid.height} / Test {test.height}")
    print(f"Seuil calibré (2022) : {tuned_threshold:.2f}")
    print(f"Recall grave (2023)  : {recall:.3f}")
    print(f"F1 macro (2023)      : {f1_macro:.3f}")
    print(classification_report(y_test, y_pred, target_names=["non_grave", "grave"]))

    importances = pl.DataFrame(
        {"feature": feature_cols, "importance": model.feature_importances_}
    ).sort("importance", descending=True)
    print("Importance des features :")
    print(importances)

    return {"label": label, "recall": recall, "f1_macro": f1_macro, "threshold": tuned_threshold}


def main() -> None:
    sensors = load_sensors()
    df = build_dataset_with_traffic(sensors)

    n = df.height
    n_with_traffic = df.filter(pl.col("debit").is_not_null()).height
    print(f"\nAccidents Paris 2019-2023 : {n}")
    print(f"dont avec trafic matché    : {n_with_traffic} ({n_with_traffic / n:.0%})")
    print(
        df.group_by("annee").agg(
            pl.len().alias("n"),
            pl.col("debit").is_not_null().mean().alias("pct_avec_trafic"),
        ).sort("annee")
    )

    baac_only_cols = CARACT_FEATURES + LIEUX_FEATURES + ["nb_vehicules", "heure", "jour_semaine", "mois"]
    baac_plus_trafic_cols = baac_only_cols + TRAFFIC_FEATURES

    result_a = train_and_evaluate(df, baac_only_cols, "A) BAAC seul (Paris)")
    result_b = train_and_evaluate(df, baac_plus_trafic_cols, "B) BAAC + trafic (Paris)")

    print("\n" + "=" * 60)
    print("COMPARAISON FINALE (même sous-ensemble Paris, même split, même seuil calibré)")
    print("=" * 60)
    print(f"A) BAAC seul       : recall={result_a['recall']:.3f}  F1 macro={result_a['f1_macro']:.3f}")
    print(f"B) BAAC + trafic   : recall={result_b['recall']:.3f}  F1 macro={result_b['f1_macro']:.3f}")
    print(f"Gain recall   : {result_b['recall'] - result_a['recall']:+.3f}")
    print(f"Gain F1 macro : {result_b['f1_macro'] - result_a['f1_macro']:+.3f}")


if __name__ == "__main__":
    main()
