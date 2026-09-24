"""Couche Silver : nettoyage, typage strict et pseudonymisation des tables BAAC.

Principe de la couche : chaque table Bronze (caracteristiques, lieux, vehicules, usagers) est
traitée **indépendamment**, sans jointure inter-table. La jointure (`Num_Acc`, `id_vehicule`),
l'agrégation du label `is_grave` et le schéma en étoile relèvent de la couche Gold (cf.
docs/Architecture_GRAVIA.md §5 — décision actée le 2026-08-24). Les identifiants (`Num_Acc`,
`id_vehicule`, `id_usager`, `Num_Veh`) restent en `Utf8` : ce sont des clés de jointure, pas des
quantités, et un cast numérique risquerait de tronquer un zéro de tête ou de déborder.

Les codes appliqués ici sont vérifiés contre le dictionnaire officiel ONISR (cf. CLAUDE.md,
section « Documentation de référence ») plutôt que devinés.

Pseudonymisation (cf. CLAUDE.md, données personnelles et sensibles ; docs/AIPD_GRAVIA.md §2.2/§5) :
    - Géolocalisation : `lat`/`long` (caracteristiques) et `adr` (caracteristiques, adresse
      postale en texte libre — ex. "56bis Avenue Raspail", renseignée sur la quasi-totalité des
      lignes) sont supprimées, de même que `voie`/`v1`/`v2`/`pr`/`pr1` (lieux — nom de route et
      point de repère métrique, une localisation aussi précise qu'une coordonnée une fois combinée
      au numéro de route). Trouvé en auditant l'AIPD après coup : `adr` et le triplet route+pr+pr1
      étaient conservés tels quels alors qu'ils réidentifient au moins aussi précisément qu'une
      coordonnée arrondie — le vecteur de risque que l'AIPD nomme explicitement. Aucune des deux
      couches n'est utilisée par `gravia.gold` ni par `ml/features` (vérifié), leur suppression ne
      change aucun chiffre du modèle. `com` (commune) supprimée pour la même raison, trouvé en
      auditant à nouveau après une question explicite de l'utilisateur sur la RGPD : combinée à la
      date exacte de l'accident (conservée jusqu'en Gold), une commune peu accidentogène peut
      rester le seul accident du jour dans sa cellule — un vrai vecteur de ré-identification même
      sans coordonnées précises. Un simple arrondi de `lat`/`long` plutôt que leur suppression
      avait déjà été écarté pour cette exacte raison (resterait individualisant dans une commune
      peu accidentogène) sans que le raisonnement soit appliqué à `com` elle-même jusqu'ici. `com`
      n'est utilisée nulle part en aval (vérifié : ni `gravia.gold`, ni `ml/features`, ni aucun
      notebook). La localisation reste disponible via `dep` seule (96 départements, granularité
      nettement plus grossière qu'une commune).
    - Âge : `an_nais` est remplacé par une tranche d'âge (`tranche_age`), calculée à partir de
      `_millesime` (année de l'accident, déjà présente en provenance Bronze — cf. `gravia.bronze`)
      plutôt que via `caracteristiques.an`, ce qui évite une jointure inter-table pour une simple
      date. Un âge manquant, négatif ou supérieur à 110 ans (saisie aberrante) tombe dans la
      catégorie "Inconnu" plutôt que de fausser une tranche.

Non traité à ce stade, faute de documentation vérifiée :
    - `hrmn` (heure/minutes) reste en `Utf8` ici : le dictionnaire ONISR ne précise pas son
      format. Vérifié empiriquement `"HH:MM"` sur les 5 millésimes 2019-2023 lors de
      l'implémentation de la couche Gold (cf. `gravia.gold`, qui en extrait `heure`) — non
      re-typé en Silver pour ne pas dupliquer cette logique entre les deux couches.

Qualité des données : chaque fichier BAAC source contient une ligne finale entièrement vide
(artefact d'export, `Num_Acc` et toutes les autres colonnes à `null` après lecture Bronze) —
constatée sur 17 des 20 combinaisons table/millésime 2019-2023. Filtrée en tout début de
`clean_table` (`Num_Acc` non nul), avant tout traitement spécifique à une table.

Utilisation :
    python -m gravia.silver                  # traite 2019-2023 et téléverse sur MinIO
    python -m gravia.silver --years 2023     # un seul millésime
    python -m gravia.silver --no-upload      # production locale seulement
"""

from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence
from pathlib import Path

import polars as pl

from gravia.bronze import DEFAULT_YEARS, bronze_path
from gravia.config import Settings, get_settings

if hasattr(sys.stdout, "reconfigure"):
    # Absent sous `airflow tasks test`, qui enveloppe stdout dans `RedactedIO` (ne délègue pas
    # cet attribut) — sans incidence dans ce contexte, pas de console Windows à ré-encoder.
    sys.stdout.reconfigure(encoding="utf-8")

#: Colonnes entières par table, castées avec `strict=False` pour absorber les sentinelles
#: `" -1"` et les cellules non renseignées (cf. CLAUDE.md, pièges de schéma BAAC).
CARACTERISTIQUES_INT_COLUMNS: tuple[tuple[str, pl.DataType], ...] = (
    ("jour", pl.Int8),
    ("mois", pl.Int8),
    ("an", pl.Int32),
    ("lum", pl.Int8),
    ("agg", pl.Int8),
    ("int", pl.Int8),
    ("atm", pl.Int8),
    ("col", pl.Int8),
)

#: Artefacts Excel trouvés dans `nbv` en auditant les données brutes (EDA sur Bronze) : le
#: producteur a laissé fuiter des formules non résolues dans l'export CSV (55 lignes sur
#: 273 226, 2022 et 2023 uniquement). Sans ce traitement, `cast_columns` (strict=False) les
#: transforme en NULL silencieux — indiscernable d'une valeur réellement absente — plutôt qu'en
#: la sentinelle -1 (« non renseigné ») déjà utilisée pour cette colonne partout ailleurs.
NBV_EXCEL_ARTIFACTS: tuple[str, ...] = ("#ERREUR", "#VALEURMULTI")

LIEUX_INT_COLUMNS: tuple[tuple[str, pl.DataType], ...] = (
    ("catr", pl.Int8),
    ("circ", pl.Int8),
    ("nbv", pl.Int16),
    ("vosp", pl.Int8),
    ("prof", pl.Int8),
    ("plan", pl.Int8),
    ("surf", pl.Int8),
    ("infra", pl.Int8),
    ("situ", pl.Int8),
    ("vma", pl.Int16),
)

#: Localisation précise (nom de route + point de repère métrique), supprimée par
#: pseudonymisation au même titre que `lat`/`long` (cf. docstring module) : `pr`/`pr1` combinés à
#: `voie` localisent un accident à quelques dizaines de mètres, aussi précisément qu'une
#: coordonnée.
LIEUX_DROPPED_COLUMNS: tuple[str, ...] = ("voie", "v1", "v2", "pr", "pr1")

#: Largeurs en mètres : certains millésimes utilisent la virgule décimale française. Traité à
#: part de `LIEUX_INT_COLUMNS` car il faut normaliser le séparateur avant le cast.
LIEUX_FLOAT_COLUMNS: tuple[str, ...] = ("lartpc", "larrout")

VEHICULES_INT_COLUMNS: tuple[tuple[str, pl.DataType], ...] = (
    ("senc", pl.Int8),
    ("catv", pl.Int8),
    ("obs", pl.Int8),
    ("obsm", pl.Int8),
    ("choc", pl.Int8),
    ("manv", pl.Int8),
    ("motor", pl.Int8),
    ("occutc", pl.Int16),
)

#: `actp` (action du piéton) mélange des codes numériques et les lettres `A`/`B` (cf.
#: dictionnaire ONISR) : elle reste volontairement en `Utf8`, absente de cette liste.
USAGERS_INT_COLUMNS: tuple[tuple[str, pl.DataType], ...] = (
    ("place", pl.Int8),
    ("catu", pl.Int8),
    ("grav", pl.Int8),
    ("sexe", pl.Int8),
    ("trajet", pl.Int8),
    ("secu1", pl.Int8),
    ("secu2", pl.Int8),
    ("secu3", pl.Int8),
    ("locp", pl.Int8),
    ("etatp", pl.Int8),
)

#: Bornes supérieures des tranches d'âge (`age <= borne`), évaluées dans l'ordre. Au-delà de la
#: dernière borne, l'usager tombe dans `AGE_BUCKET_SENIOR`.
AGE_BUCKETS: tuple[tuple[int, str], ...] = (
    (17, "0-17"),
    (24, "18-24"),
    (34, "25-34"),
    (49, "35-49"),
    (64, "50-64"),
)
AGE_BUCKET_SENIOR = "65+"
AGE_BUCKET_UNKNOWN = "Inconnu"
#: Age au-delà duquel une valeur est jugée aberrante (saisie erronée) plutôt que réelle.
AGE_IMPLAUSIBLE_ABOVE = 110

#: Colonnes de géolocalisation précise, supprimées par pseudonymisation (cf. docstring module).
#: `adr` (adresse postale en texte libre) réidentifie au moins aussi précisément que `lat`/`long`.
#: `com` (commune) supprimée pour la même raison : combinée à la date exacte, elle peut isoler un
#: accident unique dans une commune peu accidentogène (cf. docstring module, risque déjà identifié
#: en écartant l'arrondi de coordonnées mais pas appliqué à `com` elle-même jusqu'ici).
CARACTERISTIQUES_DROPPED_COLUMNS: tuple[str, ...] = ("lat", "long", "adr", "com")


class MissingBronzeFileError(FileNotFoundError):
    """Le Parquet Bronze attendu n'existe pas : la couche Bronze n'a pas encore été exécutée."""


def cast_columns(df: pl.DataFrame, columns: Sequence[tuple[str, pl.DataType]]) -> pl.DataFrame:
    """Caste une liste de colonnes vers leur type cible, en absorbant les valeurs invalides.

    La sentinelle `" -1"` (cf. CLAUDE.md, pièges de schéma BAAC) est précédée d'une espace dans
    la quasi-totalité des colonnes codées du BAAC (constaté au-delà des seules `grav`/`catv`/
    `catu` citées par le CLAUDE.md : `lum`, `int`, `atm`, `col`, `circ`, `vosp`, `prof`, `plan`,
    `surf`, `infra`, `situ`, `sexe`, `trajet`, `locp`…). Sans `str.strip_chars()` au préalable,
    `" -1".cast(Int8, strict=False)` renvoie `null` plutôt que `-1` : la valeur explicitement
    codée « non renseigné » par le BAAC se confondrait silencieusement avec une valeur réellement
    absente de la source, deux cas que la traçabilité gouvernance doit pouvoir distinguer.

    Args:
        df: Table à typer.
        columns: Association nom de colonne -> type Polars cible.

    Returns:
        La table avec les colonnes castées. Seule une valeur qui ne peut pas être convertie même
        après nettoyage des espaces (cellule non numérique, vide) devient `null` ; `strict=False`
        reflète la réalité d'un fichier source non contrôlé pour ces cas-là uniquement.
    """
    return df.with_columns(
        [pl.col(name).str.strip_chars().cast(dtype, strict=False) for name, dtype in columns]
    )


def clean_caracteristiques(df: pl.DataFrame) -> pl.DataFrame:
    """Nettoie la rubrique CARACTERISTIQUES : typage strict et suppression de la géoloc précise.

    Args:
        df: Table Bronze `caracteristiques` d'un millésime.

    Returns:
        La table typée, sans `lat`/`long` (pseudonymisation — cf. docstring module).
    """
    df = cast_columns(df, CARACTERISTIQUES_INT_COLUMNS)
    return df.drop(CARACTERISTIQUES_DROPPED_COLUMNS)


def clean_lieux(df: pl.DataFrame) -> pl.DataFrame:
    """Nettoie la rubrique LIEUX : typage, largeurs décimales, dédoublonnage, pseudonymisation.

    Un accident peut apparaître sur plusieurs lignes dans `lieux` (cf. CLAUDE.md, pièges de
    schéma BAAC) alors que la rubrique ne décrit qu'un seul lieu principal par accident. Faute de
    critère documenté pour départager les doublons, la première ligne rencontrée est conservée ;
    l'ordre est déterministe (celui du fichier source) mais arbitraire quant au contenu.

    Args:
        df: Table Bronze `lieux` d'un millésime.

    Returns:
        La table typée, une ligne par `Num_Acc`, sans `voie`/`v1`/`v2`/`pr`/`pr1`
        (pseudonymisation — cf. docstring module).
    """
    df = df.with_columns(
        pl.col("nbv").str.strip_chars().replace(NBV_EXCEL_ARTIFACTS, "-1").alias("nbv")
    )
    df = cast_columns(df, LIEUX_INT_COLUMNS)
    df = df.with_columns(
        [
            pl.col(col)
            .str.strip_chars()
            .str.replace(",", ".", literal=True)
            .cast(pl.Float64, strict=False)
            for col in LIEUX_FLOAT_COLUMNS
        ]
    )
    df = df.unique(subset=["Num_Acc"], keep="first")
    return df.drop(LIEUX_DROPPED_COLUMNS)


def clean_vehicules(df: pl.DataFrame) -> pl.DataFrame:
    """Nettoie la rubrique VEHICULES : typage strict des colonnes catégorielles.

    Args:
        df: Table Bronze `vehicules` d'un millésime.

    Returns:
        La table typée.
    """
    return cast_columns(df, VEHICULES_INT_COLUMNS)


def add_age_bucket(df: pl.DataFrame) -> pl.DataFrame:
    """Remplace `an_nais` par une tranche d'âge, sans jointure vers `caracteristiques`.

    L'âge est calculé à partir de `_millesime` (année de l'accident, ajoutée en provenance dans
    `gravia.bronze.add_provenance` pour les 4 tables) plutôt que de `caracteristiques.an`, ce qui
    garde le traitement de `usagers` autonome — cohérent avec le principe « pas de jointure
    inter-table en Silver ».

    Args:
        df: Table Bronze `usagers` d'un millésime, déjà munie de `_millesime`.

    Returns:
        La table avec une colonne `tranche_age` et sans `an_nais`.
    """
    age = pl.col("_millesime") - pl.col("an_nais").str.strip_chars().cast(pl.Int32, strict=False)

    bucket = pl.when(age.is_null() | (age < 0) | (age > AGE_IMPLAUSIBLE_ABOVE)).then(
        pl.lit(AGE_BUCKET_UNKNOWN)
    )
    for upper_bound, label in AGE_BUCKETS:
        bucket = bucket.when(age <= upper_bound).then(pl.lit(label))
    bucket = bucket.otherwise(pl.lit(AGE_BUCKET_SENIOR))

    return df.with_columns(bucket.alias("tranche_age")).drop("an_nais")


def clean_usagers(df: pl.DataFrame) -> pl.DataFrame:
    """Nettoie la rubrique USAGERS : typage strict et pseudonymisation de l'âge.

    Args:
        df: Table Bronze `usagers` d'un millésime.

    Returns:
        La table typée, avec `tranche_age` à la place de `an_nais`.
    """
    df = cast_columns(df, USAGERS_INT_COLUMNS)
    return add_age_bucket(df)


CLEANERS = {
    "caracteristiques": clean_caracteristiques,
    "lieux": clean_lieux,
    "vehicules": clean_vehicules,
    "usagers": clean_usagers,
}


def silver_path(table: str, year: int, settings: Settings) -> Path:
    """Chemin local du Parquet Silver d'une table et d'un millésime.

    Args:
        table: Nom logique de la table.
        year: Millésime.
        settings: Configuration.

    Returns:
        Le chemin cible.
    """
    return settings.paths.silver / "baac" / table / f"millesime={year}" / "part-0.parquet"


def object_key(table: str, year: int) -> str:
    """Clé de l'objet Silver dans le stockage objet.

    Args:
        table: Nom logique de la table.
        year: Millésime.

    Returns:
        La clé S3, sans le nom du bucket.
    """
    return f"silver/baac/{table}/millesime={year}/part-0.parquet"


def clean_table(table: str, year: int, settings: Settings) -> Path:
    """Nettoie une table d'un millésime depuis son Parquet Bronze et écrit le Parquet Silver.

    Args:
        table: Nom logique de la table (clé de `CLEANERS`).
        year: Millésime à traiter.
        settings: Configuration (chemins source et destination).

    Returns:
        Le chemin du fichier Parquet écrit.

    Raises:
        MissingBronzeFileError: Si le Parquet Bronze correspondant n'existe pas encore.
    """
    source = bronze_path(table, year, settings)
    if not source.exists():
        raise MissingBronzeFileError(
            f"Bronze introuvable pour '{table}' {year} ({source}). "
            f"Lancer 'python -m gravia.bronze --years {year}' au préalable."
        )

    df = pl.read_parquet(source)

    n_before = df.height
    df = df.filter(pl.col("Num_Acc").is_not_null())
    dropped = n_before - df.height
    if dropped:
        print(
            f"  [avertissement] {table} {year} : {dropped} ligne(s) entièrement vide(s) écartée(s)."
        )

    df = CLEANERS[table](df)

    destination = silver_path(table, year, settings)
    destination.parent.mkdir(parents=True, exist_ok=True)
    df.write_parquet(destination, compression="snappy")

    print(f"  {table:16s} {year}  {df.height:>7} lignes  ->  {destination.name}")
    return destination


def upload(paths: dict[str, Path], settings: Settings) -> None:
    """Téléverse les Parquet Silver produits vers le stockage objet.

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

    print(f"\n{len(paths)} objets téléversés vers s3://{store.bucket}/silver/baac/")


def clean(years: tuple[int, ...], settings: Settings, do_upload: bool = True) -> dict[str, Path]:
    """Nettoie l'ensemble des tables BAAC pour les millésimes demandés.

    Args:
        years: Millésimes à traiter.
        settings: Configuration.
        do_upload: Téléverser vers le stockage objet après écriture locale.

    Returns:
        Association clé d'objet -> fichier local produit.
    """
    produced: dict[str, Path] = {}

    for year in years:
        print(f"\nMillésime {year}")
        for table in CLEANERS:
            path = clean_table(table, year, settings)
            produced[object_key(table, year)] = path

    if do_upload:
        upload(produced, settings)

    return produced


def main() -> None:
    """Point d'entrée en ligne de commande."""
    parser = argparse.ArgumentParser(description="Nettoyage Silver des millésimes BAAC.")
    parser.add_argument(
        "--years",
        type=int,
        nargs="+",
        default=list(DEFAULT_YEARS),
        help=f"Millésimes à traiter (défaut : {' '.join(map(str, DEFAULT_YEARS))}).",
    )
    parser.add_argument(
        "--no-upload",
        action="store_true",
        help="Produire les Parquet localement sans téléverser vers le stockage objet.",
    )
    args = parser.parse_args()

    produced = clean(tuple(args.years), get_settings(), do_upload=not args.no_upload)
    print(f"\nSilver : {len(produced)} tables traitées.")


if __name__ == "__main__":
    main()
