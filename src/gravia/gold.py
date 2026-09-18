"""Couche Gold : jointure des tables Silver, construction du label et du schéma en étoile.

C'est ici, et uniquement ici, que les 4 tables BAAC sont jointes (`Num_Acc`) — Bronze et Silver
traitent chaque table indépendamment (cf. `gravia.silver`, docs/Architecture_GRAVIA.md §5). Le
schéma physique (`gold_fact_accident`, `gold_dim_date`, `gold_dim_lieu`, `gold_dim_conditions`,
`gold_dim_collision`) suit fidèlement docs/Architecture_GRAVIA.md §4.2, DDL dans
`data/models/gold_schema.sql`.

Écarts assumés par rapport au schéma d'origine (documentés ici, pas silencieux) :
    - Les flags véhicule/usager du schéma d'origine (`flag_moto`, `flag_poids_lourd`,
      `flag_pieton`) n'avaient jamais été implémentés ni testés. Ils sont remplacés par la
      configuration réellement validée dans `notebooks/eval_enrichissement_vs_seuil.ipynb`
      (`flag_2roues_motorise`, `flag_poids_lourd`, `flag_velo_edp`, `flag_pieton`), qui inclut un
      flag de plus et un périmètre `catv` différent pour le premier. Les codes `catv` sont repris
      à l'identique de ce notebook, pas redevinés.
    - `departement` est en `VARCHAR`, pas `INTEGER` comme esquissé dans le diagramme d'origine :
      les codes INSEE de Corse (`2A`, `2B`) ne sont pas numériques.
    - `type_collision` est décodé en libellé texte (dictionnaire ONISR) plutôt que de garder le
      code brut `col`, pour correspondre au typage `string` du diagramme d'origine — les autres
      dimensions gardent des codes bruts (`int`), ce n'est donc pas systématique.
    - `gold_dim_lieu` porte 8 attributs de plus que le diagramme d'origine (`intersection`,
      `regime_circulation`, `nb_voies`, `voie_reservee`, `profil_route`, `trace_plan`,
      `infrastructure`, `situation`) : le diagramme ne prévoyait que 4 attributs, insuffisant
      pour reproduire le baseline déjà validé (`notebooks/eda_baseline_baac.ipynb`), qui les utilise
      tous. Trouvé en préparant `ml/features` — corrigé avant d'aller plus loin plutôt que de
      construire les features sur un Gold structurellement incomplet.

Construction du label : `is_grave` = au moins un usager avec `grav ∈ {2, 3}` (tué ou hospitalisé)
rattaché à l'accident (cf. CLAUDE.md, définition de la cible ; CDC §3). Une absence totale
d'usager rattaché à un accident est traitée comme une anomalie de données et lève une exception
plutôt que de deviner silencieusement une étiquette.

`jour_ferie` est un enrichissement neuf, non testé en amont dans un notebook (contrairement aux
autres colonnes de ce module) : calculé pour les jours fériés légaux de France métropolitaine
(8 dates fixes + 3 dates mobiles dérivées de Pâques via `dateutil.easter`, désormais dépendance
figée explicite du projet).

Utilisation :
    python -m gravia.gold                  # charge 2019-2023 dans PostgreSQL
    python -m gravia.gold --years 2023     # un seul millésime
"""

from __future__ import annotations

import argparse
import re
import sys
from dataclasses import dataclass
from datetime import date, timedelta

import polars as pl
import sqlalchemy as sa
from dateutil.easter import easter

from gravia.bronze import DEFAULT_YEARS
from gravia.config import PROJECT_ROOT, Settings, get_settings
from gravia.silver import silver_path

if hasattr(sys.stdout, "reconfigure"):
    # Absent sous `airflow tasks test`, qui enveloppe stdout dans `RedactedIO` (ne délègue pas
    # cet attribut) — sans incidence dans ce contexte, pas de console Windows à ré-encoder.
    sys.stdout.reconfigure(encoding="utf-8")

#: Codes `catv` (véhicules) vérifiés empiriquement dans
#: notebooks/eval_enrichissement_vs_seuil.ipynb — meilleure configuration testée à ce jour.
CATV_2ROUES_MOTORISE: tuple[int, ...] = (2, 30, 31, 32, 33, 34, 35, 36, 41, 42, 43)
CATV_POIDS_LOURD: tuple[int, ...] = (13, 14, 15, 16, 17, 37, 38)
CATV_VELO_EDP: tuple[int, ...] = (1, 50, 60, 80)

#: Libellés du type de collision (`col`), dictionnaire ONISR — cf. CLAUDE.md, documentation
#: de référence. `-1` couvre aussi les codes hors nomenclature après normalisation des manquants.
COLLISION_LABELS: dict[int, str] = {
    -1: "Non renseigné",
    1: "Deux véhicules - frontale",
    2: "Deux véhicules - par l'arrière",
    3: "Deux véhicules - par le côté",
    4: "Trois véhicules et plus - en chaîne",
    5: "Trois véhicules et plus - collisions multiples",
    6: "Autre collision",
    7: "Sans collision",
}
COLLISION_LABEL_UNKNOWN = "Non renseigné"

#: Jours fériés fixes (mois, jour) — France métropolitaine.
FIXED_HOLIDAYS: tuple[tuple[int, int], ...] = (
    (1, 1),  # Jour de l'an
    (5, 1),  # Fête du Travail
    (5, 8),  # Victoire 1945
    (7, 14),  # Fête nationale
    (8, 15),  # Assomption
    (11, 1),  # Toussaint
    (11, 11),  # Armistice
    (12, 25),  # Noël
)

GOLD_SCHEMA_PATH = PROJECT_ROOT / "data" / "models" / "gold_schema.sql"

FACT_COLUMNS: tuple[str, ...] = (
    "accident_id",
    "date_key",
    "lieu_key",
    "conditions_key",
    "collision_key",
    "nb_vehicules",
    "nb_usagers",
    "flag_2roues_motorise",
    "flag_poids_lourd",
    "flag_velo_edp",
    "flag_pieton",
    "is_grave",
    "_millesime",
)


class MissingUsagersError(ValueError):
    """Un accident n'a aucun usager rattaché : impossible de construire `is_grave`."""


@dataclass(frozen=True)
class DimensionSpec:
    """Décrit comment dédupliquer et charger une dimension du schéma en étoile.

    Attributes:
        table: Nom de la table Postgres.
        key_column: Colonne de clé de substitution (`SERIAL`).
        conflict_columns: Colonnes de la contrainte `UNIQUE`, utilisées comme cible `ON CONFLICT`
            et comme clé de jointure pour ramener la clé de substitution dans la table de faits.
        insert_columns: Colonnes effectivement écrites à l'insertion (sur-ensemble de
            `conflict_columns` pour `gold_dim_date`, dont les attributs dérivés de `jour`
            dépendent de la date mais ne font pas partie de la contrainte d'unicité).
    """

    table: str
    key_column: str
    conflict_columns: tuple[str, ...]
    insert_columns: tuple[str, ...]


DATE_DIM = DimensionSpec(
    table="gold_dim_date",
    key_column="date_key",
    conflict_columns=("jour", "heure"),
    insert_columns=("jour", "heure", "jour_semaine", "weekend", "mois", "jour_ferie"),
)
LIEU_DIM_COLUMNS = (
    "departement",
    "agglomeration",
    "intersection",
    "categorie_route",
    "regime_circulation",
    "nb_voies",
    "voie_reservee",
    "profil_route",
    "trace_plan",
    "vitesse_max",
    "infrastructure",
    "situation",
)
LIEU_DIM = DimensionSpec(
    table="gold_dim_lieu",
    key_column="lieu_key",
    conflict_columns=LIEU_DIM_COLUMNS,
    insert_columns=LIEU_DIM_COLUMNS,
)
CONDITIONS_DIM = DimensionSpec(
    table="gold_dim_conditions",
    key_column="conditions_key",
    conflict_columns=("luminosite", "meteo", "etat_surface"),
    insert_columns=("luminosite", "meteo", "etat_surface"),
)
COLLISION_DIM = DimensionSpec(
    table="gold_dim_collision",
    key_column="collision_key",
    conflict_columns=("type_collision",),
    insert_columns=("type_collision",),
)


def french_public_holidays(year: int) -> set[date]:
    """Jours fériés légaux de France métropolitaine pour une année donnée.

    Args:
        year: Année.

    Returns:
        L'ensemble des 11 dates fériées (8 fixes + 3 mobiles dérivées de Pâques).
    """
    easter_sunday = easter(year)
    movable = {
        easter_sunday + timedelta(days=1),  # Lundi de Pâques
        easter_sunday + timedelta(days=39),  # Ascension
        easter_sunday + timedelta(days=50),  # Lundi de Pentecôte
    }
    fixed = {date(year, month, day) for month, day in FIXED_HOLIDAYS}
    return fixed | movable


def aggregate_vehicules(df: pl.DataFrame) -> pl.DataFrame:
    """Agrège la rubrique VEHICULES au grain accident.

    Args:
        df: Table Silver `vehicules` d'un millésime.

    Returns:
        Une ligne par `Num_Acc` : nombre de véhicules distincts et flags de catégorie.
    """
    catv = pl.col("catv")
    return df.group_by("Num_Acc").agg(
        pl.col("num_veh").n_unique().alias("nb_vehicules"),
        catv.is_in(CATV_2ROUES_MOTORISE).any().alias("flag_2roues_motorise"),
        catv.is_in(CATV_POIDS_LOURD).any().alias("flag_poids_lourd"),
        catv.is_in(CATV_VELO_EDP).any().alias("flag_velo_edp"),
    )


def aggregate_usagers(df: pl.DataFrame) -> pl.DataFrame:
    """Agrège la rubrique USAGERS au grain accident : label et flag piéton.

    Args:
        df: Table Silver `usagers` d'un millésime.

    Returns:
        Une ligne par `Num_Acc` : nombre d'usagers, `is_grave`, `flag_pieton`.
    """
    # `.any()` sur un groupe où `grav` est entièrement null renvoie `False` (constaté en
    # testant), pas `null` : un accident dont tous les usagers auraient un `grav` illisible
    # serait donc silencieusement classé "non grave" au lieu de déclencher l'erreur ci-dessous
    # via `build_fact_frame`. Jamais rencontré sur les 5 millésimes réels (vérifié : `grav` brut
    # ne contient que "1"-"4"/" -1"/l'unique ligne d'export vide déjà filtrée en amont), mais le
    # filet de sécurité est peu coûteux à poser pour les millésimes futurs.
    return df.group_by("Num_Acc").agg(
        pl.len().alias("nb_usagers"),
        pl.when(pl.col("grav").is_null().all())
        .then(None)
        .otherwise(pl.col("grav").is_in([2, 3]).any())
        .alias("is_grave"),
        (pl.col("catu") == 3).any().alias("flag_pieton"),
    )


def build_fact_frame(year: int, settings: Settings) -> pl.DataFrame:
    """Joint les 4 tables Silver d'un millésime et construit les colonnes du schéma en étoile.

    Args:
        year: Millésime à traiter.
        settings: Configuration (chemins Silver).

    Returns:
        Une ligne par accident, avec les colonnes naturelles de chaque dimension (pas encore les
        clés de substitution, résolues par `upsert_dimension`).

    Raises:
        MissingUsagersError: Si un accident n'a aucun usager rattaché.
    """
    caract = pl.read_parquet(silver_path("caracteristiques", year, settings))
    lieux = pl.read_parquet(silver_path("lieux", year, settings))
    vehicules = aggregate_vehicules(pl.read_parquet(silver_path("vehicules", year, settings)))
    usagers = aggregate_usagers(pl.read_parquet(silver_path("usagers", year, settings)))

    df = (
        caract.join(lieux, on="Num_Acc", how="left")
        .join(vehicules, on="Num_Acc", how="left")
        .join(usagers, on="Num_Acc", how="left")
    )

    missing_label = df.filter(pl.col("is_grave").is_null()).height
    if missing_label:
        raise MissingUsagersError(
            f"{missing_label} accident(s) {year} sans usager rattaché : impossible de "
            "construire is_grave."
        )

    holidays = sorted(french_public_holidays(year))
    accident_date = pl.date(pl.col("an"), pl.col("mois"), pl.col("jour"))
    weekday = accident_date.dt.weekday()

    df = df.with_columns(accident_date.alias("_jour_date"))
    n_before = df.height
    df = df.filter(pl.col("_jour_date").is_not_null())
    dropped = n_before - df.height
    if dropped:
        print(f"  [avertissement] {dropped} accident(s) {year} sans date exploitable, exclus.")

    return df.select(
        pl.col("Num_Acc").alias("accident_id"),
        pl.col("_jour_date").alias("jour"),
        pl.col("hrmn").str.slice(0, 2).cast(pl.Int8, strict=False).alias("heure"),
        weekday.alias("jour_semaine"),
        weekday.is_in([6, 7]).alias("weekend"),
        pl.col("mois"),
        pl.col("_jour_date").is_in(holidays).alias("jour_ferie"),
        pl.col("dep").alias("departement"),
        (pl.col("agg").fill_null(-1) == 2).alias("agglomeration"),
        pl.col("int").fill_null(-1).alias("intersection"),
        pl.col("catr").fill_null(-1).alias("categorie_route"),
        pl.col("circ").fill_null(-1).alias("regime_circulation"),
        pl.col("nbv").fill_null(-1).alias("nb_voies"),
        pl.col("vosp").fill_null(-1).alias("voie_reservee"),
        pl.col("prof").fill_null(-1).alias("profil_route"),
        pl.col("plan").fill_null(-1).alias("trace_plan"),
        pl.col("vma").fill_null(-1).alias("vitesse_max"),
        pl.col("infra").fill_null(-1).alias("infrastructure"),
        pl.col("situ").fill_null(-1).alias("situation"),
        pl.col("lum").fill_null(-1).alias("luminosite"),
        pl.col("atm").fill_null(-1).alias("meteo"),
        pl.col("surf").fill_null(-1).alias("etat_surface"),
        pl.col("col")
        .fill_null(-1)
        .cast(pl.Int32)
        .replace_strict(COLLISION_LABELS, default=COLLISION_LABEL_UNKNOWN)
        .alias("type_collision"),
        pl.col("nb_vehicules").fill_null(0),
        pl.col("nb_usagers").fill_null(0),
        pl.col("flag_2roues_motorise").fill_null(False),
        pl.col("flag_poids_lourd").fill_null(False),
        pl.col("flag_velo_edp").fill_null(False),
        pl.col("flag_pieton").fill_null(False),
        pl.col("is_grave"),
        pl.lit(year, dtype=pl.Int16).alias("_millesime"),
    )


def create_schema(engine: sa.Engine) -> None:
    """Crée le schéma en étoile Gold s'il n'existe pas déjà (DDL idempotent).

    Découpe sur `;` suivi d'une fin de ligne, pas sur `;` seul : les commentaires SQL du fichier
    contiennent eux-mêmes des points-virgules (ex. `-- -1 = non renseigné ; lieux.circ`), qui ne
    terminent jamais une ligne. Un `str.split(";")` naïf coupait au milieu d'une instruction.

    Args:
        engine: Connexion SQLAlchemy vers PostgreSQL.
    """
    ddl = GOLD_SCHEMA_PATH.read_text(encoding="utf-8")
    with engine.begin() as conn:
        for statement in filter(None, (s.strip() for s in re.split(r";\s*\n", ddl))):
            conn.execute(sa.text(statement))


def upsert_dimension(conn: sa.Connection, spec: DimensionSpec, frame: pl.DataFrame) -> pl.DataFrame:
    """Insère les combinaisons inédites d'une dimension et ramène sa clé de substitution.

    Args:
        conn: Connexion (transaction ouverte).
        spec: Description de la dimension.
        frame: Table de faits en construction, contenant les colonnes naturelles de la dimension.

    Returns:
        `frame` avec une colonne supplémentaire, nommée `spec.key_column`.
    """
    distinct = frame.select(list(spec.insert_columns)).unique()
    records = distinct.to_dicts()
    if records:
        columns_sql = ", ".join(spec.insert_columns)
        placeholders = ", ".join(f":{c}" for c in spec.insert_columns)
        conflict_sql = ", ".join(spec.conflict_columns)
        conn.execute(
            sa.text(
                f"INSERT INTO {spec.table} ({columns_sql}) VALUES ({placeholders}) "
                f"ON CONFLICT ({conflict_sql}) DO NOTHING"
            ),
            records,
        )

    lookup_columns_sql = ", ".join([spec.key_column, *spec.conflict_columns])
    rows = conn.execute(sa.text(f"SELECT {lookup_columns_sql} FROM {spec.table}")).mappings().all()
    lookup = pl.DataFrame([dict(row) for row in rows])
    return frame.join(lookup, on=list(spec.conflict_columns), how="left")


def load_fact(conn: sa.Connection, year: int, frame: pl.DataFrame) -> int:
    """Recharge `gold_fact_accident` pour un millésime (purge puis insertion).

    Args:
        conn: Connexion (transaction ouverte).
        year: Millésime traité (utilisé pour la purge ciblée).
        frame: Table de faits, munie des clés de substitution des 4 dimensions.

    Returns:
        Le nombre de lignes insérées.
    """
    conn.execute(sa.text("DELETE FROM gold_fact_accident WHERE _millesime = :year"), {"year": year})

    records = frame.select(list(FACT_COLUMNS)).to_dicts()
    if records:
        columns_sql = ", ".join(FACT_COLUMNS)
        placeholders = ", ".join(f":{c}" for c in FACT_COLUMNS)
        conn.execute(
            sa.text(f"INSERT INTO gold_fact_accident ({columns_sql}) VALUES ({placeholders})"),
            records,
        )
    return len(records)


def load_year(engine: sa.Engine, year: int, settings: Settings) -> int:
    """Construit et charge un millésime complet dans le schéma en étoile Gold.

    Args:
        engine: Connexion SQLAlchemy vers PostgreSQL.
        year: Millésime à traiter.
        settings: Configuration.

    Returns:
        Le nombre d'accidents chargés.
    """
    frame = build_fact_frame(year, settings)
    with engine.begin() as conn:
        for spec in (DATE_DIM, LIEU_DIM, CONDITIONS_DIM, COLLISION_DIM):
            frame = upsert_dimension(conn, spec, frame)
        n = load_fact(conn, year, frame)

    print(f"  {year}  {n:>7} accidents  ->  gold_fact_accident")
    return n


def load(years: tuple[int, ...], settings: Settings) -> int:
    """Charge l'ensemble des millésimes demandés dans le schéma en étoile Gold.

    Args:
        years: Millésimes à traiter.
        settings: Configuration.

    Returns:
        Le nombre total d'accidents chargés.
    """
    engine = sa.create_engine(settings.database.url)
    create_schema(engine)

    total = 0
    for year in years:
        total += load_year(engine, year, settings)
    return total


def main() -> None:
    """Point d'entrée en ligne de commande."""
    parser = argparse.ArgumentParser(description="Chargement Gold des millésimes BAAC.")
    parser.add_argument(
        "--years",
        type=int,
        nargs="+",
        default=list(DEFAULT_YEARS),
        help=f"Millésimes à traiter (défaut : {' '.join(map(str, DEFAULT_YEARS))}).",
    )
    args = parser.parse_args()

    total = load(tuple(args.years), get_settings())
    print(f"\nGold : {total} accidents chargés.")


if __name__ == "__main__":
    main()
