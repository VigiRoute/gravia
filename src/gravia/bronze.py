"""Couche Bronze : ingestion brute des millésimes BAAC en Parquet.

Principe de la couche : **fidélité à la source**. Aucune valeur n'est nettoyée, castée ni
filtrée ici — les codes `" -1"`, les champs vides et les formats incohérents d'une année à
l'autre sont conservés tels quels. Toutes les colonnes sont lues en texte pour éviter toute
coercion silencieuse (un `catv` lu en entier perdrait les `" -1"` ; une date zéro-paddée
« 05 » deviendrait 5 sans trace). Le nettoyage, le typage et la pseudonymisation relèvent
de la couche Silver.

Une seule exception structurelle, documentée : le nom de la colonne identifiant est
harmonisé (cf. `ACCIDENT_ID_ALIASES`), car le producteur l'a renommée `Accident_Id` sur le
millésime 2022 uniquement. Sans cette harmonisation les partitions annuelles n'auraient pas
le même schéma et ne pourraient pas être lues ensemble. **Les valeurs ne sont pas touchées,
seul l'en-tête l'est.**

Chaque enregistrement est enrichi de colonnes de provenance (`_millesime`, `_source_file`,
`_ingested_at`) qui assurent la traçabilité exigée par le plan de gouvernance.

Utilisation :
    python -m gravia.bronze                  # ingère 2019-2023 et téléverse sur MinIO
    python -m gravia.bronze --years 2023     # un seul millésime
    python -m gravia.bronze --no-upload      # production locale seulement
"""

from __future__ import annotations

import argparse
import sys
from datetime import UTC, datetime
from pathlib import Path

import polars as pl

from gravia.config import Settings, get_settings

if hasattr(sys.stdout, "reconfigure"):
    # Absent sous `airflow tasks test`, qui enveloppe stdout dans `RedactedIO` (ne délègue pas
    # cet attribut) — sans incidence dans ce contexte, pas de console Windows à ré-encoder.
    sys.stdout.reconfigure(encoding="utf-8")

#: Millésimes BAAC ingérés par défaut. Le schéma est stable sur cette période
#: (cf. CLAUDE.md, pièges de schéma) ; élargir impose de revérifier les en-têtes.
DEFAULT_YEARS: tuple[int, ...] = (2019, 2020, 2021, 2022, 2023)

#: Les 4 tables BAAC, avec les variantes de nom de fichier réellement rencontrées.
#: `caracteristiques` en cumule trois : l'orthographe correcte, la coquille du producteur
#: (« carcteristiques », sans le « a », sur 2021 et 2022) et la forme abrégée de 2023.
SOURCE_FILE_PATTERNS: dict[str, tuple[str, ...]] = {
    "caracteristiques": (
        "caracteristiques-{year}.csv",
        "carcteristiques-{year}.csv",
        "caract-{year}.csv",
    ),
    "lieux": ("lieux-{year}.csv",),
    "vehicules": ("vehicules-{year}.csv",),
    "usagers": ("usagers-{year}.csv",),
}

#: Noms sous lesquels l'identifiant d'accident apparaît selon les millésimes.
#: `Accident_Id` ne concerne que le fichier caractéristiques 2022.
ACCIDENT_ID_ALIASES: tuple[str, ...] = ("Num_Acc", "Accident_Id")

#: Nom canonique de l'identifiant après harmonisation.
ACCIDENT_ID = "Num_Acc"

#: Séparateur des CSV BAAC.
CSV_SEPARATOR = ";"


class SourceFileNotFoundError(FileNotFoundError):
    """Aucun fichier source ne correspond à une table et un millésime attendus."""


class MissingAccidentIdError(ValueError):
    """Le fichier ne contient aucune colonne identifiant d'accident reconnue."""


def resolve_source_file(table: str, year: int, baac_dir: Path) -> Path:
    """Localise le fichier CSV d'une table pour un millésime donné.

    Essaie successivement les variantes de nom connues pour la table, ce qui absorbe les
    changements de convention du producteur d'une année à l'autre.

    Args:
        table: Nom logique de la table (clé de `SOURCE_FILE_PATTERNS`).
        year: Millésime recherché.
        baac_dir: Répertoire contenant les CSV BAAC bruts.

    Returns:
        Le chemin du fichier trouvé.

    Raises:
        KeyError: Si la table n'est pas connue.
        SourceFileNotFoundError: Si aucune variante de nom n'existe sur le disque.
    """
    patterns = SOURCE_FILE_PATTERNS[table]
    for pattern in patterns:
        candidate = baac_dir / pattern.format(year=year)
        if candidate.exists():
            return candidate

    tried = ", ".join(pattern.format(year=year) for pattern in patterns)
    raise SourceFileNotFoundError(
        f"Aucun fichier pour la table '{table}' du millésime {year} dans {baac_dir}. "
        f"Noms essayés : {tried}."
    )


def normalize_accident_id(df: pl.DataFrame, source: Path) -> pl.DataFrame:
    """Harmonise le nom de la colonne identifiant d'accident.

    Seul l'en-tête est modifié ; aucune valeur n'est transformée.

    Args:
        df: Table lue telle quelle depuis le CSV.
        source: Chemin du fichier source, cité en cas d'erreur.

    Returns:
        La table dont l'identifiant s'appelle `Num_Acc`.

    Raises:
        MissingAccidentIdError: Si aucun alias connu n'est présent.
    """
    if ACCIDENT_ID in df.columns:
        return df

    for alias in ACCIDENT_ID_ALIASES:
        if alias in df.columns:
            return df.rename({alias: ACCIDENT_ID})

    raise MissingAccidentIdError(
        f"{source.name} ne contient aucune colonne identifiant parmi {ACCIDENT_ID_ALIASES}. "
        f"Colonnes présentes : {df.columns}."
    )


def read_raw_csv(path: Path) -> pl.DataFrame:
    """Lit un CSV BAAC sans aucune inférence de type.

    `infer_schema_length=0` force toutes les colonnes en texte : les codes sentinelles
    (`" -1"`), les zéros de tête et les décimales à virgule sont préservés à l'identique
    pour que la couche Silver décide seule de leur interprétation.

    Args:
        path: Chemin du CSV source.

    Returns:
        La table, toutes colonnes en `Utf8`.
    """
    return pl.read_csv(path, separator=CSV_SEPARATOR, infer_schema_length=0)


def add_provenance(
    df: pl.DataFrame, year: int, source: Path, ingested_at: datetime
) -> pl.DataFrame:
    """Ajoute les colonnes de traçabilité exigées par le plan de gouvernance.

    Args:
        df: Table ingérée.
        year: Millésime d'origine.
        source: Fichier source.
        ingested_at: Horodatage d'ingestion, partagé par tout un lot pour qu'une exécution
            soit identifiable d'un seul coup.

    Returns:
        La table augmentée de `_millesime`, `_source_file` et `_ingested_at`.
    """
    return df.with_columns(
        pl.lit(year, dtype=pl.Int32).alias("_millesime"),
        pl.lit(source.name).alias("_source_file"),
        pl.lit(ingested_at).alias("_ingested_at"),
    )


def ingest_table(table: str, year: int, settings: Settings, ingested_at: datetime) -> Path:
    """Ingère une table d'un millésime et écrit le Parquet Bronze local.

    Args:
        table: Nom logique de la table.
        year: Millésime à ingérer.
        settings: Configuration (chemins source et destination).
        ingested_at: Horodatage d'ingestion du lot.

    Returns:
        Le chemin du fichier Parquet écrit.
    """
    source = resolve_source_file(table, year, settings.paths.baac)
    df = read_raw_csv(source)
    df = normalize_accident_id(df, source)
    df = add_provenance(df, year, source, ingested_at)

    destination = bronze_path(table, year, settings)
    destination.parent.mkdir(parents=True, exist_ok=True)
    df.write_parquet(destination, compression="snappy")

    print(f"  {table:16s} {year}  {df.height:>7} lignes  ->  {destination.name}")
    return destination


def bronze_path(table: str, year: int, settings: Settings) -> Path:
    """Chemin local du Parquet Bronze d'une table et d'un millésime.

    Le partitionnement par millésime reprend la granularité de publication du BAAC : un
    nouveau millésime s'ajoute sans réécrire les précédents.

    Args:
        table: Nom logique de la table.
        year: Millésime.
        settings: Configuration.

    Returns:
        Le chemin cible.
    """
    return settings.paths.bronze / "baac" / table / f"millesime={year}" / "part-0.parquet"


def object_key(table: str, year: int) -> str:
    """Clé du même objet dans le stockage objet.

    Args:
        table: Nom logique de la table.
        year: Millésime.

    Returns:
        La clé S3, sans le nom du bucket.
    """
    return f"bronze/baac/{table}/millesime={year}/part-0.parquet"


def upload(paths: dict[str, Path], settings: Settings) -> None:
    """Téléverse les Parquet Bronze produits vers le stockage objet.

    Args:
        paths: Association clé d'objet -> fichier local à téléverser.
        settings: Configuration du stockage objet.
    """
    import boto3

    store = settings.object_store
    client = boto3.client(
        "s3",
        endpoint_url=store.endpoint or None,
        aws_access_key_id=store.access_key,
        aws_secret_access_key=store.secret_key,
        region_name=store.region,
    )

    for key, local_path in paths.items():
        client.upload_file(str(local_path), store.bucket, key)

    print(f"\n{len(paths)} objets téléversés vers s3://{store.bucket}/bronze/baac/")


def ingest(years: tuple[int, ...], settings: Settings, do_upload: bool = True) -> dict[str, Path]:
    """Ingère l'ensemble des tables BAAC pour les millésimes demandés.

    L'opération est **idempotente** : relancer sur un millésime déjà ingéré réécrit les
    mêmes fichiers et les mêmes clés d'objet, sans doublon ni effet de bord.

    Args:
        years: Millésimes à ingérer.
        settings: Configuration.
        do_upload: Téléverser vers le stockage objet après écriture locale.

    Returns:
        Association clé d'objet -> fichier local produit.
    """
    ingested_at = datetime.now(UTC)
    produced: dict[str, Path] = {}

    for year in years:
        print(f"\nMillésime {year}")
        for table in SOURCE_FILE_PATTERNS:
            path = ingest_table(table, year, settings, ingested_at)
            produced[object_key(table, year)] = path

    if do_upload:
        upload(produced, settings)

    return produced


def main() -> None:
    """Point d'entrée en ligne de commande."""
    parser = argparse.ArgumentParser(description="Ingestion Bronze des millésimes BAAC.")
    parser.add_argument(
        "--years",
        type=int,
        nargs="+",
        default=list(DEFAULT_YEARS),
        help=f"Millésimes à ingérer (défaut : {' '.join(map(str, DEFAULT_YEARS))}).",
    )
    parser.add_argument(
        "--no-upload",
        action="store_true",
        help="Produire les Parquet localement sans téléverser vers le stockage objet.",
    )
    args = parser.parse_args()

    produced = ingest(tuple(args.years), get_settings(), do_upload=not args.no_upload)
    print(f"\nBronze : {len(produced)} tables ingérées.")


if __name__ == "__main__":
    main()
