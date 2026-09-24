"""Tests unitaires de la couche Silver (src/gravia/silver.py)."""

from pathlib import Path

import polars as pl
import pytest

from gravia.bronze import bronze_path, ingest
from gravia.config import Settings, StoragePaths
from gravia.silver import (
    AGE_BUCKET_UNKNOWN,
    add_age_bucket,
    cast_columns,
    clean,
    clean_caracteristiques,
    clean_lieux,
    clean_table,
    silver_path,
    upload,
)


def test_cast_columns_strips_and_parses_leading_space_sentinel() -> None:
    """`' -1'` (espace de tête) doit devenir l'entier -1, pas `null` (cf. CLAUDE.md)."""
    df = pl.DataFrame({"grav": [" -1", "2", "abc", ""]})

    result = cast_columns(df, [("grav", pl.Int8)])

    assert result["grav"].to_list() == [-1, 2, None, None]


def test_clean_caracteristiques_drops_precise_geolocation() -> None:
    df = pl.DataFrame(
        {
            "Num_Acc": ["1"],
            "jour": ["05"],
            "mois": ["3"],
            "an": ["2023"],
            "lum": [" -1"],
            "agg": ["2"],
            "int": ["1"],
            "atm": ["1"],
            "col": ["6"],
            "lat": ["48.85"],
            "long": ["2.35"],
            "adr": ["56bis Avenue Raspail"],
            "dep": ["75"],
            "com": ["75101"],
        }
    )

    result = clean_caracteristiques(df)

    assert "lat" not in result.columns
    assert "long" not in result.columns
    assert "adr" not in result.columns
    assert "com" not in result.columns
    assert "dep" in result.columns
    assert result["lum"][0] == -1


def test_clean_lieux_dedups_and_normalizes_decimal_comma() -> None:
    """Un accident a plusieurs lignes dans lieux (cf. CLAUDE.md) ; certains millésimes
    utilisent la virgule décimale française pour les largeurs."""
    df = pl.DataFrame(
        {
            "Num_Acc": ["1", "1", "2"],
            "catr": ["3", "3", "1"],
            "voie": ["A63", "A63", "RN10"],
            "v1": ["63", "63", "10"],
            "v2": ["", "", ""],
            "circ": ["2", "2", "1"],
            "nbv": ["2", "2", "4"],
            "vosp": ["0", "0", "0"],
            "prof": ["1", "1", "1"],
            "pr": ["10", "10", "5"],
            "pr1": ["100", "100", "50"],
            "plan": ["1", "1", "1"],
            "surf": ["1", "1", "1"],
            "infra": ["0", "0", "0"],
            "situ": ["1", "1", "1"],
            "vma": ["50", "50", "90"],
            "lartpc": ["1,50", "1,50", " -1"],
            "larrout": ["6,00", "6,00", "7.5"],
        }
    )

    result = clean_lieux(df)

    assert result.height == 2
    assert result.filter(pl.col("Num_Acc") == "1")["lartpc"].item() == 1.5
    assert result.filter(pl.col("Num_Acc") == "2")["lartpc"].item() == -1.0
    assert result.filter(pl.col("Num_Acc") == "2")["larrout"].item() == 7.5


def test_clean_lieux_maps_excel_artifacts_to_sentinel() -> None:
    """`nbv` contient parfois des artefacts Excel non résolus dans le CSV source (constaté sur
    2022/2023 réels : "#ERREUR", "#VALEURMULTI") ; ils doivent devenir -1 (non renseigné), pas
    un NULL silencieux indiscernable d'une valeur réellement absente."""
    df = pl.DataFrame(
        {
            "Num_Acc": ["1", "2", "3"],
            "catr": ["3", "1", "1"],
            "voie": ["A63", "A63", "RN10"],
            "v1": ["63", "63", "10"],
            "v2": ["", "", ""],
            "circ": ["2", "1", "1"],
            "nbv": ["#ERREUR", " #VALEURMULTI", "2"],
            "pr": ["10", "10", "5"],
            "pr1": ["100", "100", "50"],
            "vosp": ["0", "0", "0"],
            "prof": ["1", "1", "1"],
            "plan": ["1", "1", "1"],
            "surf": ["1", "1", "1"],
            "infra": ["0", "0", "0"],
            "situ": ["1", "1", "1"],
            "vma": ["50", "50", "50"],
            "lartpc": ["1,50", "1,50", "1,50"],
            "larrout": ["6,00", "6,00", "6,00"],
        }
    )

    result = clean_lieux(df)

    assert result.filter(pl.col("Num_Acc") == "1")["nbv"].item() == -1
    assert result.filter(pl.col("Num_Acc") == "2")["nbv"].item() == -1
    assert result.filter(pl.col("Num_Acc") == "3")["nbv"].item() == 2
    assert result["nbv"].null_count() == 0
    for col in ("voie", "v1", "v2", "pr", "pr1"):
        assert col not in result.columns


def test_add_age_bucket_covers_all_boundaries() -> None:
    df = pl.DataFrame(
        {
            # ages en 2023 : 13, 23, 28, 43, 58, 73
            "an_nais": ["2010", "2000", "1995", "1980", "1965", "1950"],
            "_millesime": [2023] * 6,
        }
    )

    result = add_age_bucket(df)

    assert result["tranche_age"].to_list() == ["0-17", "18-24", "25-34", "35-49", "50-64", "65+"]
    assert "an_nais" not in result.columns


def test_add_age_bucket_flags_missing_and_implausible_as_unknown() -> None:
    df = pl.DataFrame(
        {
            "an_nais": [None, "2030", "1800"],  # absent, âge négatif, saisie aberrante
            "_millesime": [2023, 2023, 2023],
        }
    )

    result = add_age_bucket(df)

    assert result["tranche_age"].to_list() == [AGE_BUCKET_UNKNOWN] * 3


def test_clean_table_drops_blank_export_artifact_row(tmp_path) -> None:
    """Chaque fichier BAAC source contient une ligne finale entièrement vide (cf. CLAUDE.md) ;
    elle doit être écartée en Silver, pas laissée traverser le pipeline."""
    settings = Settings(
        paths=StoragePaths(
            raw=tmp_path / "raw", bronze=tmp_path / "bronze", silver=tmp_path / "silver"
        )
    )
    bronze_df = pl.DataFrame(
        {
            "Num_Acc": ["202300000001", None],
            "jour": ["05", None],
            "mois": ["3", None],
            "an": ["2023", None],
            "lum": [" -1", None],
            "agg": ["2", None],
            "int": ["1", None],
            "atm": ["1", None],
            "col": ["6", None],
            "lat": ["48.85", None],
            "long": ["2.35", None],
            "adr": ["56bis Avenue Raspail", None],
            "dep": ["75", None],
            "com": ["75101", None],
        }
    )
    source = bronze_path("caracteristiques", 2023, settings)
    source.parent.mkdir(parents=True, exist_ok=True)
    bronze_df.write_parquet(source)

    clean_table("caracteristiques", 2023, settings)

    result = pl.read_parquet(silver_path("caracteristiques", 2023, settings))
    assert result.height == 1
    assert result["Num_Acc"][0] == "202300000001"
    assert result["lum"][0] == -1


def _settings(tmp_path: Path) -> Settings:
    return Settings(
        paths=StoragePaths(
            raw=tmp_path / "raw", bronze=tmp_path / "bronze", silver=tmp_path / "silver"
        )
    )


def test_clean_runs_local_pipeline_from_real_bronze_output(tmp_path: Path) -> None:
    """Bronze -> Silver de bout en bout sur des CSV minimaux, sans mock."""
    settings = _settings(tmp_path)
    baac_dir = settings.paths.baac
    baac_dir.mkdir(parents=True, exist_ok=True)
    (baac_dir / "caracteristiques-2023.csv").write_text(
        "Num_Acc;jour;mois;an;lum;agg;int;atm;col;lat;long;hrmn;dep;com;adr\n"
        "1;05;3;2023;1;2;1;1;6;48.85;2.35;08:30;75;75101;\n",
        encoding="utf-8",
    )
    (baac_dir / "lieux-2023.csv").write_text(
        "Num_Acc;catr;voie;v1;v2;circ;nbv;vosp;prof;pr;pr1;plan;lartpc;larrout;surf;infra;situ;vma\n"
        "1;4;;;;2;2;0;1;10;100;1;1,50;6,00;1;0;1;30\n",
        encoding="utf-8",
    )
    (baac_dir / "vehicules-2023.csv").write_text(
        "Num_Acc;id_vehicule;num_veh;senc;catv;obs;obsm;choc;manv;motor;occutc\n"
        "1;1;A01;1;7;0;0;1;1;1;0\n",
        encoding="utf-8",
    )
    (baac_dir / "usagers-2023.csv").write_text(
        "Num_Acc;id_vehicule;num_veh;place;catu;grav;sexe;an_nais;trajet;secu1;secu2;secu3;"
        "locp;actp;etatp\n"
        "1;1;A01;1;1;1;1;1990;5;1;0;0;0;0;0\n",
        encoding="utf-8",
    )
    ingest((2023,), settings, do_upload=False)

    produced = clean((2023,), settings, do_upload=False)

    assert len(produced) == 4
    result = pl.read_parquet(silver_path("caracteristiques", 2023, settings))
    assert result.height == 1
    assert "lat" not in result.columns
    assert "com" not in result.columns
    usagers = pl.read_parquet(silver_path("usagers", 2023, settings))
    assert usagers["tranche_age"][0] == "25-34"  # 2023 - 1990 = 33 ans


def test_upload_calls_s3_client_for_each_object(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    settings = _settings(tmp_path)
    local_file = tmp_path / "part-0.parquet"
    pl.DataFrame({"a": [1]}).write_parquet(local_file)
    calls: list[tuple[str, str, str]] = []

    class FakeS3Client:
        def upload_file(self, filename: str, bucket: str, key: str) -> None:
            calls.append((filename, bucket, key))

    monkeypatch.setattr("boto3.client", lambda *args, **kwargs: FakeS3Client())

    key = "silver/baac/caracteristiques/millesime=2023/part-0.parquet"
    upload({key: local_file}, settings)

    assert calls == [(str(local_file), settings.object_store.bucket, key)]
