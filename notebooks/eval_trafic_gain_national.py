"""Le trafic Paris, ajouté comme feature optionnelle/sparse à un modèle NATIONAL, apporte-t-il
un gain — la config la plus proche de ce qui serait réellement déployé ?

notebooks/eval_trafic_gain_paris.py a montré que sur un modèle entraîné UNIQUEMENT sur Paris,
le trafic n'apporte aucun gain (recall +0,007, F1 macro -0,013) — mais ce test avait un biais
méthodologique : restreindre l'entraînement à Paris (15 405 lignes, ~9 % de graves) dégrade
déjà fortement le modèle par rapport au baseline national (273 226 lignes, ~36 % de graves,
recall 0,808 / F1 macro 0,708, cf. eda_baseline_baac.py) — le modèle Paris seul plafonne à F1
macro ~0,38, bien avant même de parler de trafic.

Ce script répond à la question qui compte vraiment pour la production : entraîner le modèle
sur TOUTE la France (comme le baseline), et ajouter le trafic comme feature optionnelle/sparse
(peuplée uniquement pour les accidents parisiens matchés à un capteur, null partout ailleurs —
LightGBM gère nativement le manquant). Si ça n'aide même pas dans cette config nationale, il
n'y a plus d'ambiguïté méthodologique : le trafic ne vaut pas la complexité qu'il ajoute au
pipeline (téléchargement de ~35 Go d'archives Deflate64, jointure spatiale, capteurs en panne).

Deux lectures rapportées :
  1. Métriques nationales agrégées (recall/F1 macro sur les 54 822 accidents du test 2023).
     Attendu : effet quasi invisible, Paris ne représente que ~9 % des accidents nationaux, et
     seule la moitié de ce sous-ensemble a une valeur trafic non-nulle (~4,5 % du total) — pas
     assez pour bouger une métrique agrégée sur 54 822 lignes.
  2. Métriques restreintes au SOUS-ENSEMBLE PARISIEN du test national (même modèle, mêmes
     poids, prédictions filtrées a posteriori sur dep=75) — c'est là qu'un éventuel gain local
     serait visible, si le modèle national avec trafic fait mieux QUE LE MÊME MODÈLE NATIONAL
     sans trafic, spécifiquement sur Paris.

RÉSULTATS :
  - Trafic sparse (Paris, ~4,6 % du volume national) : effet négligeable partout.
      National : recall 0,808 -> 0,810 (+0,001), F1 macro 0,708 -> 0,707 (-0,000).
      Paris seul (même modèle) : recall 0,007 -> 0,009 (+0,002), F1 macro 0,483 -> 0,485.
    Confirme eval_trafic_gain_paris.py : le trafic n'apporte pas de gain mesurable, ni en
    modèle dédié Paris ni en feature sparse sur le modèle national.

  - DÉCOUVERTE PLUS IMPORTANTE, SANS RAPPORT AVEC LE TRAFIC : le modèle national avec son
    seuil unique calibré à 0,44 (sur la validation 2022) donne un **recall de 0,007 sur le
    sous-ensemble parisien** (3 accidents graves détectés sur 431 !), alors que le recall
    national agrégé de 0,808 valide le seuil CDC. Vérifié indépendamment (probabilités
    prédites pour Paris : min 0,035, médiane 0,108, max 0,514 — presque toutes sous le seuil
    national). Ce n'est pas un bug : Paris a structurellement moins d'accidents graves (~9 %
    vs ~36 % national), donc le modèle y prédit systématiquement des probabilités plus
    faibles, qui ne franchissent quasiment jamais un seuil calibré sur la distribution
    nationale.
    -> Un seuil de décision UNIQUE au niveau national masque un angle mort de sécurité sur
    les sous-populations à risque de base plus faible (zones urbaines denses type Paris, et
    probablement d'autres). Le recall global du CDC (>=0,80) peut être atteint tout en ratant
    presque tous les accidents graves d'une zone entière. À traiter avant mise en production :
    calibration de seuil par contexte (zone/dep, urbain vs rural) ou recalibration des
    probabilités, plutôt qu'un seuil global unique.
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


def load_caracteristiques_national(year: int) -> pl.DataFrame:
    """Toute la France (pas de filtre dep), avec lat/long conservés pour la jointure trafic."""
    df = pl.read_csv(BAAC_DIR / CARACT_FILES[year], separator=";", infer_schema_length=0)
    id_col = "Accident_Id" if "Accident_Id" in df.columns else "Num_Acc"
    return df.select(
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


def load_national_year(year: int) -> pl.DataFrame:
    caract = load_caracteristiques_national(year)
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
        .drop_nulls(["is_grave"])
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
    if not week_files or candidates.height == 0:
        return pl.DataFrame(
            schema={"iu_ac": pl.Utf8, "t_1h": pl.Datetime, "debit": pl.Float64, "taux_occupation": pl.Float64}
        )
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


def compute_paris_traffic_feature(year: int, national_year: pl.DataFrame, sensors: pl.DataFrame) -> pl.DataFrame:
    """Renvoie (num_acc, debit, taux_occupation) uniquement pour les accidents parisiens
    matchés — le reste (hors Paris) restera null après la jointure avec le national."""
    paris = national_year.filter(pl.col("dep") == "75").drop_nulls(["acc_lat", "acc_lon"]).with_columns(
        pl.datetime(pl.col("annee"), pl.col("mois"), pl.col("jour"))
        .dt.combine(pl.col("hrmn").str.strptime(pl.Time, "%H:%M", strict=False))
        .dt.truncate("1h")
        .alias("acc_hour")
    ).drop_nulls(["acc_hour"])

    nearest = nearest_sensor_per_accident(paris, sensors)
    traffic = collect_traffic_for_year(year, nearest)
    matched = nearest.join(
        traffic, left_on=["iu_ac", "acc_hour"], right_on=["iu_ac", "t_1h"], how="inner"
    ).unique(subset=["num_acc"], keep="first")
    return matched.select("num_acc", *TRAFFIC_FEATURES)


def build_national_dataset_with_sparse_traffic(sensors: pl.DataFrame) -> pl.DataFrame:
    all_years = []
    for year in range(2019, 2024):
        print(f"Année {year}...")
        national_year = load_national_year(year)
        traffic_feature = compute_paris_traffic_feature(year, national_year, sensors)
        merged = national_year.join(traffic_feature, on="num_acc", how="left")
        all_years.append(merged)
    return pl.concat(all_years, how="diagonal_relaxed")


def prepare_features(df: pl.DataFrame, feature_cols: list[str]) -> pl.DataFrame:
    return df.with_columns(
        [pl.col(c).cast(pl.Categorical) for c in CATEGORICAL_FEATURES if c in feature_cols]
    ).with_columns(
        [pl.col(c).cast(pl.Float64, strict=False) for c in NUMERIC_FEATURES + TRAFFIC_FEATURES if c in feature_cols]
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
        n_estimators=300, learning_rate=0.05, class_weight="balanced", random_state=42, verbosity=-1,
    )
    model.fit(X_train, y_train, categorical_feature=cat_in_use)

    proba_valid = model.predict_proba(X_valid)[:, 1]
    thresholds = np.linspace(0.01, 0.99, 99)
    valid_recalls = [recall_score(y_valid, (proba_valid >= t).astype(int)) for t in thresholds]
    eligible = [t for t, r in zip(thresholds, valid_recalls) if r >= TARGET_RECALL]
    tuned_threshold = max(eligible) if eligible else thresholds[int(np.argmax(valid_recalls))]

    proba_test = model.predict_proba(X_test)[:, 1]
    y_pred = (proba_test >= tuned_threshold).astype(int)

    print(f"\n--- Modèle {label} ---")
    print(f"Train {train.height} / Valid {valid.height} / Test {test.height}")
    print(f"Seuil calibré (2022) : {tuned_threshold:.2f}")

    recall_national = recall_score(y_test, y_pred)
    f1_national = f1_score(y_test, y_pred, average="macro")
    print(f"NATIONAL   -> recall={recall_national:.3f}  F1 macro={f1_national:.3f}")

    # Découpage a posteriori sur le sous-ensemble parisien du même test set.
    test_pd = test.to_pandas()
    is_paris = (test_pd["dep"] == "75").to_numpy()
    recall_paris = recall_score(y_test[is_paris], y_pred[is_paris])
    f1_paris = f1_score(y_test[is_paris], y_pred[is_paris], average="macro")
    print(f"PARIS seul -> recall={recall_paris:.3f}  F1 macro={f1_paris:.3f}  (n={is_paris.sum()})")

    print(classification_report(y_test, y_pred, target_names=["non_grave", "grave"]))

    return {
        "label": label, "threshold": tuned_threshold,
        "recall_national": recall_national, "f1_national": f1_national,
        "recall_paris": recall_paris, "f1_paris": f1_paris,
    }


def main() -> None:
    sensors = load_sensors()
    df = build_national_dataset_with_sparse_traffic(sensors)

    n = df.height
    n_paris = df.filter(pl.col("dep") == "75").height
    n_paris_with_traffic = df.filter((pl.col("dep") == "75") & pl.col("debit").is_not_null()).height
    print(f"\nAccidents nationaux 2019-2023 : {n}")
    print(f"dont Paris                     : {n_paris} ({n_paris / n:.1%})")
    print(f"dont Paris avec trafic non-null : {n_paris_with_traffic} ({n_paris_with_traffic / n:.1%} du total national)")

    baac_only_cols = CARACT_FEATURES + LIEUX_FEATURES + ["nb_vehicules", "heure", "jour_semaine", "mois"]
    baac_plus_trafic_cols = baac_only_cols + TRAFFIC_FEATURES

    result_a = train_and_evaluate(df, baac_only_cols, "A) National, BAAC seul")
    result_b = train_and_evaluate(df, baac_plus_trafic_cols, "B) National, BAAC + trafic sparse (Paris)")

    print("\n" + "=" * 60)
    print("COMPARAISON FINALE — modèle NATIONAL, avec vs sans trafic sparse")
    print("=" * 60)
    print(f"A) BAAC seul     -> national: recall={result_a['recall_national']:.3f} F1={result_a['f1_national']:.3f}"
          f"  | Paris seul: recall={result_a['recall_paris']:.3f} F1={result_a['f1_paris']:.3f}")
    print(f"B) BAAC+trafic   -> national: recall={result_b['recall_national']:.3f} F1={result_b['f1_national']:.3f}"
          f"  | Paris seul: recall={result_b['recall_paris']:.3f} F1={result_b['f1_paris']:.3f}")
    print(f"Gain recall (national) : {result_b['recall_national'] - result_a['recall_national']:+.3f}")
    print(f"Gain F1 (national)     : {result_b['f1_national'] - result_a['f1_national']:+.3f}")
    print(f"Gain recall (Paris)    : {result_b['recall_paris'] - result_a['recall_paris']:+.3f}")
    print(f"Gain F1 (Paris)        : {result_b['f1_paris'] - result_a['f1_paris']:+.3f}")


if __name__ == "__main__":
    main()
