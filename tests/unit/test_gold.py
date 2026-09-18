"""Tests unitaires de la couche Gold (src/gravia/gold.py)."""

from datetime import date
from pathlib import Path

import polars as pl
import pytest

from gravia.config import Settings, StoragePaths
from gravia.gold import (
    MissingUsagersError,
    aggregate_usagers,
    aggregate_vehicules,
    build_fact_frame,
    french_public_holidays,
)
from gravia.silver import silver_path


def _write_silver_fixture(
    settings: Settings,
    year: int,
    caract: pl.DataFrame,
    lieux: pl.DataFrame,
    vehicules: pl.DataFrame,
    usagers: pl.DataFrame,
) -> None:
    for table, df in (
        ("caracteristiques", caract),
        ("lieux", lieux),
        ("vehicules", vehicules),
        ("usagers", usagers),
    ):
        path = silver_path(table, year, settings)
        path.parent.mkdir(parents=True, exist_ok=True)
        df.write_parquet(path)


def _settings(tmp_path: Path) -> Settings:
    return Settings(
        paths=StoragePaths(
            raw=tmp_path / "raw", bronze=tmp_path / "bronze", silver=tmp_path / "silver"
        )
    )


def test_french_public_holidays_2023_fixed_and_movable_dates() -> None:
    """Vérifié contre les dates de Pâques 2023 connues (dimanche 9 avril)."""
    holidays = french_public_holidays(2023)

    assert date(2023, 1, 1) in holidays  # Jour de l'an
    assert date(2023, 7, 14) in holidays  # Fête nationale
    assert date(2023, 12, 25) in holidays  # Noël
    assert date(2023, 4, 10) in holidays  # Lundi de Pâques (Pâques + 1)
    assert date(2023, 5, 18) in holidays  # Ascension (Pâques + 39)
    assert date(2023, 5, 29) in holidays  # Lundi de Pentecôte (Pâques + 50)
    assert len(holidays) == 11
    assert date(2023, 4, 9) not in holidays  # Pâques (dimanche) n'est pas férié en soi


def test_aggregate_vehicules_flags_and_counts() -> None:
    df = pl.DataFrame(
        {
            "Num_Acc": ["1", "1", "2"],
            "num_veh": ["A01", "B01", "A01"],
            "catv": [7, 33, 13],  # VL + moto sur 1 ; poids lourd sur 2
        },
        schema_overrides={"catv": pl.Int8},
    )

    result = aggregate_vehicules(df).sort("Num_Acc")

    acc1 = result.filter(pl.col("Num_Acc") == "1")
    assert acc1["nb_vehicules"].item() == 2
    assert acc1["flag_2roues_motorise"].item() is True
    assert acc1["flag_poids_lourd"].item() is False

    acc2 = result.filter(pl.col("Num_Acc") == "2")
    assert acc2["nb_vehicules"].item() == 1
    assert acc2["flag_poids_lourd"].item() is True
    assert acc2["flag_2roues_motorise"].item() is False


def test_aggregate_usagers_label_and_pedestrian_flag() -> None:
    df = pl.DataFrame(
        {
            "Num_Acc": ["1", "1", "2"],
            "grav": [1, 3, 4],  # accident 1 grave (hospitalisé), accident 2 non grave
            "catu": [1, 3, 1],  # un piéton sur l'accident 1
        },
        schema_overrides={"grav": pl.Int8, "catu": pl.Int8},
    )

    result = aggregate_usagers(df).sort("Num_Acc")

    acc1 = result.filter(pl.col("Num_Acc") == "1")
    assert acc1["is_grave"].item() is True
    assert acc1["flag_pieton"].item() is True
    assert acc1["nb_usagers"].item() == 2

    acc2 = result.filter(pl.col("Num_Acc") == "2")
    assert acc2["is_grave"].item() is False
    assert acc2["flag_pieton"].item() is False


def test_aggregate_usagers_all_null_grav_yields_null_not_false() -> None:
    """`.any()` sur un groupe Polars entièrement null renvoie `False`, pas `null` (constaté en
    testant) : un accident dont tous les usagers ont un `grav` illisible ne doit pas être
    silencieusement classé "non grave" — `is_grave` doit rester `null` pour déclencher
    `MissingUsagersError` dans `build_fact_frame`, comme un accident sans usager du tout."""
    df = pl.DataFrame(
        {
            "Num_Acc": ["1", "1", "2"],
            "grav": [None, None, 2],
            "catu": [1, 1, 1],
        },
        schema_overrides={"grav": pl.Int8, "catu": pl.Int8},
    )

    result = aggregate_usagers(df).sort("Num_Acc")

    assert result.filter(pl.col("Num_Acc") == "1")["is_grave"].item() is None
    assert result.filter(pl.col("Num_Acc") == "2")["is_grave"].item() is True


def test_build_fact_frame_joins_tables_and_derives_dimensions(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    caract = pl.DataFrame(
        {
            "Num_Acc": ["1", "2"],
            "an": [2023, 2023],
            "mois": [1, 7],
            "jour": [1, 14],
            "hrmn": ["08:30", "23:57"],
            "dep": ["75", "2A"],
            "agg": [2, 1],
            "int": [1, -1],
            "lum": [1, 3],
            "atm": [1, -1],
            "col": [6, 99],  # 99 : code hors nomenclature ONISR
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
            "Num_Acc": ["1", "2"],
            "catr": [4, 1],
            "circ": [2, -1],
            "nbv": [2, 4],
            "vosp": [0, -1],
            "prof": [1, 1],
            "plan": [1, 1],
            "vma": [30, 130],
            "surf": [1, 2],
            "infra": [0, -1],
            "situ": [1, 1],
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
        {"Num_Acc": ["1", "1", "2"], "num_veh": ["A01", "B01", "A01"], "catv": [7, 33, 7]},
        schema_overrides={"catv": pl.Int8},
    )
    usagers = pl.DataFrame(
        {"Num_Acc": ["1", "1", "2"], "grav": [1, 3, 4], "catu": [1, 1, 1]},
        schema_overrides={"grav": pl.Int8, "catu": pl.Int8},
    )
    _write_silver_fixture(settings, 2023, caract, lieux, vehicules, usagers)

    frame = build_fact_frame(2023, settings)

    assert frame.height == 2

    acc1 = frame.filter(pl.col("accident_id") == "1")
    assert acc1["is_grave"].item() is True  # un usager hospitalisé (grav=3)
    assert acc1["flag_2roues_motorise"].item() is True
    assert acc1["nb_vehicules"].item() == 2
    assert acc1["departement"].item() == "75"
    assert acc1["jour_ferie"].item() is True  # 1er janvier
    assert acc1["weekend"].item() is True  # 2023-01-01 est un dimanche
    assert acc1["heure"].item() == 8
    assert acc1["intersection"].item() == 1
    assert acc1["regime_circulation"].item() == 2
    assert acc1["nb_voies"].item() == 2

    acc2 = frame.filter(pl.col("accident_id") == "2")
    assert acc2["is_grave"].item() is False
    assert acc2["departement"].item() == "2A"  # code Corse non numérique
    assert acc2["type_collision"].item() == "Non renseigné"  # code 99 hors nomenclature
    assert acc2["heure"].item() == 23
    assert acc2["intersection"].item() == -1  # non renseigné
    assert acc2["voie_reservee"].item() == -1


def test_build_fact_frame_raises_when_accident_has_no_usager(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    caract = pl.DataFrame(
        {
            "Num_Acc": ["1"],
            "an": [2023],
            "mois": [1],
            "jour": [1],
            "hrmn": ["08:30"],
            "dep": ["75"],
            "agg": [2],
            "lum": [1],
            "atm": [1],
            "col": [6],
        },
        schema_overrides={
            "an": pl.Int32,
            "mois": pl.Int8,
            "jour": pl.Int8,
            "agg": pl.Int8,
            "lum": pl.Int8,
            "atm": pl.Int8,
            "col": pl.Int8,
        },
    )
    lieux = pl.DataFrame(
        {"Num_Acc": ["1"], "catr": [4], "vma": [30], "surf": [1]},
        schema_overrides={"catr": pl.Int8, "vma": pl.Int16, "surf": pl.Int8},
    )
    vehicules = pl.DataFrame(schema={"Num_Acc": pl.Utf8, "num_veh": pl.Utf8, "catv": pl.Int8})
    usagers = pl.DataFrame(schema={"Num_Acc": pl.Utf8, "grav": pl.Int8, "catu": pl.Int8})
    _write_silver_fixture(settings, 2023, caract, lieux, vehicules, usagers)

    with pytest.raises(MissingUsagersError):
        build_fact_frame(2023, settings)
