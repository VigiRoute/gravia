"""Exploration : le trafic Paris (historique, capteurs permanents) apporte-t-il un signal
lié à la gravité des accidents BAAC ?

Contrairement au DATEX II (temps réel seul) et au TMJA (historique mais ~1,6 % de couverture
BAAC réelle, cf. notebooks/explo_trafic_tmja_national.py et explo_trafic_datex_national.py),
Paris a un vrai historique (archives annuelles 2010->2025, données horaires par capteur) ET
des coordonnées WGS84 directes (`geo_point_2d` dans le référentiel des sites) — donc une
jointure spatiale simple, sans reprojection Lambert93 ni artefact de colonnes décalées.

Sources (data.gouv.fr, Ville de Paris) :
  - "Comptage routier - Historique" : archives hebdomadaires par capteur (iu_ac, t_1h, q
    débit, k taux d'occupation, etat_trafic). ATTENTION : volumineux (~250 Mo compressés,
    ~7,5 Go décompressés par année, découpé en fichiers hebdomadaires) et compressé en
    Deflate64 -> le module `zipfile` de Python plante dessus (NotImplementedError), il faut
    extraire avec `unzip` (Info-ZIP) au préalable.
  - "Comptage routier - Référentiel géographique" : position (lat/lon) de chaque capteur,
    utilisable directement (pas de reprojection).

Portée de ce test (volontairement réduite, cohérent avec l'esprit "mini ETL") : une semaine
de trafic (2023-01-01 -> 2023-01-08, déjà extraite manuellement à
data/raw/trafic_paris/trafic_capteurs_2023_W00_20230101_20230108.txt) contre les accidents
BAAC parisiens (dep=75) survenus la même semaine. Objectif : mesurer le taux de rattachement
réel (accident <-> capteur à moins de 300 m, même heure) et comparer le trafic mesuré entre
accidents graves et non graves — un premier indice, pas une preuve statistique (échantillon
d'une semaine).

Label `is_grave` recalculé depuis `usagers-2023.csv` selon la règle CDC : grave si au moins un
usager a grav in {2 (tué), 3 (hospitalisé)}.

RÉSULTATS (67 accidents Paris, semaine du 2023-01-01) :
  - Couverture géo (capteur <= 300 m)         : 97 % (65/67)
  - Couverture géo + temporelle (mesure complète) : 96 % (64/67)
  → Radicalement différent des deux autres sources testées : DATEX II national (61 % géo,
    mais 0 % de jointure commune fiable, cf. explo_trafic_datex_national.py) et TMJA national
    (1,6 % de couverture réelle, cf. explo_trafic_tmja_national.py). Paris est la seule source
    où la jointure trafic <-> BAAC fonctionne quasi systématiquement.
  - Signal grave/non-grave sur cet échantillon : 6 accidents graves vs 58 non graves — bien
    trop peu pour conclure sur une corrélation. Piste à noter (pas une conclusion) : occupation
    moyenne PLUS FAIBLE pour les 6 accidents graves (5,5 %) que pour les non graves (10,3 %),
    cohérent avec un phénomène connu en sécurité routière (trafic fluide -> vitesses plus
    élevées -> accidents plus graves), mais un échantillon d'une semaine ne permet aucune
    inférence fiable ; il faudrait étendre à plusieurs mois pour trancher.
  → Verdict couverture : Paris est exploitable comme feature trafic pour les accidents
    parisiens. Reste à trancher si l'apport prédictif est réel (nécessite un échantillon plus
    large) et comment traiter le hors-Paris (feature manquante/optionnelle, cf. mémoire projet).
"""

import math
import sys
from pathlib import Path

import polars as pl

sys.stdout.reconfigure(encoding="utf-8")

RAW_DIR = Path(__file__).resolve().parent.parent / "data" / "raw"
TRAFFIC_WEEK_FILE = (
    RAW_DIR / "trafic_paris" / "trafic_capteurs_2023_W00_20230101_20230108.txt"
)
SENSOR_REF_FILE = RAW_DIR / "trafic_paris" / "referentiel-comptages-routiers.csv"
BAAC_CARACT_FILE = RAW_DIR / "baac" / "caract-2023.csv"
BAAC_USAGERS_FILE = RAW_DIR / "baac" / "usagers-2023.csv"

MATCH_RADIUS_M = 300.0
EARTH_RADIUS_M = 6_371_000.0


def haversine_m(lat1, lon1, lat2, lon2) -> pl.Expr:
    """Distance haversine (mètres) entre deux points, en expressions polars vectorisées."""
    lat1_r, lat2_r = lat1.radians(), lat2.radians()
    dlat = (lat2 - lat1).radians()
    dlon = (lon2 - lon1).radians()
    a = (dlat / 2).sin() ** 2 + lat1_r.cos() * lat2_r.cos() * (dlon / 2).sin() ** 2
    return 2 * EARTH_RADIUS_M * a.sqrt().arcsin()


def load_sensors(path: Path) -> pl.DataFrame:
    df = pl.read_csv(path, separator=";", infer_schema_length=0)
    latlon = pl.col("geo_point_2d").str.split_exact(", ", 1)
    return df.select(
        pl.col("Identifiant arc").alias("iu_ac"),
        latlon.struct.field("field_0").cast(pl.Float64).alias("sensor_lat"),
        latlon.struct.field("field_1").cast(pl.Float64).alias("sensor_lon"),
    ).unique(subset=["iu_ac"])


def load_traffic_week(path: Path) -> pl.DataFrame:
    df = pl.read_csv(path, separator=";", infer_schema_length=0)
    return df.select(
        pl.col("iu_ac"),
        pl.col("t_1h").str.strptime(pl.Datetime, "%Y-%m-%d %H:%M:%S").alias("t_1h"),
        pl.col("q").cast(pl.Float64, strict=False).alias("debit"),
        pl.col("k").cast(pl.Float64, strict=False).alias("taux_occupation"),
        pl.col("etat_trafic"),
    )


def load_baac_accidents(caract_path: Path, usagers_path: Path) -> pl.DataFrame:
    caract = pl.read_csv(caract_path, separator=";", infer_schema_length=0)
    usagers = pl.read_csv(usagers_path, separator=";", infer_schema_length=0)

    gravite = (
        usagers.group_by(pl.col("Num_Acc").alias("num_acc"))
        .agg(pl.col("grav").cast(pl.Int64, strict=False).is_in([2, 3]).any().alias("is_grave"))
    )

    paris_semaine = caract.filter(
        (pl.col("dep") == "75") & (pl.col("mois") == "01") & (pl.col("jour").cast(pl.Int64) <= 7)
    )

    return (
        paris_semaine.select(
            pl.col("Num_Acc").alias("num_acc"),
            pl.col("jour").cast(pl.Int64),
            pl.col("hrmn"),
            pl.col("lat").str.replace(",", ".").cast(pl.Float64).alias("acc_lat"),
            pl.col("long").str.replace(",", ".").cast(pl.Float64).alias("acc_lon"),
        )
        .join(gravite, on="num_acc", how="left")
        .with_columns(
            pl.datetime(2023, 1, pl.col("jour"))
            .dt.combine(pl.col("hrmn").str.strptime(pl.Time, "%H:%M"))
            .dt.truncate("1h")
            .alias("acc_hour")
        )
    )


def main() -> None:
    sensors = load_sensors(SENSOR_REF_FILE)
    traffic = load_traffic_week(TRAFFIC_WEEK_FILE)
    accidents = load_baac_accidents(BAAC_CARACT_FILE, BAAC_USAGERS_FILE)

    n_acc = accidents.height
    print(f"Accidents Paris, semaine du 2023-01-01 : {n_acc}")
    print(f"Capteurs référencés                    : {sensors.height}")
    print(f"Mesures trafic (semaine, tous capteurs) : {traffic.height}")
    print()

    # Jointure spatiale : capteur le plus proche de chaque accident (produit cartésien
    # accidents x capteurs, raisonnable ici vu la taille — 67 accidents x ~3700 capteurs).
    nearest = (
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

    n_geo_matched = nearest.height
    print(
        f"Accidents avec un capteur à <= {MATCH_RADIUS_M:.0f} m : "
        f"{n_geo_matched} ({n_geo_matched / n_acc:.0%})"
    )

    # Jointure temporelle : mesure du capteur retenu à la même heure que l'accident.
    with_traffic = nearest.join(
        traffic, left_on=["iu_ac", "acc_hour"], right_on=["iu_ac", "t_1h"], how="inner"
    )

    n_full_matched = with_traffic.height
    print(
        f"Accidents avec mesure de trafic à la même heure     : "
        f"{n_full_matched} ({n_full_matched / n_acc:.0%} du total, "
        f"{n_full_matched / n_geo_matched:.0%} des géo-matchés)"
    )
    print()

    print("Comparaison trafic mesuré : grave vs non grave (échantillon d'une semaine) :")
    print(
        with_traffic.group_by("is_grave").agg(
            pl.len().alias("n"),
            pl.col("debit").mean().alias("debit_moyen"),
            pl.col("taux_occupation").mean().alias("occupation_moyenne"),
            pl.col("dist_m").mean().alias("distance_moyenne_m"),
        )
    )
    print()
    print("Détail des accidents matchés :")
    print(
        with_traffic.select(
            "num_acc", "is_grave", "dist_m", "debit", "taux_occupation", "etat_trafic"
        )
    )


if __name__ == "__main__":
    main()
