"""Test d'intégration de ml/features/gold_features.py contre un PostgreSQL réel.

Réutilise le même millésime factice que tests/integration/test_gold_postgres.py (1900) et purge
ses propres données en sortie — cf. ce module pour le détail de l'isolation vis-à-vis de la base
dev partagée.
"""

from pathlib import Path

import polars as pl
import pytest
import sqlalchemy as sa

from gravia.config import Settings, StoragePaths, get_settings
from gravia.gold import create_schema, load_year
from gravia.silver import silver_path
from ml.features.gold_features import YEAR_COLUMN, load_gold_features

TEST_MILLESIME = 1901  # distinct de test_gold_postgres.py pour rester isolé si lancés ensemble
TEST_DEPARTEMENT = "ZY"


def _settings(tmp_path: Path) -> Settings:
    base = get_settings()
    return Settings(
        paths=StoragePaths(
            raw=tmp_path / "raw", bronze=tmp_path / "bronze", silver=tmp_path / "silver"
        ),
        object_store=base.object_store,
        database=base.database,
    )


def _postgres_reachable(settings: Settings) -> bool:
    try:
        sa.create_engine(settings.database.url).connect().close()
    except Exception:
        return False
    return True


def test_load_gold_features_reads_joined_star_schema(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    if not _postgres_reachable(settings):
        pytest.skip("PostgreSQL non joignable (stack dev non démarrée, cf. `make dev`)")

    caract = pl.DataFrame(
        {
            "Num_Acc": ["FEAT0001"],
            "an": [TEST_MILLESIME],
            "mois": [1],
            "jour": [1],
            "hrmn": ["08:30"],
            "dep": [TEST_DEPARTEMENT],
            "agg": [2],
            "int": [1],
            "lum": [1],
            "atm": [1],
            "col": [6],
        },
        schema_overrides={
            "an": pl.Int32,
            "mois": pl.Int8,
            "jour": pl.Int8,
            "agg": pl.Int8,
            "int": pl.Int8,
            "lum": pl.Int8,
            "atm": pl.Int8,
            "col": pl.Int8,
        },
    )
    lieux = pl.DataFrame(
        {
            "Num_Acc": ["FEAT0001"],
            "catr": [4],
            "circ": [2],
            "nbv": [2],
            "vosp": [0],
            "prof": [1],
            "plan": [1],
            "vma": [30],
            "surf": [1],
            "infra": [0],
            "situ": [1],
        },
        schema_overrides={
            "catr": pl.Int8,
            "circ": pl.Int8,
            "nbv": pl.Int16,
            "vosp": pl.Int8,
            "prof": pl.Int8,
            "plan": pl.Int8,
            "vma": pl.Int16,
            "surf": pl.Int8,
            "infra": pl.Int8,
            "situ": pl.Int8,
        },
    )
    vehicules = pl.DataFrame(
        {"Num_Acc": ["FEAT0001"], "num_veh": ["A01"], "catv": [7]},
        schema_overrides={"catv": pl.Int8},
    )
    usagers = pl.DataFrame(
        {"Num_Acc": ["FEAT0001"], "grav": [3], "catu": [1]},
        schema_overrides={"grav": pl.Int8, "catu": pl.Int8},
    )
    for table, df in (
        ("caracteristiques", caract),
        ("lieux", lieux),
        ("vehicules", vehicules),
        ("usagers", usagers),
    ):
        path = silver_path(table, TEST_MILLESIME, settings)
        path.parent.mkdir(parents=True, exist_ok=True)
        df.write_parquet(path)

    engine = sa.create_engine(settings.database.url)
    create_schema(engine)

    try:
        load_year(engine, TEST_MILLESIME, settings)

        features = load_gold_features(engine).filter(pl.col("accident_id") == "FEAT0001")

        assert features.height == 1
        row = features.row(0, named=True)
        assert row["is_grave"] is True
        assert row[YEAR_COLUMN] == TEST_MILLESIME
        assert row["departement"] == TEST_DEPARTEMENT
        assert row["categorie_route"] == 4
        assert row["type_collision"] == "Autre collision"
    finally:
        with engine.begin() as conn:
            conn.execute(
                sa.text("DELETE FROM gold_fact_accident WHERE _millesime = :year"),
                {"year": TEST_MILLESIME},
            )
            conn.execute(
                sa.text("DELETE FROM gold_dim_lieu WHERE departement = :dep"),
                {"dep": TEST_DEPARTEMENT},
            )
