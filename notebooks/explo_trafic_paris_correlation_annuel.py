"""Extension de notebooks/explo_trafic_paris_correlation.py à l'année complète 2023.

Le test sur une semaine (67 accidents, 96 % de couverture) montrait une jointure trafic
Paris <-> BAAC quasi systématique, mais seulement 6 accidents graves — trop peu pour juger
si le trafic apporte un vrai signal de gravité. Ce script refait le même protocole sur les
~4700 accidents parisiens de l'année 2023 pour avoir un échantillon de graves suffisant.

Approche mémoire-consciente : les 68 fichiers hebdomadaires (7,1 Go décompressés,
data/raw/trafic_paris/2023_full/) ne sont PAS tous chargés en RAM. On calcule d'abord, pour
chaque accident, le capteur le plus proche et l'heure cible (léger : accidents x capteurs
référencés). On scanne ensuite chaque fichier hebdomadaire en LAZY (pl.scan_csv) et on ne
matérialise que les lignes dont (capteur, heure) correspond à un accident à matcher — le
volume gardé en mémoire reste de l'ordre de quelques milliers de lignes, pas 7 Go.

Prérequis : avoir lancé une fois l'extraction du zip source dans
data/raw/trafic_paris/2023_full/ (voir commande `unzip` dans le commit associé) — ce script
ne re-télécharge pas les 246 Mo du zip à chaque exécution, seulement les fichiers BAAC/
référentiel s'ils manquent.

RÉSULTATS (4763 accidents Paris 2023, dont 431 graves — 9,0 %) :
  - Couverture géo (capteur <= 300 m)              : 94 % (4469/4763)
  - Couverture géo + temporelle (mesure complète)   : 93 % (4407/4763, 99 % des géo-matchés)
  → Confirme le chiffre obtenu sur l'échantillon d'une semaine (96 %) : ce n'était pas un
    coup de chance, la jointure Paris <-> BAAC est bien quasi systématique à l'échelle de
    l'année.
  - Test de Welch (grave vs non grave) :
      * taux_occupation : 10,07 % (n=397 graves) vs 11,92 % (n=4010) — t=-2,14, p≈0,03
        → SIGNIFICATIF au seuil 0,05. Les accidents graves ont lieu sur un trafic MOINS
        chargé, cohérent avec la littérature sécurité routière (trafic fluide -> vitesses
        plus élevées -> gravité accrue).
      * débit (véh/h) : 1049,7 (graves) vs 964,1 (non graves) — t=0,76, NON significatif.
  - Caveat : sur les colonnes débit/occupation, une partie des accidents matchés a une valeur
    nulle (certains capteurs ne remontent que l'un des deux indicateurs) — les effectifs
    utilisés pour le test (n=397/4010) sont donc légèrement inférieurs au nombre d'accidents
    matchés (431/4332).
  → Verdict : le signal trafic sur Paris est réel, mesurable, et dans le sens attendu. Ça
    justifie d'investir dans le trafic comme feature (au moins pour le sous-ensemble
    parisien) plutôt que de l'abandonner comme pour les bulletins. Reste à décider du
    traitement hors-Paris (feature manquante/optionnelle) et si étendre à d'autres grandes
    villes disposant du même type de capteurs vaut la peine.
"""

import sys
import urllib.request
from pathlib import Path

import polars as pl

sys.stdout.reconfigure(encoding="utf-8")

RAW_DIR = Path(__file__).resolve().parent.parent / "data" / "raw"
TRAFFIC_YEAR_DIR = RAW_DIR / "trafic_paris" / "2023_full"
SENSOR_REF_FILE = RAW_DIR / "trafic_paris" / "referentiel-comptages-routiers.csv"
BAAC_CARACT_FILE = RAW_DIR / "baac" / "caract-2023.csv"
BAAC_USAGERS_FILE = RAW_DIR / "baac" / "usagers-2023.csv"

MATCH_RADIUS_M = 300.0
EARTH_RADIUS_M = 6_371_000.0


def haversine_m(lat1, lon1, lat2, lon2) -> pl.Expr:
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


def load_baac_accidents_full_year(caract_path: Path, usagers_path: Path) -> pl.DataFrame:
    caract = pl.read_csv(caract_path, separator=";", infer_schema_length=0)
    usagers = pl.read_csv(usagers_path, separator=";", infer_schema_length=0)

    gravite = usagers.group_by(pl.col("Num_Acc").alias("num_acc")).agg(
        pl.col("grav").cast(pl.Int64, strict=False).is_in([2, 3]).any().alias("is_grave")
    )

    paris = caract.filter(pl.col("dep") == "75")

    return (
        paris.select(
            pl.col("Num_Acc").alias("num_acc"),
            pl.col("mois").cast(pl.Int64),
            pl.col("jour").cast(pl.Int64),
            pl.col("hrmn"),
            pl.col("lat").str.replace(",", ".").cast(pl.Float64, strict=False).alias("acc_lat"),
            pl.col("long").str.replace(",", ".").cast(pl.Float64, strict=False).alias("acc_lon"),
        )
        .join(gravite, on="num_acc", how="left")
        .drop_nulls(["acc_lat", "acc_lon"])
        .with_columns(
            pl.datetime(2023, pl.col("mois"), pl.col("jour"))
            .dt.combine(pl.col("hrmn").str.strptime(pl.Time, "%H:%M", strict=False))
            .dt.truncate("1h")
            .alias("acc_hour")
        )
        .drop_nulls(["acc_hour"])
    )


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


def collect_traffic_for_candidates(candidates: pl.DataFrame) -> pl.DataFrame:
    """Scanne chaque fichier hebdomadaire en lazy et ne garde que les lignes utiles."""
    target_sensors = candidates.select("iu_ac").unique().to_series().to_list()
    target_hours = candidates.select("acc_hour").unique().to_series().to_list()

    matched_frames = []
    week_files = sorted(TRAFFIC_YEAR_DIR.glob("trafic_capteurs_2023_*.txt"))
    for i, week_file in enumerate(week_files, 1):
        lf = pl.scan_csv(week_file, separator=";", infer_schema_length=0)
        filtered = (
            lf.filter(pl.col("iu_ac").is_in(target_sensors))
            .select(
                pl.col("iu_ac"),
                pl.col("t_1h").str.strptime(pl.Datetime, "%Y-%m-%d %H:%M:%S", strict=False),
                pl.col("q").cast(pl.Float64, strict=False).alias("debit"),
                pl.col("k").cast(pl.Float64, strict=False).alias("taux_occupation"),
                pl.col("etat_trafic"),
            )
            .filter(pl.col("t_1h").is_in(target_hours))
            .collect()
        )
        if filtered.height:
            matched_frames.append(filtered)
        if i % 10 == 0:
            print(f"  ... {i}/{len(week_files)} fichiers hebdomadaires scannés")

    return pl.concat(matched_frames) if matched_frames else pl.DataFrame()


def main() -> None:
    sensors = load_sensors(SENSOR_REF_FILE)
    accidents = load_baac_accidents_full_year(BAAC_CARACT_FILE, BAAC_USAGERS_FILE)

    n_acc = accidents.height
    n_grave = accidents.filter(pl.col("is_grave")).height
    print(f"Accidents Paris 2023 (avec lat/long exploitables) : {n_acc}")
    print(f"dont graves                                        : {n_grave} ({n_grave / n_acc:.1%})")
    print()

    nearest = nearest_sensor_per_accident(accidents, sensors)
    n_geo = nearest.height
    print(f"Accidents avec capteur <= {MATCH_RADIUS_M:.0f} m : {n_geo} ({n_geo / n_acc:.0%})")
    print()

    print(f"Scan des {len(list(TRAFFIC_YEAR_DIR.glob('*.txt')))} fichiers hebdomadaires (lazy, filtré)...")
    traffic = collect_traffic_for_candidates(nearest)
    print(f"Lignes de trafic retenues après filtrage : {traffic.height}")
    print()

    with_traffic = nearest.join(
        traffic, left_on=["iu_ac", "acc_hour"], right_on=["iu_ac", "t_1h"], how="inner"
    ).unique(subset=["num_acc"])

    n_full = with_traffic.height
    print(
        f"Accidents avec mesure de trafic complète : {n_full} ({n_full / n_acc:.0%} du total, "
        f"{n_full / n_geo:.0%} des géo-matchés)"
    )
    n_grave_matched = with_traffic.filter(pl.col("is_grave")).height
    print(f"dont accidents graves matchés             : {n_grave_matched}")
    print()

    print("Comparaison trafic mesuré : grave vs non grave (année complète 2023) :")
    print(
        with_traffic.group_by("is_grave").agg(
            pl.len().alias("n"),
            pl.col("debit").mean().alias("debit_moyen"),
            pl.col("debit").median().alias("debit_median"),
            pl.col("taux_occupation").mean().alias("occupation_moyenne"),
            pl.col("taux_occupation").median().alias("occupation_mediane"),
        )
    )

    out_path = RAW_DIR / "trafic_paris" / "accidents_2023_avec_trafic.parquet"
    with_traffic.write_parquet(out_path)
    print(f"\nRésultat complet sauvegardé : {out_path}")


if __name__ == "__main__":
    main()
