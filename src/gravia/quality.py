"""Contrôle qualité de la couche Silver (Great Expectations), CDC ENF-7 / Architecture_GRAVIA.md
§2.2 (`S -.qualité.-> GE` : Great Expectations valide Silver, pas Bronze ni Gold).

Valide **Silver**, pas Bronze (fidélité brute à la source, aucune coercion à valider) ni Gold
(schéma en étoile, déjà en aval de Silver) — la même frontière que le diagramme d'architecture.

Les jeux de valeurs autorisées par colonne codée ne sont pas recopiés à l'aveugle du dictionnaire
ONISR mais **constatés empiriquement sur les 5 millésimes réels 2019-2023** déjà produits en
Silver localement. Ces contrôles formalisent en expectations exécutables les pièges de schéma
BAAC déjà documentés en prose dans CLAUDE.md (sentinelle `-1`, `id_usager` absent avant 2021…).

Volontairement pas de borne stricte sur `an` (accepte 2005-2030, la portée BAAC citée par le CDC,
cf. `PLAUSIBLE_YEAR_RANGE`) pour ne pas casser ce module au premier nouveau millésime ajouté
(cf. docs/AVANCEMENT_GRAVIA.md, « Pistes à évaluer plus tard »).

`vma` (vitesse maximale autorisée, `lieux`) tolère jusqu'à 0,1 % de valeurs aberrantes
(`mostly=0,999`) : constaté sur les données réelles, 64 lignes sur 273 226 (2019-2023) portent une
vitesse de 300 à 901 km/h — un bruit de saisie déjà présent dans la source BAAC, pas introduit par
Silver (qui type les colonnes, mais ne borne pas leurs valeurs). Toute autre colonne codée est
vérifiée strictement (aucune tolérance) : son jeu de valeurs est fermé et sans exception observée.

`id_usager` est absent des fichiers BAAC 2019-2020 (cf. CLAUDE.md, pièges de schéma) : sa
contrainte de non-nullité n'est ajoutée à la suite `usagers` que si la colonne est présente dans
le batch validé — pas une non-conformité si elle manque sur ces millésimes-là.

Usage :
    python -m gravia.quality                  # valide 2019-2023 (Silver déjà produit en local)
    python -m gravia.quality --years 2023
"""

from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass, field

import great_expectations as gx
import polars as pl

from gravia.bronze import DEFAULT_YEARS
from gravia.config import Settings, get_settings
from gravia.silver import CLEANERS, silver_path

if hasattr(sys.stdout, "reconfigure"):
    # Absent sous `airflow tasks test`, qui enveloppe stdout dans `RedactedIO` (ne délègue pas
    # cet attribut) — sans incidence dans ce contexte, pas de console Windows à ré-encoder.
    sys.stdout.reconfigure(encoding="utf-8")

#: Portée BAAC citée par le CDC (2005-2024) avec une marge pour de futurs millésimes, plutôt que
#: la liste fermée des 5 années actuellement en Silver — cf. docstring module.
PLAUSIBLE_YEAR_RANGE: tuple[int, int] = (2005, 2030)

#: `nbv`/`vma` (lieux) : cf. docstring module, tolérance empirique au bruit de saisie source.
NUMERIC_RANGE_MOSTLY = 0.999


class SilverQualityError(Exception):
    """Au moins une table Silver ne respecte pas sa suite d'expectations."""


@dataclass(frozen=True)
class TableValidationResult:
    """Résultat de validation d'une table pour un millésime, indépendant de l'objet GX interne."""

    table: str
    year: int
    success: bool
    failed_expectations: tuple[str, ...] = field(default_factory=tuple)


def _caracteristiques_expectations() -> list[gx.Expectation]:
    return [
        gx.expectations.ExpectColumnValuesToBeUnique(column="Num_Acc"),
        gx.expectations.ExpectColumnValuesToBeBetween(column="jour", min_value=1, max_value=31),
        gx.expectations.ExpectColumnValuesToBeInSet(column="mois", value_set=list(range(1, 13))),
        gx.expectations.ExpectColumnValuesToBeBetween(
            column="an", min_value=PLAUSIBLE_YEAR_RANGE[0], max_value=PLAUSIBLE_YEAR_RANGE[1]
        ),
        gx.expectations.ExpectColumnValuesToBeInSet(column="lum", value_set=[-1, 1, 2, 3, 4, 5]),
        gx.expectations.ExpectColumnValuesToBeInSet(column="agg", value_set=[1, 2]),
        gx.expectations.ExpectColumnValuesToBeInSet(column="int", value_set=list(range(-1, 10))),
        gx.expectations.ExpectColumnValuesToBeInSet(column="atm", value_set=list(range(-1, 10))),
        gx.expectations.ExpectColumnValuesToBeInSet(column="col", value_set=list(range(-1, 8))),
        gx.expectations.ExpectColumnValuesToNotBeNull(column="dep"),
    ]


def _lieux_expectations() -> list[gx.Expectation]:
    return [
        gx.expectations.ExpectColumnValuesToBeUnique(column="Num_Acc"),
        gx.expectations.ExpectColumnValuesToBeInSet(
            column="catr", value_set=[1, 2, 3, 4, 5, 6, 7, 9]
        ),
        gx.expectations.ExpectColumnValuesToBeInSet(column="circ", value_set=[-1, 1, 2, 3, 4]),
        gx.expectations.ExpectColumnValuesToBeInSet(column="vosp", value_set=[-1, 0, 1, 2, 3]),
        gx.expectations.ExpectColumnValuesToBeInSet(column="prof", value_set=[-1, 1, 2, 3, 4]),
        gx.expectations.ExpectColumnValuesToBeInSet(column="plan", value_set=[-1, 1, 2, 3, 4]),
        gx.expectations.ExpectColumnValuesToBeInSet(column="surf", value_set=list(range(-1, 10))),
        gx.expectations.ExpectColumnValuesToBeInSet(column="infra", value_set=list(range(-1, 10))),
        gx.expectations.ExpectColumnValuesToBeInSet(
            column="situ", value_set=[-1, 1, 2, 3, 4, 5, 6, 8]
        ),
        gx.expectations.ExpectColumnValuesToBeBetween(
            column="nbv", min_value=-1, max_value=20, mostly=NUMERIC_RANGE_MOSTLY
        ),
        gx.expectations.ExpectColumnValuesToBeBetween(
            column="vma", min_value=-1, max_value=200, mostly=NUMERIC_RANGE_MOSTLY
        ),
    ]


def _vehicules_expectations() -> list[gx.Expectation]:
    return [
        gx.expectations.ExpectColumnValuesToNotBeNull(column="Num_Acc"),
        gx.expectations.ExpectColumnValuesToNotBeNull(column="id_vehicule"),
        gx.expectations.ExpectColumnValuesToBeInSet(column="senc", value_set=[-1, 0, 1, 2, 3]),
        gx.expectations.ExpectColumnValuesToBeBetween(column="catv", min_value=-1, max_value=99),
        gx.expectations.ExpectColumnValuesToBeBetween(column="obs", min_value=-1, max_value=17),
        gx.expectations.ExpectColumnValuesToBeInSet(
            column="obsm", value_set=[-1, 0, 1, 2, 4, 5, 6, 9]
        ),
        gx.expectations.ExpectColumnValuesToBeBetween(column="choc", min_value=-1, max_value=9),
        gx.expectations.ExpectColumnValuesToBeBetween(column="manv", min_value=-1, max_value=26),
        gx.expectations.ExpectColumnValuesToBeBetween(column="motor", min_value=-1, max_value=6),
    ]


def _usagers_expectations(columns: list[str]) -> list[gx.Expectation]:
    expectations: list[gx.Expectation] = [
        gx.expectations.ExpectColumnValuesToNotBeNull(column="Num_Acc"),
        gx.expectations.ExpectColumnValuesToBeBetween(column="place", min_value=-1, max_value=10),
        gx.expectations.ExpectColumnValuesToBeInSet(column="catu", value_set=[1, 2, 3]),
        gx.expectations.ExpectColumnValuesToBeInSet(column="grav", value_set=[-1, 1, 2, 3, 4]),
        gx.expectations.ExpectColumnValuesToBeInSet(column="sexe", value_set=[-1, 1, 2]),
        gx.expectations.ExpectColumnValuesToBeInSet(
            column="trajet", value_set=[-1, 0, 1, 2, 3, 4, 5, 9]
        ),
        gx.expectations.ExpectColumnValuesToBeInSet(column="etatp", value_set=[-1, 1, 2, 3]),
        gx.expectations.ExpectColumnValuesToBeInSet(
            column="tranche_age",
            value_set=["0-17", "18-24", "25-34", "35-49", "50-64", "65+", "Inconnu"],
        ),
    ]
    if "id_usager" in columns:
        expectations.append(gx.expectations.ExpectColumnValuesToNotBeNull(column="id_usager"))
    return expectations


def build_expectations(table: str, columns: list[str]) -> list[gx.Expectation]:
    """Construit la liste d'expectations d'une table Silver.

    Renvoie des objets `Expectation` autonomes plutôt qu'une `ExpectationSuite` déjà peuplée :
    `ExpectationSuite.add_expectation` exige un contexte GX actif (créé plus tard, dans
    `validate_table`), donc les construire ici en amont sans suite les garde indépendants de ce
    contexte.

    Args:
        table: Nom logique de la table (`caracteristiques`, `lieux`, `vehicules`, `usagers`).
        columns: Colonnes réellement présentes dans le batch à valider (conditionne `id_usager`
            pour `usagers`, cf. docstring module).

    Returns:
        Les expectations à associer à une suite.

    Raises:
        ValueError: Si `table` n'est pas une table Silver connue.
    """
    if table == "caracteristiques":
        return _caracteristiques_expectations()
    if table == "lieux":
        return _lieux_expectations()
    if table == "vehicules":
        return _vehicules_expectations()
    if table == "usagers":
        return _usagers_expectations(columns)
    raise ValueError(f"Table Silver inconnue : {table!r} (attendu : {list(CLEANERS)})")


def validate_table(table: str, year: int, df: pl.DataFrame) -> TableValidationResult:
    """Valide une table Silver d'un millésime contre sa suite d'expectations.

    Args:
        table: Nom logique de la table.
        year: Millésime, uniquement pour le rapport (aucun impact sur les expectations).
        df: Table Silver déjà nettoyée (cf. `gravia.silver.clean_table`).

    Returns:
        Le résultat, indépendant de l'objet interne Great Expectations.
    """
    context = gx.get_context(mode="ephemeral")
    data_source = context.data_sources.add_pandas(f"{table}_{year}_source")
    asset = data_source.add_dataframe_asset(name="asset")
    batch_definition = asset.add_batch_definition_whole_dataframe("batch")

    suite = context.suites.add(gx.ExpectationSuite(name=f"silver_{table}"))
    for expectation in build_expectations(table, df.columns):
        suite.add_expectation(expectation)

    validation_definition = context.validation_definitions.add(
        gx.ValidationDefinition(
            name=f"{table}_{year}_validation", data=batch_definition, suite=suite
        )
    )

    result = validation_definition.run(batch_parameters={"dataframe": df.to_pandas()})

    failed = tuple(r.expectation_config.type for r in result.results if not r.success)
    return TableValidationResult(
        table=table, year=year, success=result.success, failed_expectations=failed
    )


def validate_silver(years: tuple[int, ...], settings: Settings) -> list[TableValidationResult]:
    """Valide toutes les tables Silver des millésimes demandés.

    Args:
        years: Millésimes à valider (Silver doit déjà avoir été produit localement, cf.
            `gravia.silver.clean`).
        settings: Configuration (chemins Silver).

    Returns:
        Un résultat par couple (table, millésime).

    Raises:
        SilverQualityError: Si au moins une table échoue sa suite d'expectations. Le message
            liste chaque échec pour un diagnostic direct, sans avoir à rejouer la validation.
    """
    results: list[TableValidationResult] = []
    for year in years:
        for table in CLEANERS:
            df = pl.read_parquet(silver_path(table, year, settings))
            result = validate_table(table, year, df)
            results.append(result)
            verdict = "OK" if result.success else "ÉCHEC"
            print(f"  {table:16s} {year}  [{verdict}]")
            for expectation in result.failed_expectations:
                print(f"    - {expectation} en échec")

    failures = [r for r in results if not r.success]
    if failures:
        detail = "; ".join(
            f"{r.table} {r.year} ({', '.join(r.failed_expectations)})" for r in failures
        )
        raise SilverQualityError(
            f"{len(failures)} table(s) Silver en échec de validation : {detail}"
        )

    return results


def main() -> None:
    """Point d'entrée en ligne de commande."""
    parser = argparse.ArgumentParser(
        description="Contrôle qualité Great Expectations de la couche Silver."
    )
    parser.add_argument(
        "--years",
        type=int,
        nargs="+",
        default=list(DEFAULT_YEARS),
        help=f"Millésimes à valider (défaut : {' '.join(map(str, DEFAULT_YEARS))}).",
    )
    args = parser.parse_args()

    results = validate_silver(tuple(args.years), get_settings())
    print(f"\nQualité Silver : {len(results)} table(s) validée(s), toutes conformes.")


if __name__ == "__main__":
    main()
