"""Exploration : le flux trafic national DATEX II (transport.data.gouv.fr) est-il exploitable
comme enrichissement pour GRAVIA ?

Trois ressources du dataset "État de circulation temps réel sur le réseau national routier
non concédé" :
- 79165 : vitesses moyennes / débits par site de mesure (XML DATEX II, snapshot ~6 min)
- 79167 : table de référence des sites de mesure (CSV) — géolocalise chaque site
- 79166 : statut trafic autour des grandes agglomérations — sert un index de répertoires
  (publication Bison Futé par DIR), pas un fichier unique. Hors scope de ce mini ETL.

PIÈGE QUALITÉ (constaté à l'exploration). La table de référence 79167 est décalée : son
en-tête déclare 20 colonnes (dont `code_insee_commune`) mais chaque ligne n'en contient que
19. La colonne `code_insee_commune` est un FANTÔME — elle n'existe pas dans les données et
décale tout le reste d'un cran. Conséquences :
  - il n'y a AUCUN code INSEE de commune dans cette table (donc pas de jointure directe à
    BAAC sur la commune, contrairement à ce que l'en-tête laisse croire) ;
  - le seul rattachement géographique fiable est un couple de coordonnées Lambert93
    (EPSG:2154), présent pour ~61 % des sites seulement.
La jointure DATEX -> BAAC devra donc être SPATIALE (reprojeter Lambert93 -> WGS84, rattacher
au point d'accident le plus proche), pas par clé commune.

Ce script télécharge un snapshot, le parse, lit la table de référence en corrigeant le
décalage de colonnes, et rapporte la couverture géographique réellement exploitable — pas de
features ni de modèle à ce stade.

RÉSULTATS (snapshot du 2026-07-01, ~1086 sites nationaux) :
  - Mesures live PROPRES : débit + vitesse renseignés partout, seulement 2 vitesses
    sentinelles ; après nettoyage, médianes débit ~1880 véh/h, vitesse ~82 km/h (max 120).
  - Couverture géo pour jointure BAAC : ~61 % des sites seulement (coords Lambert93).
  - LIMITE MAJEURE À TRANCHER : ces ressources sont du TEMPS RÉEL (snapshot ~6 min), pas un
    archivage historique. Pour entraîner sur le BAAC historique il n'y a pas de trafic
    historique correspondant ici → vérifier l'existence d'archives avant de compter sur ce
    trafic comme feature d'entraînement (le CDC le suppose "prioritaire car archivé").
"""

import sys
import urllib.request
import xml.etree.ElementTree as ET
from pathlib import Path

import polars as pl

# Console Windows en cp1252 par défaut : force l'UTF-8 pour les tableaux polars.
sys.stdout.reconfigure(encoding="utf-8")

RAW_DIR = Path(__file__).resolve().parent.parent / "data" / "raw" / "trafic_datex"

SPEED_FLOW_URL = "https://transport.data.gouv.fr/resources/79165/download"
SITE_REF_URL = "https://transport.data.gouv.fr/resources/79167/download"

DATEX_NS = "{http://datex2.eu/schema/2/2_0}"


def fetch(url: str, dest: Path) -> Path:
    """Télécharge une ressource si elle n'est pas déjà en cache local."""
    if not dest.exists():
        dest.parent.mkdir(parents=True, exist_ok=True)
        with urllib.request.urlopen(url, timeout=30) as response:
            dest.write_bytes(response.read())
    return dest


def parse_speed_flow(xml_path: Path) -> pl.DataFrame:
    """Extrait (site_id, horodatage, débit véh/h, vitesse moyenne km/h) du snapshot XML."""
    tree = ET.parse(xml_path)
    rows = []
    for site in tree.getroot().iter(f"{DATEX_NS}siteMeasurements"):
        site_id_el = site.find(f"{DATEX_NS}measurementSiteReference")
        site_id = site_id_el.get("id") if site_id_el is not None else None
        timestamp_el = site.find(f"{DATEX_NS}measurementTimeDefault")
        timestamp = timestamp_el.text if timestamp_el is not None else None

        flow = site.find(f".//{DATEX_NS}vehicleFlowRate")
        speed = site.find(f".//{DATEX_NS}speed")

        rows.append(
            {
                "site_id": site_id,
                "measurement_time": timestamp,
                "flow_veh_per_h": float(flow.text) if flow is not None else None,
                "avg_speed_kmh": float(speed.text) if speed is not None else None,
            }
        )
    return pl.DataFrame(rows)


# La ligne de données ne contient pas de champ `code_insee_commune` : on mappe donc les
# colonnes par POSITION (19 champs réels), pas par nom d'en-tête, qui est décalé.
SITE_REF_COLUMNS = [
    "site_id",  # code_pme
    "gestionnaire",  # source (DIR)
    "source_2",
    "axe",  # ex: A28, N147
    "pr_debut",
    "abscisse_debut",
    "pr_fin",
    "abscisse_fin",
    "sens_id",
    "sens_gestionnaire",
    "sens_cardinal",
    "sens_migratoire",
    "longueur",
    "nb_voies",
    "x_deb_l93",  # Lambert93 X (EPSG:2154)
    "y_deb_l93",  # Lambert93 Y
    "x_fin_l93",
    "y_fin_l93",
    "code_traficolor",
]


def parse_site_reference(csv_path: Path) -> pl.DataFrame:
    """Charge la table de référence des sites en corrigeant le décalage de colonnes.

    L'en-tête d'origine est ignoré (`code_insee_commune` est un fantôme) : on lit les 19
    champs réels par position via ``SITE_REF_COLUMNS``.
    """
    with open(csv_path, encoding="utf-8") as f:
        rows = [line.rstrip("\n").split(";") for line in f]
    rows = rows[1:]  # ignore l'en-tête décalé
    df = pl.DataFrame(rows, schema=SITE_REF_COLUMNS, orient="row")
    return df.select(
        "site_id",
        "gestionnaire",
        "axe",
        pl.col("x_deb_l93").cast(pl.Float64, strict=False),
        pl.col("y_deb_l93").cast(pl.Float64, strict=False),
    )


def main() -> None:
    speed_flow_path = fetch(SPEED_FLOW_URL, RAW_DIR / "speed_flow_snapshot.xml")
    site_ref_path = fetch(SITE_REF_URL, RAW_DIR / "site_reference.csv")

    measurements = parse_speed_flow(speed_flow_path)
    sites = parse_site_reference(site_ref_path)

    joined = measurements.join(sites, on="site_id", how="left")

    n_sites = measurements.height
    n_geo = joined.filter(
        pl.col("x_deb_l93").is_not_null() & pl.col("y_deb_l93").is_not_null()
    ).height

    print(f"Sites mesurés dans le snapshot           : {n_sites}")
    print(
        f"Sites géolocalisables (Lambert93, jointure spatiale BAAC) : "
        f"{n_geo} ({n_geo / n_sites:.0%})"
    )
    print()
    print("Complétude des mesures (débit / vitesse) dans le snapshot live :")
    print(
        joined.select(
            pl.col("flow_veh_per_h").is_not_null().mean().alias("pct_debit_renseigne"),
            pl.col("avg_speed_kmh").is_not_null().mean().alias("pct_vitesse_renseignee"),
        )
    )
    print()
    print("Statistiques débit / vitesse :")
    print(
        joined.select(
            pl.col("flow_veh_per_h").min().alias("debit_min"),
            pl.col("flow_veh_per_h").median().alias("debit_med"),
            pl.col("flow_veh_per_h").max().alias("debit_max"),
            pl.col("avg_speed_kmh").min().alias("vitesse_min"),
            pl.col("avg_speed_kmh").median().alias("vitesse_med"),
            pl.col("avg_speed_kmh").max().alias("vitesse_max"),
        )
    )
    print()
    print("Aperçu :")
    print(joined.head(10))
    print()
    print("Répartition par gestionnaire (DIR) :")
    print(joined.group_by("gestionnaire").len().sort("len", descending=True))


if __name__ == "__main__":
    main()
