"""Construction de la matrice de features ML à partir du schéma en étoile Gold.

Reprend le protocole déjà validé dans `notebooks/eda_baseline_baac.ipynb` (baseline, recall 0,808 /
F1 macro 0,708) et `notebooks/eval_enrichissement_vs_seuil.ipynb` (meilleure configuration testée,
enrichissement véhicule/usager), avec les noms de colonnes du schéma Gold plutôt que les codes
BAAC bruts lus directement en CSV par ces notebooks.

Trois groupes de colonnes, pour ne pas mélanger ce qui est prouvé et ce qui ne l'est pas :
    - `BASELINE_*` : exactement les features du baseline d'origine (`CARACT_FEATURES` +
      `LIEUX_FEATURES` + `NUMERIC_FEATURES` du notebook), renommées comme Gold les expose.
      `agglomeration` (`agg` dans le notebook, code catégoriel 1/2) est reclassée en colonne
      booléenne : Gold la stocke déjà comme un booléen propre, pas la peine de revenir à un code.
    - `ENRICHED_FLAG_COLUMNS` : les 4 flags véhicule/usager de la meilleure configuration testée
      (`flag_2roues_motorise`/`flag_poids_lourd`/`flag_velo_edp`/`flag_pieton`).
    - `UNVALIDATED_*` : colonnes disponibles dans Gold mais jamais testées dans aucun notebook
      (`weekend`, `jour_ferie`, `nb_usagers`). Exposées séparément plutôt que mélangées aux
      groupes ci-dessus : les utiliser comme features est une expérimentation à part entière, à
      comparer au protocole existant avant d'être adoptée (cf. CLAUDE.md, référence baseline).

`type_collision` est un changement de représentation, pas une nouvelle feature : c'est le même
signal que le `col` du notebook (code catégoriel), mais décodé en libellé texte par Gold — un
type de catégorie différent, LightGBM traite les deux identiquement.

Split train/valid/test : reprend exactement le protocole cité dans CLAUDE.md (train 2019-2021 /
validation 2022 / test 2023, holdout jamais vu même pour le calibrage de seuil). Codé en dur
plutôt que paramétré : dévier de ce split casserait silencieusement la comparabilité avec les
chiffres de référence déjà publiés.
"""

from __future__ import annotations

import polars as pl
import sqlalchemy as sa

#: Reprises telles quelles de CARACT_FEATURES + LIEUX_FEATURES (notebooks/eda_baseline_baac.ipynb),
#: renommées comme le schéma Gold les expose. `agglomeration` n'y figure pas : cf. docstring.
BASELINE_CATEGORICAL_COLUMNS: tuple[str, ...] = (
    "luminosite",
    "departement",
    "intersection",
    "meteo",
    "type_collision",
    "categorie_route",
    "regime_circulation",
    "voie_reservee",
    "profil_route",
    "trace_plan",
    "etat_surface",
    "infrastructure",
    "situation",
)

#: Reprises telles quelles de NUMERIC_FEATURES (notebooks/eda_baseline_baac.ipynb).
BASELINE_NUMERIC_COLUMNS: tuple[str, ...] = (
    "nb_voies",
    "vitesse_max",
    "heure",
    "nb_vehicules",
    "mois",
    "jour_semaine",
)

#: `agg` du baseline d'origine (code catégoriel 1/2) ; Gold le stocke en booléen propre.
BASELINE_BOOLEAN_COLUMNS: tuple[str, ...] = ("agglomeration",)

#: Meilleure configuration testée (notebooks/eval_enrichissement_vs_seuil.ipynb) : recall Paris
#: 0,812, F1 macro national 0,609, combinée à un seuil par département au moment du scoring
#: (hors périmètre de ce module — c'est une décision de `ml/training`, pas une feature).
ENRICHED_FLAG_COLUMNS: tuple[str, ...] = (
    "flag_2roues_motorise",
    "flag_poids_lourd",
    "flag_velo_edp",
    "flag_pieton",
)

#: Disponibles dans Gold, jamais testées : cf. docstring module.
UNVALIDATED_BOOLEAN_COLUMNS: tuple[str, ...] = ("weekend", "jour_ferie")
UNVALIDATED_NUMERIC_COLUMNS: tuple[str, ...] = ("nb_usagers",)

LABEL_COLUMN = "is_grave"
YEAR_COLUMN = "annee"

#: Split temporel du protocole déjà validé (cf. CLAUDE.md, référence baseline).
TRAIN_YEARS_BEFORE = 2022  # train : annee < 2022 (2019-2021)
VALID_YEAR = 2022
TEST_YEAR = 2023

_GOLD_QUERY = """
    SELECT
        f.accident_id,
        f._millesime AS annee,
        f.is_grave,
        f.nb_vehicules,
        f.nb_usagers,
        f.flag_2roues_motorise,
        f.flag_poids_lourd,
        f.flag_velo_edp,
        f.flag_pieton,
        d.heure,
        d.jour_semaine,
        d.weekend,
        d.mois,
        d.jour_ferie,
        l.departement,
        l.agglomeration,
        l.intersection,
        l.categorie_route,
        l.regime_circulation,
        l.nb_voies,
        l.voie_reservee,
        l.profil_route,
        l.trace_plan,
        l.vitesse_max,
        l.infrastructure,
        l.situation,
        c.luminosite,
        c.meteo,
        c.etat_surface,
        col.type_collision
    FROM gold_fact_accident f
    JOIN gold_dim_date d ON f.date_key = d.date_key
    JOIN gold_dim_lieu l ON f.lieu_key = l.lieu_key
    JOIN gold_dim_conditions c ON f.conditions_key = c.conditions_key
    JOIN gold_dim_collision col ON f.collision_key = col.collision_key
"""


def load_gold_features(engine: sa.Engine) -> pl.DataFrame:
    """Charge le schéma en étoile Gold, déjà joint, en un DataFrame plat.

    Args:
        engine: Connexion SQLAlchemy vers PostgreSQL.

    Returns:
        Une ligne par accident, colonnes brutes (pas encore typées pour un modèle — cf.
        `prepare_features`).
    """
    with engine.connect() as conn:
        return pl.read_database(_GOLD_QUERY, connection=conn)


def prepare_features(df: pl.DataFrame) -> pl.DataFrame:
    """Type les colonnes pour un modèle : catégorielles en `Categorical`, numériques en `Float64`.

    Args:
        df: Sortie de `load_gold_features`.

    Returns:
        La même table, catégorielles et numériques castées. Les colonnes booléennes ne sont pas
        retouchées : `Categorical`/`Float64` n'apportent rien à un booléen déjà propre (aucune
        valeur manquante dans Gold, cf. `data/models/gold_schema.sql`).

    Note:
        Les colonnes catégorielles numériques (codes BAAC en `Int8`/`Int16`) passent par `Utf8`
        avant `Categorical` : Polars caste un entier directement en `Categorical` en traitant sa
        *valeur* comme un code de catégorie interne (qui doit être positif), pas comme un
        libellé — un `-1` (sentinelle « non renseigné », omniprésente dans ces colonnes, cf.
        CLAUDE.md) fait échouer le cast direct. Passer par `Utf8` en fait un vrai libellé texte.
    """
    return df.with_columns(
        [pl.col(c).cast(pl.Utf8).cast(pl.Categorical) for c in BASELINE_CATEGORICAL_COLUMNS]
    ).with_columns(
        [
            pl.col(c).cast(pl.Float64)
            for c in (*BASELINE_NUMERIC_COLUMNS, *UNVALIDATED_NUMERIC_COLUMNS)
        ]
    )


def split_train_valid_test(
    df: pl.DataFrame,
) -> tuple[pl.DataFrame, pl.DataFrame, pl.DataFrame]:
    """Split temporel anti-leakage : train 2019-2021 / validation 2022 / test 2023.

    Le test (2023) est un holdout jamais vu, y compris pour calibrer un seuil de décision — le
    calibrer sur le test serait de la fuite de méthodologie (cf. notebooks/eda_baseline_baac.ipynb).

    Args:
        df: Sortie de `prepare_features` (ou `load_gold_features`, le split ne dépend pas du
            typage).

    Returns:
        `(train, valid, test)`.
    """
    train = df.filter(pl.col(YEAR_COLUMN) < TRAIN_YEARS_BEFORE)
    valid = df.filter(pl.col(YEAR_COLUMN) == VALID_YEAR)
    test = df.filter(pl.col(YEAR_COLUMN) == TEST_YEAR)
    return train, valid, test


def main() -> None:
    """Point d'entrée en ligne de commande : charge, type, split et résume (vérification rapide)."""
    import sys

    from gravia.config import get_settings

    sys.stdout.reconfigure(encoding="utf-8")

    settings = get_settings()
    engine = sa.create_engine(settings.database.url)

    print("Chargement depuis Gold...")
    df = load_gold_features(engine)
    df = prepare_features(df)
    train, valid, test = split_train_valid_test(df)

    print(f"\nTotal      : {df.height} accidents ({df[LABEL_COLUMN].mean():.1%} graves)")
    print(f"Train      : {train.height} accidents ({train[LABEL_COLUMN].mean():.1%} graves)")
    print(f"Validation : {valid.height} accidents ({valid[LABEL_COLUMN].mean():.1%} graves)")
    print(f"Test       : {test.height} accidents ({test[LABEL_COLUMN].mean():.1%} graves)")


if __name__ == "__main__":
    main()
